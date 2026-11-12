"""Tests for foreground-priority, coalescing extraction scheduling.

Covers the scheduling contract added on 2026-10-25:

- a new foreground message preempts an in-flight extraction job and the job
  is re-queued (never lost), then runs again once the turn ends — for the
  same exchange it was created for, so the interrupted exchange is never
  silently skipped in favour of a newer one;
- extraction never starts while a foreground turn is streaming;
- pending jobs coalesce per exchange, and at most one job runs at a time;
- job failures keep the existing single-shot policy (logged, not retried)
  and never kill the drain;
- cancelling the drain (shutdown) re-queues the running job and the scheduler
  recovers on the next schedule;
- after a preemption, restart waits for the configured idle delay;
- through the real FastAPI app: two consecutive messages while extraction is
  pending both get extracted after the turn (sequentially, never blocking
  chat), the foreground bracket wraps generation (not extraction), and
  /retry uses the same scheduling.
"""

import asyncio
import time

import pytest
from fastapi import BackgroundTasks

from backend.app.services.extraction_scheduler import ExtractionScheduler

from .conftest import create_conversation, post_message_stream, read_sse_events


class BlockingJob:
    """Job fake: records (conversation, seq) calls; the first `block_first`
    calls block until released, and record their own cancellation."""

    def __init__(self, block_first: int = 1) -> None:
        self.calls: list[tuple[str, int]] = []
        self.cancelled: list[tuple[str, int]] = []
        self.block_first = block_first
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, conversation_id: str, user_message_seq: int) -> None:
        key = (conversation_id, user_message_seq)
        self.calls.append(key)
        if len(self.calls) <= self.block_first:
            self.started.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled.append(key)
                raise


