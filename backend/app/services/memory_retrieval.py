"""Keyword memory retrieval (Phase 2, Step 5).

Turns the current user message into a small, deterministic evidence pack of
stored memories. Local only: SQLite FTS5 when this build provides it, a plain
``LIKE`` scan otherwise. No embeddings, no external services, no new
dependencies.

Ranking (documented, deterministic):

- Query terms are the meaningful tokens of the current user message:
  unicode word tokens, casefolded, stopwords removed, tokens shorter than
  three characters dropped, de-duplicated in order of appearance, capped at
  ``MAX_QUERY_TERMS``. Punctuation-only or stopword-only input yields no terms
  and therefore no results (there is no recency-only fallback that would
  invent relevance).
- A term matches a field by exact token equality after possessive/plural
  normalization (``preferences`` also matches ``preference``; ``Mara's``
  tokenizes to ``mara``). Substring matches are never accepted as relevance:
  ``art`` does not match ``start``.
- Field weights: keyword match 3, subject match 3, statement match 2. Each
  term contributes its single best field weight, so ``score`` is the sum over
  matched terms. Direct keyword/subject hits therefore outrank incidental
  statement words.
- Ties are broken by number of matched terms, then most recently updated
  record, then ascending id — a total order, so identical inputs always yield
  identical ordering.
- Candidate selection agrees with the scorer: candidate queries expand the
  same plural variants the scorer accepts (in both directions), then use FTS5
  prefix matches over statement/keywords plus a subject ``LIKE`` scan (the FTS
  index does not index ``subject``); the LIKE fallback covers all three fields.
  Without the subject scan, subject hits would appear only on builds without
  FTS5; without variant expansion, a plural query could never find a singular
  stored statement even though the scorer would accept the match.
- Queries with three or more terms require at least two matched terms
  (``min_matches``); single/double-term queries require one. Results below
  ``settings.memory_retrieval_min_score`` are discarded.
- Follow-up context (Phase 3): when the current message yields no evidence and
  the caller supplies the previous user message **from the same conversation**,
  retrieval retries once with the combined, bounded term list and flags
  ``query_expanded``. This lets "What about her?" reuse the topic that was just
  discussed without changing ordinary queries or crossing conversations.

Lifecycle filtering (mandatory):

- Only ``accepted`` and ``confirmed`` memories are retrievable.
- ``deleted``, ``superseded``, ``conflicted``, and ``orphaned`` are never
  returned (they are excluded in SQL and re-checked in code).
- ``review`` memories are excluded from prompt evidence: the memory
  specification never authorizes automatic use of unreviewed inferences, so
  they stay out of trusted context until a later review step confirms them.

Provenance:

- Each returned record's declared source conversation and source message ids
  are checked against the real ``conversations``/``messages`` rows in one
  batched query (see ``memory_service.resolve_source_references``). A
  message-bearing record is valid only while every declared message exists and
  belongs to the declared conversation; a surviving conversation alone cannot
  rescue a deleted message reference. Legacy conversation-only records remain
  valid while their conversation exists. Invalid records are skipped (reason
  ``invalid_provenance``), and the validated ids — never stale stored ids —
  are the ones placed on the returned record for the prompt. Retrieval never
  writes to the database.
- Malformed stored JSON (keywords/source ids) skips the record
  (``malformed_metadata``) instead of raising.

Duplicates: records whose normalized statement is identical, or whose
statement token sets are near-identical (Jaccard >= 0.9), are collapsed to the
highest-ranked copy.

Failure isolation: database errors are converted into
``RetrievalResult(error="database_error")`` with no memories. Callers (the chat
path) must treat that as "no memory evidence" and keep answering. Only counts,
reason codes, and memory ids are logged — never message or memory text.
"""

import json
import logging
import re
import sqlite3
from dataclasses import dataclass, field, replace
from typing import Sequence

from .memory_service import resolve_source_references

from ..config import (
    DEFAULT_MEMORY_RETRIEVAL_LIMIT,
    DEFAULT_MEMORY_RETRIEVAL_MIN_SCORE,
    DEFAULT_MEMORY_RETRIEVAL_SCAN_LIMIT,
)

logger = logging.getLogger(__name__)

# Only these statuses may enter ordinary prompt evidence. Everything else
# (review, conflicted, superseded, orphaned, deleted) is excluded in SQL and
# re-checked in code.
RETRIEVABLE_STATUSES = ("accepted", "confirmed")

# Field weights used by the deterministic scoring rule.
WEIGHT_STATEMENT = 2
WEIGHT_SUBJECT = 3
WEIGHT_KEYWORD = 3

