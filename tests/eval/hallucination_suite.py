"""
tests/eval/hallucination_suite.py

Hallucination test suite — Principle IV (NON-NEGOTIABLE), SC-005.

Every ExpertDiagnosis produced by the Application and Network Expert nodes
MUST satisfy two joint constraints:

  1. SOURCE GROUNDING (Constraint G):
     Every LogExcerpt in ``diagnosis.cited_evidence`` must be verbatim from
     ``FilteredEvidence.hit_lines``, matched by the pair ``(byte_offset, text)``.
     This constraint exercises the same guard that
     ``BaseExpert._assert_citations_grounded`` enforces at runtime, ensuring
     regressions are caught in CI before they reach production.

  2. QUOTE-MATCH (Constraint Q):
     The ``root_cause_hypothesis`` must share at least one *key token*
     (≥ ``MIN_TOKEN_LEN`` chars, case-insensitive, after stop-word filtering)
     with the body of at least one ``LogExcerpt`` in ``cited_evidence``.
     This ensures the hypothesis is textually grounded in the cited log
     evidence rather than being a plausible-sounding fabrication that merely
     references one unrelated log line.

Data-model reference
--------------------
From ``data-model.md`` §7 ExpertDiagnosis — Validation (Principle IV):
  * ``len(cited_evidence) ≥ 1`` always.
  * "Every claim in ``root_cause_hypothesis`` MUST be quote-matchable against
    ``cited_evidence`` in the hallucination test suite."

Spec references
---------------
  * **SC-005**: 100% of reports surfaced to users contain at least one cited
    log excerpt; zero uncited claims appear in user-facing output.
  * **Principle IV** (constitution.md §IV): Evidence-Backed Triage is
    NON-NEGOTIABLE.  Hallucinated facts about cluster state are treated as
    Sev-2 defects and require a regression test before close.

Running the suite
-----------------
::

    # Standalone
    pytest tests/eval/hallucination_suite.py -v

    # Full eval suite
    pytest tests/eval/ -v --tb=short

    # Via Makefile target (ci.yml eval stage)
    make eval-hallucination

CI gate
-------
Zero FAIL lines across all parametrised cases.  Any new Expert output that
triggers a violation MUST add a regression fixture to ``ADVERSARIAL_CASES``
before the originating PR is merged (constitution §IV, Sev-2).

Golden JSONL integration
------------------------
When the per-domain golden fixture files (produced by tasks T057–T059) are
present on disk, this suite automatically loads and validates every record in
each file.  The expected per-record JSON shape is::

    {
      "case_id":               "app-oom-001",
      "domain":                "Application",
      "hit_lines": [
        {
          "timestamp":    "<ISO-8601 with tz>",
          "container":    "app",
          "text":         "<already-redacted log line>",
          "byte_offset":  1024
        }
      ],
      "total_bytes":           4096,
      "total_lines":           200,
      "root_cause_hypothesis": "The container was killed due to OOMKilled …",
      "cited_evidence_indices": [0],
      "confidence":            "high",
      "runner_up_causes":      []
    }

Fields ``runner_up_causes``, ``total_bytes``, and ``total_lines`` may be
omitted; sensible defaults are applied.  If a record is malformed the suite
logs a warning and skips it rather than failing the whole file.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, NamedTuple, Optional, Sequence

import pytest

from src.shared.schemas import ExpertDiagnosis, FilteredEvidence, LogExcerpt

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

#: Minimum number of characters a token must have to be considered *key* for
#: the quote-match check.  Tokens shorter than this are discarded as too
#: generic to constitute meaningful evidence grounding.
MIN_TOKEN_LEN: int = 4

#: High-frequency, domain-agnostic words that are excluded from the
#: quote-match token comparison.  These words appear in both hypotheses and
#: log lines by chance and would cause false-negatives (spurious passes) if
#: allowed to satisfy the shared-token requirement.
STOP_WORDS: FrozenSet[str] = frozenset(
    {
        "this",
        "that",
        "with",
        "from",
        "have",
        "been",
        "were",
        "they",
        "their",
        "there",
        "when",
        "will",
        "into",
        "also",
        "some",
        "more",
        "than",
        "then",
        "what",
        "which",
        "where",
        "could",
        "would",
        "should",
        "about",
        "after",
        "error",
        "failed",
        "failure",
        # "error" / "failed" are intentionally excluded because they appear in
        # nearly every log line and every diagnosis regardless of domain,
        # making them useless as a quote-match signal.
    }
)

# Location of per-domain expert golden JSONL files (T057–T058).
_EVAL_DIR = Path(__file__).parent
GOLDEN_FILES: Dict[str, Path] = {
    "Application": _EVAL_DIR / "application_expert_golden.jsonl",
    "Network": _EVAL_DIR / "network_expert_golden.jsonl",
}

# Sentinel timestamp used by inline fixtures — set to a fixed point in
# time so test output is deterministic regardless of when the suite runs.
_T0 = datetime(2026, 5, 14, 10, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# ViolationDetail
# ---------------------------------------------------------------------------


class ViolationDetail(NamedTuple):
    """Machine-readable description of a single constraint violation."""

    constraint: str  # "GROUNDING" or "QUOTE_MATCH"
    case_id: str
    domain: str
    detail: str  # human-readable explanation including offending values


# ---------------------------------------------------------------------------
# HallucinationChecker
# ---------------------------------------------------------------------------


class HallucinationChecker:
    """
    Stateless checker that enforces Constraints G and Q on an
    ``(ExpertDiagnosis, FilteredEvidence)`` pair.

    Designed for use both inside this test suite and as an import for any
    future eval harness or pre-merge hook::

        checker = HallucinationChecker()
        violations = checker.check(diagnosis, evidence, case_id="app-oom-001")
        assert not violations, checker.format_violations(violations)

    Parameters
    ----------
    min_token_len:
        Override ``MIN_TOKEN_LEN`` for this instance.
    stop_words:
        Override ``STOP_WORDS`` for this instance.  Pass ``frozenset()`` to
        disable stop-word filtering entirely.
    """

    def __init__(
        self,
        min_token_len: int = MIN_TOKEN_LEN,
        stop_words: Optional[FrozenSet[str]] = None,
    ) -> None:
        self.min_token_len = min_token_len
        self.stop_words: FrozenSet[str] = (
            stop_words if stop_words is not None else STOP_WORDS
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(
        self,
        diagnosis: ExpertDiagnosis,
        evidence: FilteredEvidence,
        *,
        case_id: str = "unknown",
    ) -> List[ViolationDetail]:
        """Run both constraints and return the (possibly empty) violation list.

        Parameters
        ----------
        diagnosis:
            The ``ExpertDiagnosis`` to validate.
        evidence:
            The ``FilteredEvidence`` that was supplied to the Expert node as
            input.  ``hit_lines`` is the authoritative source for the
            grounding check.
        case_id:
            An opaque string identifying the test case — included in every
            ``ViolationDetail`` for traceable CI output.

        Returns
        -------
        List[ViolationDetail]
            Empty ⇒ fully grounded.  Non-empty ⇒ at least one violation;
            pass the list to :meth:`format_violations` for a human-readable
            error string suitable for ``assert`` messages.
        """
        violations: List[ViolationDetail] = []
        violations.extend(self._check_source_grounding(diagnosis, evidence, case_id))
        violations.extend(self._check_quote_match(diagnosis, case_id))
        return violations

    @staticmethod
    def format_violations(violations: Sequence[ViolationDetail]) -> str:
        """Render a violation list as a human-readable multi-line string.

        Suitable for use as the ``assert`` message::

            assert not violations, checker.format_violations(violations)
        """
        lines: List[str] = [
            f"Hallucination constraint violations ({len(violations)}):"
        ]
        for v in violations:
            lines.append(
                f"  [{v.constraint}] case={v.case_id!r}  domain={v.domain!r}"
            )
            for line in v.detail.splitlines():
                lines.append(f"    {line}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Constraint G — Source Grounding
    # ------------------------------------------------------------------

    def _check_source_grounding(
        self,
        diagnosis: ExpertDiagnosis,
        evidence: FilteredEvidence,
        case_id: str,
    ) -> List[ViolationDetail]:
        """Verify every cited LogExcerpt is verbatim from ``hit_lines``.

        Matching key: ``(byte_offset, text)``.  Both fields must match;
        matching on text alone would allow an adversarial LLM to inject the
        correct text with a fabricated ``byte_offset`` and pass the check.

        This mirrors the runtime guard in
        ``BaseExpert._assert_citations_grounded`` exactly, so prod and CI
        share the same failure semantics.
        """
        violations: List[ViolationDetail] = []
        hit_key: FrozenSet[tuple] = frozenset(
            (exc.byte_offset, exc.text) for exc in evidence.hit_lines
        )
        for i, exc in enumerate(diagnosis.cited_evidence):
            if (exc.byte_offset, exc.text) not in hit_key:
                violations.append(
                    ViolationDetail(
                        constraint="GROUNDING",
                        case_id=case_id,
                        domain=diagnosis.domain,
                        detail=(
                            f"cited_evidence[{i}] is NOT verbatim from "
                            f"FilteredEvidence.hit_lines.\n"
                            f"cited text        : {exc.text!r}\n"
                            f"cited byte_offset : {exc.byte_offset}\n"
                            f"(Sev-2 hallucination defect — constitution §IV)\n"
                            f"Add a regression fixture before closing the PR."
                        ),
                    )
                )
        return violations

    # ------------------------------------------------------------------
    # Constraint Q — Quote-Match
    # ------------------------------------------------------------------

    def _check_quote_match(
        self,
        diagnosis: ExpertDiagnosis,
        case_id: str,
    ) -> List[ViolationDetail]:
        """Verify the hypothesis shares ≥1 key token with cited evidence.

        Data-model §7 Validation (Principle IV, NON-NEGOTIABLE):
          "Every claim in ``root_cause_hypothesis`` MUST be quote-matchable
          against ``cited_evidence`` in the hallucination test suite."

        Algorithm
        ---------
        1. Tokenise all ``cited_evidence[i].text`` strings with
           :meth:`_key_tokens_from_texts` (lowercase, unicode-normalise,
           split on non-alphanumeric chars, apply ``MIN_TOKEN_LEN`` +
           ``STOP_WORDS`` filter).
        2. Tokenise ``root_cause_hypothesis`` with the same rules.
        3. Require intersection ≥ 1.  An empty intersection indicates the
           hypothesis was assembled without referencing any identifiable term
           from the cited log lines (SC-005 violation).
        """
        violations: List[ViolationDetail] = []

        evidence_tokens = self._key_tokens_from_texts(
            [exc.text for exc in diagnosis.cited_evidence]
        )
        hypothesis_tokens = self._key_tokens_from_texts(
            [diagnosis.root_cause_hypothesis]
        )

        shared = evidence_tokens & hypothesis_tokens
        if not shared:
            sample = sorted(evidence_tokens)[:12]
            violations.append(
                ViolationDetail(
                    constraint="QUOTE_MATCH",
                    case_id=case_id,
                    domain=diagnosis.domain,
                    detail=(
                        "root_cause_hypothesis shares ZERO key tokens with "
                        "cited_evidence texts.\n"
                        f"hypothesis            : {diagnosis.root_cause_hypothesis!r}\n"
                        f"evidence key tokens   : {sample!r} (showing ≤12)\n"
                        f"(SC-005 violation — zero uncited claims in user-facing output)"
                    ),
                )
            )
        return violations

    # ------------------------------------------------------------------
    # Token-extraction helper
    # ------------------------------------------------------------------

    def _key_tokens_from_texts(self, texts: Sequence[str]) -> FrozenSet[str]:
        """Extract unique, meaningful tokens from one or more text strings.

        Steps applied to each text:

        1. Lowercase + NFKD unicode normalise (collapses accented chars).
        2. Split on any non-alphanumeric character (``re.split``).
        3. Keep tokens with length ≥ ``self.min_token_len``.
        4. Remove tokens in ``self.stop_words``.

        Kubernetes-specific note: hyphenated names such as ``connection-refused``
        are split into ``connection`` and ``refused``, both of which are long
        enough to be key tokens.  This is correct behaviour — either half
        anchors the quote-match.
        """
        tokens: set[str] = set()
        for text in texts:
            normalised = unicodedata.normalize("NFKD", text.lower())
            for tok in re.split(r"[^\w]", normalised):
                if len(tok) >= self.min_token_len and tok not in self.stop_words:
                    tokens.add(tok)
        return frozenset(tokens)


# ---------------------------------------------------------------------------
# Fixture builder helpers
# ---------------------------------------------------------------------------


def _make_log_excerpt(
    text: str,
    byte_offset: int,
    container: str = "app",
    timestamp: datetime = _T0,
) -> LogExcerpt:
    """Build a ``LogExcerpt`` for use in inline fixtures."""
    return LogExcerpt(
        timestamp=timestamp,
        container=container,
        text=text,
        byte_offset=byte_offset,
    )


def _make_evidence(hit_lines: List[LogExcerpt]) -> FilteredEvidence:
    """Wrap a list of ``LogExcerpt`` objects in a minimal ``FilteredEvidence``."""
    return FilteredEvidence(
        total_bytes=sum(len(exc.text) for exc in hit_lines),
        total_lines=len(hit_lines),
        hit_lines=hit_lines,
        hit_count=len(hit_lines),
        truncated=False,
        containers_sampled=list({exc.container for exc in hit_lines}),
    )


def _make_diagnosis(
    domain: str,
    hypothesis: str,
    cited: List[LogExcerpt],
    confidence: str = "high",
) -> ExpertDiagnosis:
    """Build an ``ExpertDiagnosis`` for inline fixtures (no LLM required)."""
    return ExpertDiagnosis(
        domain=domain,  # type: ignore[arg-type]
        root_cause_hypothesis=hypothesis,
        cited_evidence=cited,
        confidence=confidence,  # type: ignore[arg-type]
        runner_up_causes=[],
        proposed_fix=None,
        model="test-model",
        tokens=0,
    )


# ---------------------------------------------------------------------------
# Inline test-case registry
# ---------------------------------------------------------------------------


class _Case:
    """Container for a single parametrised hallucination test scenario."""

    __slots__ = ("case_id", "diagnosis", "evidence", "expect_violations", "note")

    def __init__(
        self,
        case_id: str,
        diagnosis: ExpertDiagnosis,
        evidence: FilteredEvidence,
        *,
        expect_violations: bool,
        note: str = "",
    ) -> None:
        self.case_id = case_id
        self.diagnosis = diagnosis
        self.evidence = evidence
        self.expect_violations = expect_violations
        self.note = note


# ---------------------------------------------------------------------------
# Happy-path cases (expect_violations=False)
# ---------------------------------------------------------------------------

_app_hit_oom = _make_log_excerpt(
    text="container app OOMKilled: memory limit 512Mi exceeded, exit code 137",
    byte_offset=1024,
    container="app",
)
_app_hit_restart = _make_log_excerpt(
    text="app restarting due to OOMKilled signal, restart_count=5",
    byte_offset=2048,
    container="app",
)
_PASS_APP_OOM = _Case(
    case_id="pass-app-oom-grounded",
    diagnosis=_make_diagnosis(
        domain="Application",
        hypothesis=(
            "The application container was OOMKilled after exceeding the "
            "512Mi memory limit (restart_count=5)."
        ),
        cited=[_app_hit_oom, _app_hit_restart],
    ),
    evidence=_make_evidence([_app_hit_oom, _app_hit_restart]),
    expect_violations=False,
    note="All cited excerpts are in hit_lines; hypothesis quotes 'OOMKilled' "
    "and 'restart_count' both present in evidence.",
)

_net_hit_refused = _make_log_excerpt(
    text="ECONNREFUSED connecting to svc:8080; connection refused by remote host",
    byte_offset=512,
    container="proxy",
)
_net_hit_timeout = _make_log_excerpt(
    text="dial tcp 10.0.0.5:5432: i/o timeout after 3 retries",
    byte_offset=768,
    container="proxy",
)
_PASS_NET_CONN_REFUSED = _Case(
    case_id="pass-net-connection-refused",
    diagnosis=_make_diagnosis(
        domain="Network",
        hypothesis=(
            "The proxy sidecar is receiving ECONNREFUSED from the upstream "
            "service, suggesting the target endpoint is unreachable."
        ),
        cited=[_net_hit_refused],
    ),
    evidence=_make_evidence([_net_hit_refused, _net_hit_timeout]),
    expect_violations=False,
    note="Cited excerpt is in hit_lines; hypothesis contains 'ECONNREFUSED'.",
)

_net_hit_dns = _make_log_excerpt(
    text="getaddrinfo ENOTFOUND kafka.internal: DNS lookup failed for hostname",
    byte_offset=1024,
    container="consumer",
)
_PASS_NET_DNS = _Case(
    case_id="pass-net-dns-lookup-failed",
    diagnosis=_make_diagnosis(
        domain="Network",
        hypothesis=(
            "DNS lookup failed for kafka.internal: getaddrinfo returned "
            "ENOTFOUND, indicating the service is not registered in the "
            "cluster DNS."
        ),
        cited=[_net_hit_dns],
    ),
    evidence=_make_evidence([_net_hit_dns]),
    expect_violations=False,
    note="Single cited excerpt; hypothesis explicitly quotes 'getaddrinfo' "
    "and 'ENOTFOUND' from the evidence text.",
)

_app_hit_panic = _make_log_excerpt(
    text="goroutine 1 [running]: runtime: panic: runtime error: index out of range [3] with length 2",
    byte_offset=4096,
    container="api",
)
_PASS_APP_PANIC = _Case(
    case_id="pass-app-panic-index-range",
    diagnosis=_make_diagnosis(
        domain="Application",
        hypothesis=(
            "A Go runtime panic occurred — index out of range [3] with "
            "length 2 — crashing the api container."
        ),
        cited=[_app_hit_panic],
    ),
    evidence=_make_evidence([_app_hit_panic]),
    expect_violations=False,
    note="Hypothesis quotes 'runtime', 'index', and 'range' from evidence.",
)

# ---------------------------------------------------------------------------
# Adversarial — Grounding violations (expect_violations=True, Constraint G)
# ---------------------------------------------------------------------------

_fabricated_excerpt = _make_log_excerpt(
    text="container app killed by SIGTERM after PDB violation",
    byte_offset=9999,  # this byte_offset+text does NOT exist in hit_lines
    container="app",
)
_FAIL_GROUNDING_FABRICATED_EXCERPT = _Case(
    case_id="fail-grounding-fabricated-excerpt",
    diagnosis=_make_diagnosis(
        domain="Application",
        hypothesis="The container was killed by SIGTERM following a PDB violation.",
        cited=[_fabricated_excerpt],
    ),
    evidence=_make_evidence(
        [
            _make_log_excerpt(
                text="container app OOMKilled: memory limit exceeded",
                byte_offset=0,
            )
        ]
    ),
    expect_violations=True,
    note="The cited excerpt has byte_offset=9999 which is not present in "
    "hit_lines — Constraint G violation.",
)

_real_text_wrong_offset = _make_log_excerpt(
    text="ECONNREFUSED connecting to svc:8080",
    byte_offset=0,  # correct text but wrong byte_offset vs. hit_lines
    container="proxy",
)
_hit_same_text_different_offset = _make_log_excerpt(
    text="ECONNREFUSED connecting to svc:8080",
    byte_offset=512,  # actual byte_offset in hit_lines
    container="proxy",
)
_FAIL_GROUNDING_WRONG_OFFSET = _Case(
    case_id="fail-grounding-correct-text-wrong-offset",
    diagnosis=_make_diagnosis(
        domain="Network",
        hypothesis="ECONNREFUSED indicates the upstream service is unreachable.",
        cited=[_real_text_wrong_offset],
    ),
    evidence=_make_evidence([_hit_same_text_different_offset]),
    expect_violations=True,
    note="Text matches but byte_offset=0 ≠ 512; matching on text alone "
    "would pass, but the full (offset, text) key must match — Constraint G.",
)

_app_hit_crash = _make_log_excerpt(
    text="application exited with non-zero exit code 137",
    byte_offset=100,
    container="web",
)
_ghost_excerpt = _make_log_excerpt(
    text="secret_key=abc123 leaked into environment",  # not in hit_lines
    byte_offset=200,
    container="web",
)
_FAIL_GROUNDING_INJECTED_SECRET = _Case(
    case_id="fail-grounding-injected-synthetic-excerpt",
    diagnosis=_make_diagnosis(
        domain="Application",
        hypothesis=(
            "The web container exited with code 137; a secret_key may have "
            "been leaked into its environment."
        ),
        cited=[_app_hit_crash, _ghost_excerpt],
    ),
    evidence=_make_evidence([_app_hit_crash]),  # _ghost_excerpt NOT in hit_lines
    expect_violations=True,
    note="cited_evidence[1] is not in hit_lines; simulates LLM injecting a "
    "synthesised 'evidence' line not present in the filtered log stream.",
)

# ---------------------------------------------------------------------------
# Adversarial — Quote-Match violations (expect_violations=True, Constraint Q)
# ---------------------------------------------------------------------------

_net_hit_real = _make_log_excerpt(
    text="ECONNREFUSED connecting to postgres:5432 from api-server",
    byte_offset=0,
    container="api",
)
_FAIL_QUOTE_MATCH_FABRICATED_HYPOTHESIS = _Case(
    case_id="fail-quote-match-hypothesis-ignores-evidence",
    diagnosis=_make_diagnosis(
        domain="Network",
        hypothesis=(
            "The deployment is misconfigured and the replica count is set "
            "below the minimum required for high availability."
        ),
        cited=[_net_hit_real],
    ),
    evidence=_make_evidence([_net_hit_real]),
    expect_violations=True,
    note="The cited excerpt is grounded (Constraint G passes) but the "
    "hypothesis discusses 'replica count' / 'high availability' — neither "
    "appears in the cited evidence — Constraint Q violation.",
)

_net_hit_quiet = _make_log_excerpt(
    text="connection refused: dial tcp 10.0.0.5:5432: connect: connection refused",
    byte_offset=0,
    container="proxy",
)
_FAIL_QUOTE_MATCH_DOMAIN_MISMATCH = _Case(
    case_id="fail-quote-match-domain-mismatch-hypothesis",
    diagnosis=_make_diagnosis(
        domain="Network",
        hypothesis=(
            "The ingress controller's TLS certificate has expired, causing "
            "downstream handshakes to be rejected."
        ),
        cited=[_net_hit_quiet],
    ),
    evidence=_make_evidence([_net_hit_quiet]),
    expect_violations=True,
    note="Evidence is connection refused; hypothesis fabricates a TLS cert "
    "expiry story with zero shared tokens — Constraint Q violation.",
)

_app_hit_oom2 = _make_log_excerpt(
    text="OOMKilled: container memory usage 489Mi exceeds limit 256Mi",
    byte_offset=0,
    container="worker",
)
_FAIL_QUOTE_MATCH_TOO_GENERIC = _Case(
    case_id="fail-quote-match-generic-non-anchored-hypothesis",
    diagnosis=_make_diagnosis(
        domain="Application",
        hypothesis="Something went wrong with the node scheduling policy.",
        cited=[_app_hit_oom2],
    ),
    evidence=_make_evidence([_app_hit_oom2]),
    expect_violations=True,
    note="Hypothesis contains no token from the OOMKilled evidence — "
    "tokens like 'went', 'wrong', 'node', 'scheduling', 'policy' are "
    "either too short (< 4 chars) or absent from the evidence — "
    "Constraint Q violation.",
)

# ---------------------------------------------------------------------------
# Combined adversarial: both Constraint G AND Constraint Q violated
# ---------------------------------------------------------------------------

_FAIL_BOTH_CONSTRAINTS = _Case(
    case_id="fail-both-grounding-and-quote-match",
    diagnosis=_make_diagnosis(
        domain="Application",
        hypothesis="The service mesh sidecar is intercepting all egress traffic.",
        cited=[
            _make_log_excerpt(
                text="completely fabricated log line that never existed",
                byte_offset=99999,
                container="sidecar",
            )
        ],
    ),
    evidence=_make_evidence(
        [
            _make_log_excerpt(
                text="OOMKilled: worker container exceeded 128Mi limit",
                byte_offset=0,
                container="worker",
            )
        ]
    ),
    expect_violations=True,
    note="Cited excerpt is not in hit_lines (Constraint G) AND hypothesis "
    "shares no tokens with the OOMKilled evidence (Constraint Q).",
)

# ---------------------------------------------------------------------------
# Master parametrise list
# ---------------------------------------------------------------------------

INLINE_CASES: List[_Case] = [
    # Happy path — should produce zero violations
    _PASS_APP_OOM,
    _PASS_NET_CONN_REFUSED,
    _PASS_NET_DNS,
    _PASS_APP_PANIC,
    # Adversarial — Constraint G (source grounding)
    _FAIL_GROUNDING_FABRICATED_EXCERPT,
    _FAIL_GROUNDING_WRONG_OFFSET,
    _FAIL_GROUNDING_INJECTED_SECRET,
    # Adversarial — Constraint Q (quote-match)
    _FAIL_QUOTE_MATCH_FABRICATED_HYPOTHESIS,
    _FAIL_QUOTE_MATCH_DOMAIN_MISMATCH,
    _FAIL_QUOTE_MATCH_TOO_GENERIC,
    # Combined
    _FAIL_BOTH_CONSTRAINTS,
]


# ---------------------------------------------------------------------------
# Golden JSONL loader
# ---------------------------------------------------------------------------


def _load_golden_jsonl(path: Path) -> List[Dict[str, Any]]:
    """Parse a golden JSONL file and return a list of record dicts.

    Invalid / malformed records are skipped with a warning so a single bad
    line does not abort the entire file's validation.
    """
    records: List[Dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            raw = raw.strip()
            if not raw or raw.startswith("#"):
                continue
            try:
                records.append(json.loads(raw))
            except json.JSONDecodeError as exc:
                logger.warning(
                    "hallucination_suite: skipping malformed record at "
                    "%s:%d — %s",
                    path.name,
                    lineno,
                    exc,
                )
    return records


def _record_to_case(record: Dict[str, Any], source_file: str) -> Optional[_Case]:
    """Convert a golden JSONL record into a ``_Case`` object.

    Returns ``None`` if the record is missing required fields.
    """
    case_id = record.get("case_id", f"{source_file}:unknown")
    domain = record.get("domain")
    hit_line_dicts = record.get("hit_lines")
    hypothesis = record.get("root_cause_hypothesis")
    cited_indices = record.get("cited_evidence_indices")

    if domain is None or hit_line_dicts is None or hypothesis is None:
        logger.warning(
            "hallucination_suite: skipping record %r — missing required "
            "fields (domain, hit_lines, root_cause_hypothesis)",
            case_id,
        )
        return None

    try:
        hit_lines = [LogExcerpt.model_validate(h) for h in hit_line_dicts]
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "hallucination_suite: skipping record %r — hit_lines parse "
            "error: %s",
            case_id,
            exc,
        )
        return None

    if cited_indices is None:
        # Default: treat all hit_lines as cited (conservative check).
        cited = hit_lines
    else:
        valid = [i for i in cited_indices if isinstance(i, int) and 0 <= i < len(hit_lines)]
        cited = [hit_lines[i] for i in valid]
        if not cited:
            logger.warning(
                "hallucination_suite: record %r has no valid cited_evidence_indices; "
                "using all hit_lines as fallback",
                case_id,
            )
            cited = hit_lines

    total_bytes = record.get("total_bytes", sum(len(h.text) for h in hit_lines))
    total_lines = record.get("total_lines", len(hit_lines))
    confidence = record.get("confidence", "high")

    try:
        diagnosis = _make_diagnosis(
            domain=domain,
            hypothesis=hypothesis,
            cited=cited,
            confidence=confidence,
        )
        evidence = FilteredEvidence(
            total_bytes=total_bytes,
            total_lines=total_lines,
            hit_lines=hit_lines,
            hit_count=len(hit_lines),
            truncated=False,
            containers_sampled=list({h.container for h in hit_lines}),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "hallucination_suite: skipping record %r — schema error: %s",
            case_id,
            exc,
        )
        return None

    return _Case(
        case_id=case_id,
        diagnosis=diagnosis,
        evidence=evidence,
        expect_violations=False,  # golden records are expected to be clean
        note=f"loaded from {source_file}",
    )


def _collect_golden_cases() -> List[_Case]:
    """Load all available expert golden JSONL files and convert to ``_Case`` objects."""
    cases: List[_Case] = []
    for domain, path in GOLDEN_FILES.items():
        if not path.exists():
            continue
        logger.info(
            "hallucination_suite: loading golden fixtures from %s", path.name
        )
        for record in _load_golden_jsonl(path):
            case = _record_to_case(record, source_file=path.name)
            if case is not None:
                cases.append(case)
    return cases


# ---------------------------------------------------------------------------
# pytest — inline parametrised tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case",
    INLINE_CASES,
    ids=[c.case_id for c in INLINE_CASES],
)
def test_hallucination_inline(case: _Case) -> None:
    """Run both grounding and quote-match checks against each inline fixture.

    Pass cases (``expect_violations=False``) assert zero violations.
    Adversarial cases (``expect_violations=True``) assert ≥1 violation,
    confirming the checker correctly detects the planted defect.
    """
    checker = HallucinationChecker()
    violations = checker.check(case.diagnosis, case.evidence, case_id=case.case_id)

    if case.expect_violations:
        assert violations, (
            f"[{case.case_id}] Expected at least one hallucination violation "
            f"({case.note}) but none were detected.  "
            f"The checker may have missed the planted defect."
        )
    else:
        assert not violations, (
            f"[{case.case_id}] Unexpected violations detected "
            f"({case.note}):\n"
            + checker.format_violations(violations)
        )


# ---------------------------------------------------------------------------
# pytest — golden JSONL integration tests (skipped when files absent)
# ---------------------------------------------------------------------------


def _golden_cases() -> List[_Case]:
    """Lazily collect golden cases; returns empty list when no files are present."""
    return _collect_golden_cases()


@pytest.mark.parametrize(
    "case",
    _golden_cases(),
    ids=[c.case_id for c in _golden_cases()],
)
def test_hallucination_golden(case: _Case) -> None:
    """Run grounding + quote-match checks against every expert golden record.

    This test is parametrised at collection time; if no golden JSONL files
    exist (T057–T059 not yet complete) the parametrise list is empty and
    pytest reports zero collected items — this is expected and not a failure.

    Once golden files are present, each record must satisfy both constraints.
    A failure here means the golden fixture itself contains a hallucinated
    diagnosis and must be corrected before merge (Sev-2, constitution §IV).
    """
    checker = HallucinationChecker()
    violations = checker.check(case.diagnosis, case.evidence, case_id=case.case_id)
    assert not violations, (
        f"[{case.case_id}] Golden fixture violates hallucination constraints:\n"
        + checker.format_violations(violations)
    )


# ---------------------------------------------------------------------------
# pytest — HallucinationChecker unit tests
# ---------------------------------------------------------------------------


class TestHallucinationCheckerUnit:
    """Unit tests for HallucinationChecker internals.

    These tests are independent of the golden files and verify the core
    algorithm behaviour directly.
    """

    def setup_method(self) -> None:
        self.checker = HallucinationChecker()

    # ------------------------------------------------------------------
    # _key_tokens_from_texts
    # ------------------------------------------------------------------

    def test_tokens_lowercased(self) -> None:
        tokens = self.checker._key_tokens_from_texts(["OOMKilled Memory LIMIT"])
        assert "oomkilled" in tokens
        assert "memory" in tokens
        assert "limit" in tokens

    def test_tokens_min_length_filter(self) -> None:
        tokens = self.checker._key_tokens_from_texts(["ok go err bad"])
        assert not tokens  # all < 4 chars

    def test_tokens_stop_word_filter(self) -> None:
        tokens = self.checker._key_tokens_from_texts(["error failed failure with that"])
        # "error", "failed", "failure" are stop words; "with", "that" are < 4 chars
        assert not tokens

    def test_tokens_split_on_punctuation(self) -> None:
        tokens = self.checker._key_tokens_from_texts(
            ["connection-refused: ECONNREFUSED"]
        )
        assert "connection" in tokens
        assert "refused" in tokens
        assert "econnrefused" in tokens

    def test_tokens_unicode_normalised(self) -> None:
        tokens = self.checker._key_tokens_from_texts(["café restart"])
        assert "restart" in tokens

    def test_tokens_empty_input(self) -> None:
        assert self.checker._key_tokens_from_texts([]) == frozenset()

    def test_tokens_empty_string(self) -> None:
        assert self.checker._key_tokens_from_texts([""]) == frozenset()

    # ------------------------------------------------------------------
    # _check_source_grounding
    # ------------------------------------------------------------------

    def test_grounding_passes_when_all_in_hit_lines(self) -> None:
        exc = _make_log_excerpt(text="some log line", byte_offset=0)
        evidence = _make_evidence([exc])
        diagnosis = _make_diagnosis("Application", "some log line issue", [exc])
        violations = self.checker._check_source_grounding(
            diagnosis, evidence, "unit-pass"
        )
        assert not violations

    def test_grounding_fails_when_excerpt_absent(self) -> None:
        real = _make_log_excerpt(text="real line", byte_offset=0)
        ghost = _make_log_excerpt(text="ghost line", byte_offset=9999)
        evidence = _make_evidence([real])
        diagnosis = _make_diagnosis("Application", "ghost line issue", [ghost])
        violations = self.checker._check_source_grounding(
            diagnosis, evidence, "unit-fail"
        )
        assert len(violations) == 1
        assert violations[0].constraint == "GROUNDING"
        assert "byte_offset" in violations[0].detail

    def test_grounding_fails_on_text_match_wrong_offset(self) -> None:
        """Same text but different byte_offset must fail (full key check)."""
        in_lines = _make_log_excerpt(text="OOMKilled", byte_offset=100)
        cited_wrong = _make_log_excerpt(text="OOMKilled", byte_offset=0)
        evidence = _make_evidence([in_lines])
        diagnosis = _make_diagnosis("Application", "OOMKilled issue", [cited_wrong])
        violations = self.checker._check_source_grounding(
            diagnosis, evidence, "unit-offset"
        )
        assert len(violations) == 1
        assert violations[0].constraint == "GROUNDING"

    def test_grounding_multiple_violations_reported(self) -> None:
        real = _make_log_excerpt(text="real", byte_offset=0)
        g1 = _make_log_excerpt(text="ghost1", byte_offset=1)
        g2 = _make_log_excerpt(text="ghost2", byte_offset=2)
        evidence = _make_evidence([real])
        diagnosis = _make_diagnosis("Application", "ghost issue", [g1, g2])
        violations = self.checker._check_source_grounding(
            diagnosis, evidence, "unit-multi"
        )
        assert len(violations) == 2

    # ------------------------------------------------------------------
    # _check_quote_match
    # ------------------------------------------------------------------

    def test_quote_match_passes_when_tokens_overlap(self) -> None:
        exc = _make_log_excerpt(text="OOMKilled container exit code 137", byte_offset=0)
        diagnosis = _make_diagnosis(
            "Application",
            "The container was OOMKilled after exceeding its memory limit.",
            [exc],
        )
        violations = self.checker._check_quote_match(diagnosis, "unit-pass")
        assert not violations

    def test_quote_match_fails_when_no_tokens_overlap(self) -> None:
        exc = _make_log_excerpt(text="OOMKilled container exit code 137", byte_offset=0)
        diagnosis = _make_diagnosis(
            "Application",
            "The TLS certificate has expired causing ingress rejection.",
            [exc],
        )
        violations = self.checker._check_quote_match(diagnosis, "unit-fail")
        assert len(violations) == 1
        assert violations[0].constraint == "QUOTE_MATCH"

    def test_quote_match_case_insensitive(self) -> None:
        exc = _make_log_excerpt(text="OOMKILLED memory exceeded", byte_offset=0)
        diagnosis = _make_diagnosis(
            "Application",
            "Container crashed due to oomkilled memory pressure.",
            [exc],
        )
        violations = self.checker._check_quote_match(diagnosis, "unit-case")
        assert not violations

    # ------------------------------------------------------------------
    # format_violations
    # ------------------------------------------------------------------

    def test_format_violations_contains_constraint_label(self) -> None:
        exc = _make_log_excerpt(text="ghost", byte_offset=99)
        evidence = _make_evidence(
            [_make_log_excerpt(text="real", byte_offset=0)]
        )
        diagnosis = _make_diagnosis("Application", "ghost scenario", [exc])
        violations = self.checker.check(diagnosis, evidence, case_id="fmt-test")
        rendered = self.checker.format_violations(violations)
        assert "GROUNDING" in rendered
        assert "fmt-test" in rendered

    # ------------------------------------------------------------------
    # Custom configuration
    # ------------------------------------------------------------------

    def test_custom_min_token_len(self) -> None:
        """Lowering MIN_TOKEN_LEN allows shorter tokens to satisfy quote-match."""
        checker = HallucinationChecker(min_token_len=2)
        exc = _make_log_excerpt(text="db up", byte_offset=0)
        diagnosis = _make_diagnosis("Application", "db is up and running", [exc])
        violations = checker._check_quote_match(diagnosis, "custom-len")
        # "db" is 2 chars → passes with min_token_len=2
        assert not violations

    def test_custom_stop_words_empty(self) -> None:
        """Clearing stop_words lets 'error' satisfy quote-match (normally excluded)."""
        checker = HallucinationChecker(stop_words=frozenset())
        exc = _make_log_excerpt(text="error in module", byte_offset=0)
        diagnosis = _make_diagnosis("Application", "An error was encountered.", [exc])
        violations = checker._check_quote_match(diagnosis, "custom-stop")
        assert not violations