async def wait_for(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met before timeout")
        await asyncio.sleep(0.01)


# ----------------------------------------------------- preemption / priority ---

def test_foreground_message_preempts_running_extraction_and_requeues_it():
    job = BlockingJob()
    scheduler = ExtractionScheduler(job=job, idle_delay_seconds=0.0)

    async def scenario():
        background = BackgroundTasks()
        scheduler.schedule(background, "conv1", 1)
        drain = asyncio.create_task(background())
        await asyncio.wait_for(job.started.wait(), timeout=2)
        assert scheduler.pending_jobs == ()

        # A new message arrives while extraction is in flight.
        scheduler.foreground_started()
        await asyncio.wait_for(drain, timeout=2)

        # The in-flight call was aborted and the job stayed queued — with
        # its own exchange key, so it cannot be superseded by a newer one.
        assert job.cancelled == [("conv1", 1)]
        assert job.calls == [("conv1", 1)]
        assert scheduler.pending_jobs == (("conv1", 1),)
        assert scheduler.foreground_active is True

        # The turn ends: a fresh drain (its response's background phase)
        # runs the same job again — not lost, not duplicated.
        scheduler.foreground_finished()
        job.release.set()
        follow_up = BackgroundTasks()
        scheduler.schedule(follow_up, "conv1", 1)
        await asyncio.wait_for(follow_up(), timeout=2)

        assert job.calls == [("conv1", 1), ("conv1", 1)]
        assert scheduler.pending_jobs == ()

    asyncio.run(scenario())


def test_drain_never_starts_extraction_while_foreground_turn_is_active():
    job = BlockingJob(block_first=0)
    scheduler = ExtractionScheduler(job=job, idle_delay_seconds=0.0)

    async def scenario():
        scheduler.foreground_started()
        background = BackgroundTasks()
        scheduler.schedule(background, "conv1", 1)
        await asyncio.wait_for(background(), timeout=2)

        # Deferred, not dropped.
        assert job.calls == []
        assert scheduler.pending_jobs == (("conv1", 1),)

        scheduler.foreground_finished()
        after_turn = BackgroundTasks()
        scheduler.schedule(after_turn, "conv1", 1)
        await asyncio.wait_for(after_turn(), timeout=2)
        assert job.calls == [("conv1", 1)]
        assert scheduler.pending_jobs == ()

    asyncio.run(scenario())


# ---------------------------------------------------------------- coalescing ---

def test_concurrent_drains_run_each_job_exactly_once():
    job = BlockingJob()
    scheduler = ExtractionScheduler(job=job, idle_delay_seconds=0.0)

    async def scenario():
        first = BackgroundTasks()
        second = BackgroundTasks()
        scheduler.schedule(first, "conv1", 1)
        scheduler.schedule(second, "conv1", 1)

        drain_a = asyncio.create_task(first())
        drain_b = asyncio.create_task(second())
        await asyncio.wait_for(job.started.wait(), timeout=2)
        job.release.set()
        await asyncio.wait_for(asyncio.gather(drain_a, drain_b), timeout=2)

        # Two armed drains, one exchange: exactly one run.
        assert job.calls == [("conv1", 1)]
        assert scheduler.pending_jobs == ()

    asyncio.run(scenario())


def test_pending_jobs_for_different_exchanges_all_run_in_order():
    job = BlockingJob(block_first=0)
    scheduler = ExtractionScheduler(job=job, idle_delay_seconds=0.0)

    async def scenario():
        background = BackgroundTasks()
        scheduler.schedule(background, "conv_a", 1)
        scheduler.schedule(background, "conv_b", 1)
        scheduler.schedule(background, "conv_a", 2)
        await asyncio.wait_for(background(), timeout=2)
        assert job.calls == [("conv_a", 1), ("conv_b", 1), ("conv_a", 2)]
        assert scheduler.pending_jobs == ()

    asyncio.run(scenario())


# ------------------------------------------------------ failures / shutdown ---

def test_job_failure_is_not_retried_and_does_not_kill_the_drain():
    calls: list[tuple[str, int]] = []

    async def failing_job(conversation_id: str, user_message_seq: int) -> None:
        calls.append((conversation_id, user_message_seq))
        raise RuntimeError("extraction exploded")

    scheduler = ExtractionScheduler(job=failing_job, idle_delay_seconds=0.0)

    async def scenario():
        background = BackgroundTasks()
        scheduler.schedule(background, "conv1", 1)
        await asyncio.wait_for(background(), timeout=2)  # must not raise

        # Single-shot policy preserved: the failed job is dropped, and the
        # queue still works for the next exchange.
        assert calls == [("conv1", 1)]
        assert scheduler.pending_jobs == ()

        scheduler.schedule(background, "conv2", 1)
        await asyncio.wait_for(background(), timeout=2)
        assert calls == [("conv1", 1), ("conv2", 1)]

    asyncio.run(scenario())


def test_drain_cancellation_requeues_the_running_job_and_recovers():
    job = BlockingJob()
    scheduler = ExtractionScheduler(job=job, idle_delay_seconds=0.0)

    async def scenario():
        background = BackgroundTasks()
        scheduler.schedule(background, "conv1", 1)
        drain = asyncio.create_task(background())
        await asyncio.wait_for(job.started.wait(), timeout=2)

        # Shutdown: the drain task is cancelled mid-job.
        drain.cancel()
        with pytest.raises(asyncio.CancelledError):
            await drain
        await wait_for(lambda: job.cancelled == [("conv1", 1)])

        assert job.calls == [("conv1", 1)]
        assert scheduler.pending_jobs == (("conv1", 1),)

        # A later schedule drains the re-queued job normally.
        job.release.set()
        later = BackgroundTasks()
        scheduler.schedule(later, "conv1", 1)
        await asyncio.wait_for(later(), timeout=2)
        assert job.calls == [("conv1", 1), ("conv1", 1)]
        assert scheduler.pending_jobs == ()

    asyncio.run(scenario())


# ----------------------------------------------------------- idle backoff ---

def test_restart_after_preemption_waits_for_the_idle_delay():
    job = BlockingJob()
    scheduler = ExtractionScheduler(job=job, idle_delay_seconds=0.4)

    async def scenario():
        background = BackgroundTasks()
        scheduler.schedule(background, "conv1", 1)
        drain = asyncio.create_task(background())
        await asyncio.wait_for(job.started.wait(), timeout=2)

        scheduler.foreground_started()  # preemption starts the backoff clock
        await asyncio.wait_for(drain, timeout=2)
        scheduler.foreground_finished()
        job.release.set()

        restart = BackgroundTasks()
        scheduler.schedule(restart, "conv1", 1)
        started_at = time.monotonic()
        await asyncio.wait_for(restart(), timeout=2)
        elapsed = time.monotonic() - started_at

        assert job.calls == [("conv1", 1), ("conv1", 1)]
        assert elapsed >= 0.3, f"restarted after only {elapsed:.2f}s"

    asyncio.run(scenario())


# ----------------------------------------------------- through the real app ---

def test_two_messages_while_extraction_pending_extract_both_exchanges(
    client, app, fake_ollama
):
    """Two consecutive messages while extraction is deferred (pending): both
    exchanges end up extracted, sequentially, after the turn — foreground
    chat never waited for either."""
    scheduler = app.state.extraction_scheduler
    conversation = create_conversation(client)

    # Simulate another foreground turn being active (bracketed exactly as
    # send_message brackets its own stream).
    scheduler.foreground_started()

    events_one = post_message_stream(client, conversation["id"], "I like green tea.")
    events_two = post_message_stream(client, conversation["id"], "I also live in Oslo.")
    seq_one = events_one[0]["message"]["seq"]
    seq_two = events_two[0]["message"]["seq"]

    # Both responses completed without waiting for extraction, and their
    # drains deferred while any foreground turn is active.
    assert fake_ollama.chat_json_calls == []
    assert scheduler.pending_jobs == (
        (conversation["id"], seq_one),
        (conversation["id"], seq_two),
    )

    # The other turn ends; scheduling its (already queued) exchange arms a
    # fresh drain, which runs both exchanges — one at a time, each for its
    # own exchange (nothing skipped, nothing duplicated).
    other_turn = BackgroundTasks()
    scheduler.schedule(other_turn, conversation["id"], seq_one)
    scheduler.foreground_finished()
    asyncio.run(other_turn())

    assert len(fake_ollama.chat_json_calls) == 2
    first_exchange = fake_ollama.chat_json_calls[0]["messages"][1]["content"]
    second_exchange = fake_ollama.chat_json_calls[1]["messages"][1]["content"]
    assert "green tea" in first_exchange
    assert "Oslo" in second_exchange
    assert scheduler.pending_jobs == ()
    assert scheduler.foreground_active is False


def test_foreground_bracket_wraps_generation_but_not_extraction(
    client, app, fake_ollama
):
    """Wiring: send_message brackets generation with foreground_started/
    finished, and extraction only ever runs outside that bracket."""
    scheduler = app.state.extraction_scheduler
    seen: dict[str, bool] = {}

    original_stream = fake_ollama.chat_stream

    async def stream_spy(model, messages, num_ctx):
        seen.setdefault("during_generation", scheduler.foreground_active)
        async for chunk in original_stream(model, messages, num_ctx):
            yield chunk

    original_json = fake_ollama.chat_json

    async def json_spy(model, messages, num_ctx, timeout_seconds=None):
        seen.setdefault("during_extraction", scheduler.foreground_active)
        return await original_json(
            model, messages, num_ctx, timeout_seconds=timeout_seconds
        )

    fake_ollama.chat_stream = stream_spy
    fake_ollama.chat_json = json_spy

    conversation = create_conversation(client)
    events = post_message_stream(client, conversation["id"], "Hello there!")

    assert events[-1]["type"] == "done"
    assert seen == {"during_generation": True, "during_extraction": False}
    assert scheduler.foreground_active is False
    assert scheduler.pending_jobs == ()


def test_retry_endpoint_schedules_extraction_and_balances_bracket(
    client, app, fake_ollama
):
    scheduler = app.state.extraction_scheduler
    conversation = create_conversation(client)

    first = post_message_stream(client, conversation["id"], "First attempt.")
    assert first[-1]["type"] == "done"
    runs_after_first = len(fake_ollama.chat_json_calls)
    assert runs_after_first == 1

    with client.stream(
        "POST", f"/api/conversations/{conversation['id']}/retry"
    ) as response:
        assert response.status_code == 200
        retry_events = read_sse_events(response)

    # The retry re-extracts the same exchange (deduped at validation) and
    # its drain runs after the retry's stream completes.
    assert retry_events[-1]["type"] == "done"
    assert len(fake_ollama.chat_json_calls) == runs_after_first + 1
    assert scheduler.foreground_active is False
    assert scheduler.pending_jobs == ()