MIN_TERM_LENGTH = 3
MAX_QUERY_TERMS = 12
NEAR_DUPLICATE_JACCARD = 0.9
_SQL_CHUNK = 400

_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)

# Function words and conversational filler. Content words (verbs such as
# "want" or "like") are deliberately NOT stopwords: they are frequently the
# signal in a memory ("the user wants to learn guitar").
STOPWORDS = frozenset(
    """
    a about above after again against all also am an and any are aren as at be
    because been before being below between both but by can cannot could did do
    does doing don down during each few for from further had has have having he
    her here hers herself him himself his how i if in into is it its itself just
    me more most my myself no nor not now of off on once only or other our ours
    ourselves out over own really same she should so some such than that the
    their theirs them themselves then there these they this those through to too
    under until up very was we were what when where which while who whom why will
    with would you your yours yourself yourselves
    hey hi hello ok okay yeah yep nope thanks thank please
    """.split()
)


@dataclass(frozen=True)
class RetrievedMemory:
    """One validated memory evidence item (internal result model)."""

    id: str
    kind: str
    statement: str
    subject: str | None
    keywords: tuple[str, ...]
    origin: str
    status: str
    confidence: float
    source_conversation_id: str | None
    source_message_ids: tuple[str, ...]
    updated_at: str
    score: int = 0
    matched_terms: tuple[str, ...] = ()


@dataclass
class RetrievalResult:
    """Outcome of one retrieval attempt.

    ``error`` is a machine-readable code (``database_error``); ``reason``
    explains an empty successful result (``empty_query`` / ``no_matches``).
    ``skipped`` counts reasons only — never content.
    """

    memories: list[RetrievedMemory] = field(default_factory=list)
    query_terms: tuple[str, ...] = ()
    error: str | None = None
    reason: str | None = None
    considered: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    # True when the primary query found no evidence and the previous user
    # message in the same conversation was used as bounded context.
    query_expanded: bool = False


# ------------------------------------------------------------ query analysis ---

def _tokens(text: str) -> list[str]:
    return [token.casefold() for token in _WORD_RE.findall(text)]


def _term_variants(token: str) -> frozenset[str]:
    """Conservative morphological normalization (plural/``-ies`` only)."""
    variants = {token}
    if len(token) >= 4 and token.endswith("ies"):
        variants.add(token[:-3] + "y")
    elif len(token) >= 4 and token.endswith("s") and not token.endswith("ss"):
        variants.add(token[:-1])
    return frozenset(variants)


def _variant_set(text: str | None) -> frozenset[str]:
    if not text:
        return frozenset()
    variants: set[str] = set()
    for token in _tokens(text):
        variants |= _term_variants(token)
    return frozenset(variants)


def extract_query_terms(text: str | None) -> list[str]:
    """Meaningful query tokens, in order of appearance, de-duplicated."""
    if not isinstance(text, str) or not text:
        return []
    terms: list[str] = []
    for token in _tokens(text):
        if len(token) < MIN_TERM_LENGTH or token in STOPWORDS:
            continue
        if token not in terms:
            terms.append(token)
        if len(terms) >= MAX_QUERY_TERMS:
            break
    return terms


def _min_matched(terms: Sequence[str]) -> int:
    return 1 if len(terms) <= 2 else 2


# --------------------------------------------------------- candidate loading ---

def _search_tokens(terms: Sequence[str]) -> list[str]:
    """Every token form the scorer would accept for these query terms.

    Candidate selection must expand plural variants in both directions
    (``preferences`` → ``preference`` and vice versa); otherwise the scorer's
    normalization could never see the matching rows. Order is deterministic.
    """
    tokens: list[str] = []
    for term in terms:
        for variant in [term, *sorted(_term_variants(term) - {term})]:
            if variant not in tokens:
                tokens.append(variant)
    return tokens


def _fts_query(terms: Sequence[str]) -> str:
    # Tokens are [^\W_]+ only, so quoting cannot be escaped-out of. The prefix
    # star extends the same normalization used by the scorer, so the FTS
    # prefilter and the LIKE fallback agree on morphological variants.
    return " OR ".join(f'"{token}"*' for token in _search_tokens(terms))


def _fts_candidates(
    conn: sqlite3.Connection, terms: Sequence[str], scan_limit: int
) -> list[sqlite3.Row]:
    statuses = ", ".join("?" for _ in RETRIEVABLE_STATUSES)
    return conn.execute(
        f"""SELECT m.* FROM memories_fts
            JOIN memories AS m ON m.rowid = memories_fts.rowid
            WHERE memories_fts MATCH ?
              AND m.status IN ({statuses})
            ORDER BY m.rowid
            LIMIT ?""",
        (_fts_query(terms), *RETRIEVABLE_STATUSES, scan_limit),
    ).fetchall()


