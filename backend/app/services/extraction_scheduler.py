"""Foreground-priority scheduling for background memory extraction.

Why this exists (measured 2026-10-25 on the reference machine):

- Ollama serializes generation requests. While a background extraction call
  was in flight, a new foreground chat request waited behind it: time to
  first token 49.3s (behind a ~50s extraction) versus 0.2s when idle.
- Cancelling the in-flight extraction frees the model almost immediately
  (first token 0.24s after the cancel) — but only when the extraction call is
  issued through the streaming HTTP transport, which closes the connection on
  cancellation (see `OllamaClient.chat_json`).
- Extraction persists nothing until the model has answered, so a cancelled
  run loses no work: the conversation stays queued and runs again after the
  turn. Nothing is ever dropped silently.

Design constraints:

- No threads and no cross-request asyncio primitives: all state is plain
  Python, and every wait happens inside a task the caller owns (a FastAPI
  `BackgroundTasks` drain). This works under uvicorn (one loop for all
  requests) and under TestClient (one loop per request).
- At most one extraction runs at a time, and never while a foreground turn
  is active, so chat and extraction never compete for the model.
- Pending jobs are keyed by (conversation_id, user message seq), i.e. by
  the specific exchange to extract. Scheduling the same exchange twice
  coalesces into one run; a job preempted mid-run keeps its own exchange,
  so a newer message never causes the interrupted exchange to be skipped.
- Failures keep the previous single-shot policy: a job that raises is logged
  and dropped (no automatic retries). Only preemption and shutdown re-queue
  a job, because in both cases the job did not get to run.
- Jobs still pending when the process exits are lost — the same documented
  in-process limitation the previous background task had.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


class ExtractionScheduler:
    """Coalescing, foreground-priority queue for memory-extraction jobs."""

    def __init__(
        self,
        job: Callable[[str, int], Awaitable[None]],
        idle_delay_seconds: float = 0.0,
    ) -> None:
        self._job = job
        self._idle_delay = max(0.0, float(idle_delay_seconds))
        # Insertion-ordered set of (conversation_id, user_message_seq) keys:
        # one pending entry per exchange (FIFO).
        self._pending: dict[tuple[str, int], None] = {}
        # Number of concurrently streaming foreground turns (>= 0).
        self._foreground_turns = 0
        # Earliest monotonic time extraction may start after a preemption.
        self._not_before = 0.0
        # The single in-flight job task, if any (owned by the running drain).
        self._inflight: asyncio.Task | None = None
        # True while a drain task is armed or executing (guards concurrency).
        self._drain_busy = False

    # ------------------------------------------------------------- public ---

    def schedule(
        self, background_tasks, conversation_id: str, user_message_seq: int
    ) -> None:
        """Queue extraction for one exchange and arm a drain.

        Always adds a drain task: `BackgroundTasks` runs it after the
        response completes. Extra drains are harmless — the running flag
        makes concurrent drains no-ops while one drain drains everything.
        Re-arming on every schedule is what guarantees a deferred or
        preempted job is picked up again by the next response's drain.
        Scheduling the same exchange again (retry, repeated calls) collapses
        into the existing pending entry.
        """
        self._pending[(conversation_id, user_message_seq)] = None
        background_tasks.add_task(self._drain)

    def foreground_started(self) -> None:
        """A streamed reply is beginning: it takes priority over extraction.

        Any in-flight extraction is cancelled (its HTTP connection closes, so
        Ollama aborts generation and the model is free for the reply) and the
        restart is pushed out by the idle delay, so a burst of consecutive
        messages defers extraction instead of starting and killing it over
        and over. The drain re-queues the cancelled job.
        """
        self._foreground_turns += 1
        if self._inflight is not None:
            self._not_before = time.monotonic() + self._idle_delay
            logger.info(
                "Memory extraction preempted by a new foreground message."
            )
            self._inflight.cancel()

    def foreground_finished(self) -> None:
        """A streamed reply finished (normally, with an error, or closed)."""
        if self._foreground_turns > 0:
            self._foreground_turns -= 1

    @property
    def foreground_active(self) -> bool:
        return self._foreground_turns > 0

    @property
    def pending_jobs(self) -> tuple[tuple[str, int], ...]:
        """Queued (conversation_id, user_message_seq) keys (tests, diagnostics)."""
        return tuple(self._pending)

    # -------------------------------------------------------------- drain ---

    async def _drain(self) -> None:
        """Run queued extraction jobs one at a time when the model is idle.

        Armed as a FastAPI background task on every `schedule()`. The busy
        flag ensures at most one drain mutates the queue; the check that
        empties the queue and the flag reset happen in one synchronous
        stretch, so a concurrent `schedule()` either lands in a queue this
        drain still observes or arms its own fresh drain — never neither.
        """
        if self._drain_busy:
            return
        self._drain_busy = True
        try:
            while True:
                # Foreground priority: never start (or continue into) an
                # extraction while a reply is streaming.
                if self._foreground_turns > 0 or not self._pending:
                    return
                now = time.monotonic()
                if now < self._not_before:
                    # Idle backoff after a preemption; re-check everything
                    # after sleeping (another message may have arrived).
                    await asyncio.sleep(self._not_before - now)
                    continue
                conversation_id, user_message_seq = next(iter(self._pending))
                del self._pending[(conversation_id, user_message_seq)]
                task = asyncio.create_task(self._job(conversation_id, user_message_seq))
                self._inflight = task
                try:
                    await task
                except asyncio.CancelledError:
                    # Distinguishing the two cancellation sources matters:
                    # cancelling the drain also cascades into the awaited
                    # job (Python 3.14), so both end up with task.cancelled()
                    # True. `cancelling()` is non-zero only when *this* drain
                    # task was cancelled directly (shutdown).
                    current = asyncio.current_task()
                    preempted = current is not None and current.cancelling() == 0
                    if preempted:
                        # foreground_started() cancelled the job: the drain
                        # lives on — keep its exchange queued so the
                        # interrupted exchange is still extracted later.
                        self._pending[(conversation_id, user_message_seq)] = None
                        continue
                    # The drain itself is being torn down (shutdown): stop
                    # the job too and leave it queued for the next run.
                    task.cancel()
                    self._pending[(conversation_id, user_message_seq)] = None
                    raise
                except Exception:
                    # run_memory_extraction has its own catch-all; this net
                    # keeps an unexpected job failure from killing draining.
                    # The job already ran (single-shot): not re-queued.
                    logger.warning(
                        "Memory extraction job failed; continuing with the queue.",
                        exc_info=True,
                    )
                finally:
                    if self._inflight is task:
                        self._inflight = None
        finally:
            self._drain_busy = False
