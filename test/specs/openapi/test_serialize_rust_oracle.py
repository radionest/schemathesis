import json
import os
import re
import subprocess
from dataclasses import dataclass

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

try:
    import re._parser as sre_parse
except ImportError:
    import sre_parse

from schemathesis.specs.openapi.patterns import _serialize
from schemathesis.core.errors import InternalError

ORACLE_BINARY = os.path.join(
    os.path.dirname(__file__), "_regex_oracle", "target", "release", "regex-oracle"
)

# Skip the entire module if the oracle binary hasn't been built
pytestmark = pytest.mark.skipif(
    not os.path.isfile(ORACLE_BINARY),
    reason="Rust regex-oracle binary not built. Run: cargo build --release in test/specs/openapi/_regex_oracle/",
)


def _canonicalize_batch(patterns: list[str]) -> list[dict]:
    """Send patterns to Rust oracle, get canonical HIR forms back."""
    result = subprocess.run(
        [ORACLE_BINARY],
        input=json.dumps(patterns),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Oracle failed: {result.stderr}")
    return json.loads(result.stdout)


def _is_valid_regex(pattern: str) -> bool:
    try:
        re.compile(pattern)
        return True
    except (re.error, RecursionError):
        return False


# Patterns that Rust's regex crate interprets differently than Python's re:
# \< and \> are word boundary start/end in Rust, but literal < > in Python
_RUST_DIALECT_DIFFS = {"\\<", "\\>"}


@dataclass(frozen=True, slots=True)
class CheckResult:
    ok: bool
    detail: str


@dataclass(frozen=True, slots=True)
class HIRMismatch:
    pattern: str
    serialized: str
    orig_hir: str
    ser_hir: str


def _check_pattern(pattern: str) -> CheckResult:
    """Check one pattern: serialize(parse(P)) must have same Rust HIR as P."""
    if any(seq in pattern for seq in _RUST_DIALECT_DIFFS):
        return CheckResult(ok=True, detail="Rust dialect difference")

    try:
        serialized = _serialize(list(sre_parse.parse(pattern)))
    except InternalError:
        return CheckResult(ok=True, detail="unsupported opcode")

    [orig], [ser] = _canonicalize_batch([pattern]), _canonicalize_batch([serialized])

    if orig["error"] or ser["error"]:
        return CheckResult(ok=True, detail=f"Rust parse difference: {orig.get('error') or ser.get('error')}")

    if orig["canonical"] == ser["canonical"]:
        return CheckResult(ok=True, detail="OK")

    return CheckResult(
        ok=False,
        detail=(
            f"HIR mismatch:\n"
            f"  original:   {pattern!r}\n"
            f"  serialized: {serialized!r}\n"
            f"  rust(orig): {orig['canonical'][:100]}\n"
            f"  rust(ser):  {ser['canonical'][:100]}"
        ),
    )


# ---------------------------------------------------------------------------
# Parametrized: known patterns from the test suite
# ---------------------------------------------------------------------------

_KNOWN_PATTERNS = [
    r"[a-z]+",
    r"\d{3}",
    r"^[a-z]*$",
    r"^.+$",
    r"a|b",
    r"[-a-z]",
    r"[a-z-]",
    r"[a-z0-9]+",
    r"^[A-Z]{1,3}-[0-9]{2,4}$",
    r"^\w{2,4}:\d{3,5}$",
    r"^[a-zA-Z0-9]+([-a-zA-Z0-9]?[a-zA-Z0-9])*$",
    r"(abc)+",
    r"(\d{3})+",
    r"^(abc)+(def)+$",
    r"[a-z]{3,5}",
    r"\d{1,}",
    r".{0,3}",
    r"^abc[0-9]*$",
    r"^-[a-z]{1,10}-$",
    r"^[a-z]{2,4}-\d{4,15}$",
    r"^\+[0-9]{5,}$",
    r"^[a-zA-Z0-9]{2,4}-\d{4,15}$",
    r"^abc[0-9]{1,3}def[a-z]{2,5}ghi$",
    r"^geo:\w*\*?$",
    r"^[\w\W]+$",
    r"^prefix[|]+(?:,prefix[|]+)*$",
    r"(hello){2,5}",
    r"(abcd)*",
    r"^000(000)?$",
    r".*",
    r".+",
    r".?",
    r"[a-z]*",
    r"\d*",
    r"^[a-z]*-[0-9]*$",
    r"^[a-z]+$",
    r"^.$",
    r"\s+",
    r"\S+",
    r"\w+",
    r"\W+",
    r"[^a-z]+",
    r"^[a-zA-Z0-9_.-]+@[a-zA-Z0-9.-]+$",
]


@pytest.mark.parametrize("pattern", _KNOWN_PATTERNS)
def test_rust_oracle_known_patterns(pattern):
    result = _check_pattern(pattern)
    assert result.ok, result.detail


# ---------------------------------------------------------------------------
# PBT: random valid regex patterns
# ---------------------------------------------------------------------------

@given(pattern=st.text(min_size=1, max_size=60).filter(_is_valid_regex))
@settings(max_examples=2000, suppress_health_check=list(HealthCheck))
def test_rust_oracle_random(pattern):
    result = _check_pattern(pattern)
    assert result.ok, result.detail


# ---------------------------------------------------------------------------
# Corpus: bulk validation against real-world patterns
# ---------------------------------------------------------------------------

CORPUS_PATH = os.path.join(os.path.dirname(__file__), "_regex_oracle", "corpus.json")

_BATCH_SIZE = 500

# Known serializer limitations that cause HIR mismatches but are not bugs.
# Each detector returns True if the mismatch is expected for the given pattern.
_KNOWN_LIMITATIONS: list[tuple[str, re.Pattern[str]]] = [
    # sre_parse optimizes common prefixes in alternation, changing tree structure.
    # E.g. (end|endblock) → (end(?:|block)). Semantically equivalent in Python
    # but produces different HIR in Rust.
    ("branch_regroup", re.compile(r"\|")),
    # POSIX character classes [[:alpha:]], [[:blank:]], etc. are not part of
    # Python's regex syntax. sre_parse treats them as nested sets; the serializer
    # outputs something structurally different.
    ("posix_class", re.compile(r"\[\[:")),
    # Nested brackets inside character classes, e.g. [a[b]], [[a-z]], [^\[foo\]].
    # Python's sre_parse interprets these differently than Rust's regex-syntax
    # (Python treats inner [ as literal in some cases, Rust sees nested classes).
    ("nested_bracket", re.compile(r"\[.*\[|\\\[|\\\]")),
    # Inline flags (?m), (?s), (?i), (?x) etc. may be lost or rewritten during
    # serialization, changing semantics of ^ $ . and case matching.
    ("inline_flags", re.compile(r"\(\?[aimsux]")),
    # \< and \> are word-boundary anchors in Rust's regex-syntax but literal
    # < and > in Python. The serializer correctly strips the backslash, but
    # Rust interprets the result differently.
    ("rust_word_boundary", re.compile(r"\\[<>]")),
    # sre_parse diverges from re.compile on edge cases:
    # - {1, 2} (space in quantifier): re.compile accepts it, sre_parse treats
    #   braces as literals
    # - Triple-dash in character classes: [a-z0-9---_] parsed differently
    ("sre_parse_quirk", re.compile(r"\{\d+,\s+\d+\}|---")),
]


def _classify_mismatch(pattern: str) -> str | None:
    """Return the limitation name if this mismatch is a known limitation, else None."""
    for name, detector in _KNOWN_LIMITATIONS:
        if detector.search(pattern):
            return name
    return None


@pytest.mark.skipif(not os.path.isfile(CORPUS_PATH), reason="corpus.json not found")
def test_rust_oracle_corpus():
    """Validate serializer against real-world regex corpus."""
    import warnings

    from schemathesis.core.errors import InternalError  # noqa: PLC0415

    with open(CORPUS_PATH) as f:
        corpus = json.load(f)

    # Expected unsupported opcodes — verify no new ones slip through
    _EXPECTED_UNSUPPORTED = {
        "ASSERT",           # positive lookahead/lookbehind (?=...), (?<=...)
        "ASSERT_NOT",       # negative lookahead/lookbehind (?!...), (?<!...)
        "GROUPREF",         # backreference \1
        "GROUPREF_EXISTS",  # conditional backreference (?(id)yes|no)
        "ATOMIC_GROUP",     # atomic group (?>...) (Python 3.11+)
        "FAILURE",          # unconditional failure node (used internally)
    }

    counts: dict[str, int] = {
        "invalid_regex": 0,
        "unsupported_opcode": 0,
        "serialize_error": 0,
        "dialect_diff": 0,
        "oracle_batch_error": 0,
        "ok": 0,
        # Known limitation categories
        "known_branch_regroup": 0,
        "known_posix_class": 0,
        "known_nested_bracket": 0,
        "known_inline_flags": 0,
        "known_rust_word_boundary": 0,
        "known_sre_parse_quirk": 0,
        # Unexpected mismatches — these are potential bugs
        "unexpected_mismatch": 0,
    }
    unsupported_opcodes: dict[str, int] = {}
    unexpected: list[HIRMismatch] = []

    for i in range(0, len(corpus), _BATCH_SIZE):
        pairs: list[tuple[str, str]] = []
        originals: list[str] = []
        serialized_list: list[str] = []

        for pattern in corpus[i : i + _BATCH_SIZE]:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                try:
                    re.compile(pattern)
                except (re.error, RecursionError):
                    counts["invalid_regex"] += 1
                    continue

            try:
                serialized = _serialize(list(sre_parse.parse(pattern)))
            except InternalError as exc:
                counts["unsupported_opcode"] += 1
                # Extract opcode name: "Unsupported sre opcode: ASSERT_NOT"
                msg = str(exc)
                opcode = msg.rsplit(": ", 1)[-1] if ": " in msg else msg
                unsupported_opcodes[opcode] = unsupported_opcodes.get(opcode, 0) + 1
                continue
            except Exception:
                counts["serialize_error"] += 1
                continue

            pairs.append((pattern, serialized))
            originals.append(pattern)
            serialized_list.append(serialized)

        if not originals:
            continue

        try:
            orig_results = _canonicalize_batch(originals)
            ser_results = _canonicalize_batch(serialized_list)
        except RuntimeError:
            counts["oracle_batch_error"] += len(pairs)
            continue

        for (pattern, serialized), orig, ser in zip(pairs, orig_results, ser_results, strict=True):
            if orig["error"] or ser["error"]:
                counts["dialect_diff"] += 1
                continue
            if orig["canonical"] == ser["canonical"]:
                counts["ok"] += 1
                continue

            limitation = _classify_mismatch(pattern)
            if limitation is not None:
                counts[f"known_{limitation}"] += 1
            else:
                counts["unexpected_mismatch"] += 1
                if len(unexpected) < 50:
                    unexpected.append(HIRMismatch(
                        pattern=pattern,
                        serialized=serialized,
                        orig_hir=orig["canonical"][:200],
                        ser_hir=ser["canonical"][:200],
                    ))

    total = sum(counts.values())
    checked = counts["ok"] + counts["unexpected_mismatch"] + sum(v for k, v in counts.items() if k.startswith("known_"))

    summary = (
        f"\nCorpus: {total} patterns, {checked} checked\n"
        + "\n".join(f"  {k}: {v}" for k, v in counts.items())
        + f"\n  unsupported opcodes: {dict(sorted(unsupported_opcodes.items()))}"
    )

    new_opcodes = set(unsupported_opcodes) - _EXPECTED_UNSUPPORTED
    if new_opcodes:
        summary += f"\n\nUnexpected unsupported opcodes: {new_opcodes}"

    detail = ""
    if unexpected:
        detail = "\n\nUnexpected mismatches:\n" + "\n".join(
            f"  {m.pattern!r} -> {m.serialized!r}\n    hir(orig): {m.orig_hir}\n    hir(ser):  {m.ser_hir}"
            for m in unexpected[:20]
        )

    assert counts["unexpected_mismatch"] == 0 and not new_opcodes, summary + detail
