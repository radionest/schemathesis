"""Differential testing: Python serializer vs Rust regex-syntax HIR canonicalization.

Uses a Rust binary (regex-oracle) that parses regex patterns with `regex-syntax`
and returns their canonical HIR form (with capture groups stripped).

The invariant: for any pattern P, the Rust canonical form of P must equal the
Rust canonical form of serialize(parse(P)). If they differ, our serializer
changed the pattern's semantics.
"""

import json
import os
import subprocess

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

try:
    import re._parser as sre_parse
except ImportError:
    import sre_parse

from schemathesis.specs.openapi.patterns import _serialize

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
    import re

    try:
        re.compile(pattern)
        return True
    except (re.error, RecursionError):
        return False


# Patterns that Rust's regex crate interprets differently than Python's re:
# \< and \> are word boundary start/end in Rust, but literal < > in Python
_RUST_DIALECT_DIFFS = {"\\<", "\\>"}


def _check_pattern(pattern: str) -> tuple[bool, str]:
    """Check one pattern: serialize(parse(P)) must have same Rust HIR as P.

    Returns (ok, detail).
    """
    from schemathesis.core.errors import InternalError

    if any(seq in pattern for seq in _RUST_DIALECT_DIFFS):
        return True, "Rust dialect difference"

    try:
        serialized = _serialize(list(sre_parse.parse(pattern)))
    except InternalError:
        return True, "unsupported opcode"

    results = _canonicalize_batch([pattern, serialized])
    orig, ser = results[0], results[1]

    if orig["error"] or ser["error"]:
        # Dialect differences between Python and Rust regex engines
        # (e.g. \< is word boundary in Rust but literal '<' in Python,
        # \Z is unsupported in Rust, POSIX classes differ).
        # Skip patterns that either engine can't parse.
        return True, f"Rust parse difference: {orig.get('error') or ser.get('error')}"

    if orig["canonical"] == ser["canonical"]:
        return True, "OK"

    return False, (
        f"HIR mismatch:\n"
        f"  original:   {pattern!r}\n"
        f"  serialized: {serialized!r}\n"
        f"  rust(orig): {orig['canonical'][:100]}\n"
        f"  rust(ser):  {ser['canonical'][:100]}"
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
    ok, detail = _check_pattern(pattern)
    assert ok, detail


# ---------------------------------------------------------------------------
# PBT: random valid regex patterns
# ---------------------------------------------------------------------------

@given(pattern=st.text(min_size=1, max_size=60).filter(_is_valid_regex))
@settings(max_examples=2000, suppress_health_check=list(HealthCheck))
def test_rust_oracle_random(pattern):
    ok, detail = _check_pattern(pattern)
    assert ok, detail