def _subject_candidates(
    conn: sqlite3.Connection, terms: Sequence[str], scan_limit: int
) -> list[sqlite3.Row]:
    """Subject matches, which the FTS index does not cover.

    Without this, a subject hit found by the LIKE fallback would be invisible
    on a build with FTS5, so the two paths would disagree about relevance.
    """
    statuses = ", ".join("?" for _ in RETRIEVABLE_STATUSES)
    tokens = _search_tokens(terms)
    conditions = " OR ".join(["lower(COALESCE(subject, '')) LIKE ?"] * len(tokens))
    params: list[object] = [f"%{token}%" for token in tokens]
    params.extend(RETRIEVABLE_STATUSES)
    params.append(scan_limit)
    return conn.execute(
        f"""SELECT * FROM memories
            WHERE ({conditions}) AND status IN ({statuses})
            ORDER BY rowid
            LIMIT ?""",
        params,
    ).fetchall()


def _like_candidates(
    conn: sqlite3.Connection, terms: Sequence[str], scan_limit: int
) -> list[sqlite3.Row]:
    statuses = ", ".join("?" for _ in RETRIEVABLE_STATUSES)
    tokens = _search_tokens(terms)
    condition = (
        "(lower(statement) LIKE ? OR lower(COALESCE(subject, '')) LIKE ?"
        " OR lower(keywords_json) LIKE ?)"
    )
    conditions = " OR ".join([condition] * len(tokens))
    params: list[object] = []
    for token in tokens:
        like = f"%{token}%"  # tokens are alphanumeric: no LIKE metacharacters
        params.extend((like, like, like))
    params.extend(RETRIEVABLE_STATUSES)
    params.append(scan_limit)
    return conn.execute(
        f"""SELECT * FROM memories
            WHERE ({conditions}) AND status IN ({statuses})
            ORDER BY rowid
            LIMIT ?""",
        params,
    ).fetchall()


def _dedupe_rows(rows: Sequence[sqlite3.Row]) -> list[sqlite3.Row]:
    """Collapse rows returned by more than one candidate query."""
    seen: set[str] = set()
    unique: list[sqlite3.Row] = []
    for row in rows:
        memory_id = str(row["id"])
        if memory_id in seen:
            continue
        seen.add(memory_id)
        unique.append(row)
    return unique


def _candidate_rows(
    conn: sqlite3.Connection, terms: Sequence[str], scan_limit: int
) -> list[sqlite3.Row]:
    """Candidate rows via FTS5, falling back to LIKE when FTS5 is unavailable.

    Both paths prefilter lifecycle statuses in SQL and are scanned at most
    ``scan_limit`` rows each; the authoritative score/filter happens in
    Python. The FTS path adds subject matches separately because the index
    only covers statement/keywords.
    """
    try:
        rows = _fts_candidates(conn, terms, scan_limit)
    except sqlite3.OperationalError:
        # FTS5 module missing, or the index/table was never created.
        logger.info("Memory retrieval: FTS5 unavailable, using LIKE fallback.")
        return _like_candidates(conn, terms, scan_limit)
    return _dedupe_rows(rows + _subject_candidates(conn, terms, scan_limit))


# -------------------------------------------------------------- row decoding ---

def _decode_row(row: sqlite3.Row) -> tuple[RetrievedMemory | None, str | None, tuple[str, ...]]:
    """Build a retrieval record; malformed stored metadata is skipped.

    Returns ``(record, None, keywords)`` or ``(None, reason, ())``. No
    exception escapes: bad JSON, wrong types, or a missing column all map to
    ``malformed_metadata``.
    """
    try:
        keywords_raw = json.loads(row["keywords_json"])
        source_ids_raw = json.loads(row["source_message_ids_json"])
        if not isinstance(keywords_raw, list) or not all(
            isinstance(item, str) for item in keywords_raw
        ):
            return None, "malformed_metadata", ()
        if not isinstance(source_ids_raw, list) or not all(
            isinstance(item, str) for item in source_ids_raw
        ):
            return None, "malformed_metadata", ()
        statement = row["statement"]
        if not isinstance(statement, str) or not statement.strip():
            return None, "malformed_metadata", ()
        confidence = float(row["confidence"])
        if row["status"] not in RETRIEVABLE_STATUSES:
            return None, "excluded_status", ()
        keywords = tuple(keywords_raw)
        return (
            RetrievedMemory(
                id=str(row["id"]),
                kind=str(row["kind"]),
                statement=statement,
                subject=row["subject"],
                keywords=keywords,
                origin=str(row["origin"]),
                status=str(row["status"]),
                confidence=confidence,
                source_conversation_id=row["source_conversation_id"],
                source_message_ids=tuple(source_ids_raw),
                updated_at=str(row["updated_at"]),
            ),
            None,
            keywords,
        )
    except (ValueError, TypeError, KeyError, IndexError):
        return None, "malformed_metadata", ()


# ------------------------------------------------------------------ scoring ---

def _score_memory(
    terms: Sequence[str],
    keyword_variants: frozenset[str],
    subject_variants: frozenset[str],
    statement_variants: frozenset[str],
) -> tuple[int, tuple[str, ...]]:
    total = 0
    matched: list[str] = []
    for term in terms:
        variants = _term_variants(term)
        if variants & keyword_variants:
            weight = WEIGHT_KEYWORD
        elif variants & subject_variants:
            weight = WEIGHT_SUBJECT
        elif variants & statement_variants:
            weight = WEIGHT_STATEMENT
        else:
            continue
        total += weight
        matched.append(term)
    return total, tuple(matched)


def _normalized_statement(text: str) -> str:
    return " ".join(text.split()).casefold()


def _near_duplicate(a: frozenset[str], b: frozenset[str]) -> bool:
    if not a or not b:
        return False
    union = a | b
    return len(a & b) / len(union) >= NEAR_DUPLICATE_JACCARD


# ---------------------------------------------------------------- provenance ---

def _existing_ids(conn: sqlite3.Connection, table: str, ids: set[str]) -> set[str]:
    """Existing ids for a fixed table name (never interpolated from input)."""
    if not ids:
        return set()
    existing: set[str] = set()
    ordered = sorted(ids)
    for start in range(0, len(ordered), _SQL_CHUNK):
        chunk = ordered[start : start + _SQL_CHUNK]
        placeholders = ", ".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT id FROM {table} WHERE id IN ({placeholders})", chunk
        ).fetchall()
        existing.update(str(row["id"]) for row in rows)
    return existing


def _existing_message_sources(
    conn: sqlite3.Connection, ids: set[str]
) -> dict[str, str]:
    """Existing message ids mapped to their actual conversation ids."""
    if not ids:
        return {}
    existing: dict[str, str] = {}
    ordered = sorted(ids)
    for start in range(0, len(ordered), _SQL_CHUNK):
        chunk = ordered[start : start + _SQL_CHUNK]
        placeholders = ", ".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT id, conversation_id FROM messages WHERE id IN ({placeholders})",
            chunk,
        ).fetchall()
        for row in rows:
            existing[str(row["id"])] = str(row["conversation_id"])
    return existing


# ---------------------------------------------------------------- entry point ---

def retrieve_relevant_memories(
    conn: sqlite3.Connection,
    settings: object,
    query_text: str | None,
    limit: int | None = None,
    context_text: str | None = None,
) -> RetrievalResult:
    """Retrieve a bounded, ranked evidence pack for ``query_text``.

    Never raises: database failures become ``error="database_error"`` with an
    empty result, which callers must treat as "no memory evidence".

    ``context_text`` is optional bounded follow-up context (the previous user
    message in the same conversation). It is used only when the primary query
    produced no evidence, so a pronoun-only follow-up can still find the
    relevant memory without affecting ordinary queries. The caller decides the
    context and must never pass another conversation's text.
    """
    terms = extract_query_terms(query_text)
    if terms:
        result = _retrieve_for_terms(conn, settings, terms, limit)
        if result.memories or result.error is not None:
            return result
    else:
        result = RetrievalResult(reason="empty_query")
    return _retrieve_with_context(conn, settings, result, context_text, limit)


def _retrieve_with_context(
    conn: sqlite3.Connection,
    settings: object,
    primary: RetrievalResult,
    context_text: str | None,
    limit: int | None,
) -> RetrievalResult:
    """One bounded retry using the previous user message, or the primary result.

    Expansion is deliberately narrow: only when the primary query found no
    memories and no error, and only with meaningful context terms. The retry
    uses the context terms alone rather than merging them with the current
    message: the current terms already failed to match, and adding them raises
    the coverage floor (a greeting or pronoun-only message would otherwise
    prevent the earlier topic from ever satisfying it). The retry still goes
    through the same lifecycle, provenance, duplicate, ranking, and budget
    pipeline, and is not extended beyond ``MAX_QUERY_TERMS``.
    """
    context_terms = extract_query_terms(context_text)
    if not context_terms:
        return primary

    expanded = _retrieve_for_terms(conn, settings, context_terms, limit)
    if expanded.error is not None:
        return expanded
    expanded.query_expanded = True
    return expanded


def _retrieve_for_terms(
    conn: sqlite3.Connection,
    settings: object,
    terms: Sequence[str],
    limit: int | None,
) -> RetrievalResult:
    """Score and validate candidates for one bounded term list."""
    result = RetrievalResult(query_terms=tuple(terms))

    scan_limit = max(
        1, int(getattr(settings, "memory_retrieval_scan_limit", DEFAULT_MEMORY_RETRIEVAL_SCAN_LIMIT))
    )
    min_score = int(getattr(settings, "memory_retrieval_min_score", DEFAULT_MEMORY_RETRIEVAL_MIN_SCORE))
    max_results = limit
    if max_results is None:
        max_results = int(getattr(settings, "memory_retrieval_limit", DEFAULT_MEMORY_RETRIEVAL_LIMIT))
    max_results = max(0, int(max_results))
    min_matched = _min_matched(terms)

    try:
        rows = _candidate_rows(conn, terms, scan_limit)
    except sqlite3.Error:
        logger.warning("Memory retrieval failed: database error while reading memories.")
        result.error = "database_error"
        return result

    result.considered = len(rows)
    scored: list[tuple[RetrievedMemory, frozenset[str]]] = []
    for row in rows:
        record, skip, keywords = _decode_row(row)
        if record is None:
            result.skipped[skip or "malformed_metadata"] = (
                result.skipped.get(skip or "malformed_metadata", 0) + 1
            )
            continue
        score, matched = _score_memory(
            terms,
            _variant_set(" ".join(keywords)),
            _variant_set(record.subject),
            _variant_set(record.statement),
        )
        if score < min_score or len(matched) < min_matched:
            result.skipped["below_threshold"] = result.skipped.get("below_threshold", 0) + 1
            continue
        scored.append(
            (
                RetrievedMemory(
                    id=record.id,
                    kind=record.kind,
                    statement=record.statement,
                    subject=record.subject,
                    keywords=record.keywords,
                    origin=record.origin,
                    status=record.status,
                    confidence=record.confidence,
                    source_conversation_id=record.source_conversation_id,
                    source_message_ids=record.source_message_ids,
                    updated_at=record.updated_at,
                    score=score,
                    matched_terms=matched,
                ),
                _variant_set(record.statement),
            )
        )

    if not scored:
        result.reason = "no_matches"
        return result

    # Deterministic total order: score, coverage, recency, then id.
    scored.sort(key=lambda item: item[0].id)
    scored.sort(key=lambda item: item[0].updated_at, reverse=True)
    scored.sort(key=lambda item: (-item[0].score, -len(item[0].matched_terms)))

    seen_statements: set[str] = set()
    unique: list[tuple[RetrievedMemory, frozenset[str]]] = []
    for record, tokens in scored:
        normalized = _normalized_statement(record.statement)
        if normalized in seen_statements:
            result.skipped["duplicate"] = result.skipped.get("duplicate", 0) + 1
            continue
        if any(_near_duplicate(tokens, kept_tokens) for _, kept_tokens in unique):
            result.skipped["duplicate"] = result.skipped.get("duplicate", 0) + 1
            continue
        seen_statements.add(normalized)
        unique.append((record, tokens))

    try:
        conversations = _existing_ids(
            conn,
            "conversations",
            {r.source_conversation_id for r, _ in unique if r.source_conversation_id},
        )
        message_sources = _existing_message_sources(
            conn, {mid for r, _ in unique for mid in r.source_message_ids}
        )
    except sqlite3.Error:
        logger.warning("Memory retrieval failed: database error while validating sources.")
        result.error = "database_error"
        result.memories = []
        return result

    for record, _ in unique:
        conversation_id, message_ids, valid = resolve_source_references(
            record.source_conversation_id,
            record.source_message_ids,
            conversations,
            message_sources,
        )
        if not valid:
            result.skipped["invalid_provenance"] = (
                result.skipped.get("invalid_provenance", 0) + 1
            )
            continue
        if len(result.memories) >= max_results:
            break
        # Only validated source references reach the prompt. A conversation
        # that exists cannot make a deleted message id look grounded.
        result.memories.append(
            replace(
                record,
                source_conversation_id=conversation_id,
                source_message_ids=tuple(message_ids),
            )
        )

    if not result.memories:
        result.reason = "no_matches"
    return result
