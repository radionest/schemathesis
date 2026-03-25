"""Round-trip property-based tests for the regex AST serializer.

Verifies that parse → serialize produces semantically equivalent patterns.
"""

import re
import sys

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

try:
    import re._constants as sre
    import re._parser as sre_parse
except ImportError:
    import sre_constants as sre
    import sre_parse

from schemathesis.core.errors import InternalError
from schemathesis.specs.openapi.patterns import _serialize


def _is_valid_regex(pattern: str) -> bool:
    try:
        re.compile(pattern)
        return True
    except (re.error, RecursionError):
        return False


# Strategy: random text that happens to be valid regex
_random_regex = st.text(min_size=1, max_size=80).filter(_is_valid_regex)

# Strategy: structurally generated regex fragments for better coverage
_atoms = st.sampled_from([
    ".", r"\d", r"\D", r"\w", r"\W", r"\s", r"\S",
    "a", "Z", "0", " ", r"\.", r"\*", r"\+", r"\?",
    r"\t", r"\n", r"\r", r"\x00", r"\xff",
    "[a-z]", "[A-Z0-9]", "[^abc]", r"[\d\w]", r"[\s\S]",
    "[.]", r"[\^]", r"[\]]", "[-a]", "[a-]",
])

_quantifiers = st.sampled_from(["", "*", "+", "?", "{2}", "{1,3}", "{2,}", "*?", "+?", "??"])


@st.composite
def _structured_regex(draw):
    """Build a regex from parts for better opcode coverage."""
    n_parts = draw(st.integers(min_value=1, max_value=4))
    parts = []
    for _ in range(n_parts):
        atom = draw(_atoms)
        quant = draw(_quantifiers)
        # Wrap in group sometimes
        if draw(st.booleans()):
            atom = f"({atom})"
        parts.append(atom + quant)

    body = "".join(parts)

    # Optionally add anchors
    if draw(st.booleans()):
        body = "^" + body
    if draw(st.booleans()):
        body = body + "$"

    # Optionally add alternation
    if draw(st.booleans()):
        alt = draw(_atoms)
        body = f"{body}|{alt}"

    assume(_is_valid_regex(body))
    return body


@given(pattern=_random_regex)
@settings(max_examples=5000, suppress_health_check=list(HealthCheck))
def test_roundtrip_random_text(pattern):
    """Random valid regex strings: parse → serialize → compile succeeds."""
    parsed = sre_parse.parse(pattern)
    try:
        serialized = _serialize(list(parsed))
    except InternalError:
        assume(False)

    re.compile(serialized)


@given(pattern=_random_regex)
@settings(max_examples=3000, suppress_health_check=list(HealthCheck))
def test_roundtrip_idempotent(pattern):
    """serialize(parse(serialize(parse(p)))) == serialize(parse(p))."""
    parsed = sre_parse.parse(pattern)
    try:
        s1 = _serialize(list(parsed))
    except InternalError:
        assume(False)

    s2 = _serialize(list(sre_parse.parse(s1)))
    assert s1 == s2, f"Not idempotent: {pattern!r} → {s1!r} → {s2!r}"


@given(data=st.data())
@settings(max_examples=3000, suppress_health_check=list(HealthCheck))
def test_roundtrip_semantic_equivalence(data):
    """Serialized pattern matches the same strings as the original."""
    pattern = data.draw(_random_regex)
    parsed = sre_parse.parse(pattern)
    try:
        serialized = _serialize(list(parsed))
    except InternalError:
        assume(False)

    test_string = data.draw(st.text(max_size=30))
    original_match = bool(re.search(pattern, test_string))
    serialized_match = bool(re.search(serialized, test_string))
    assert original_match == serialized_match, (
        f"Semantic mismatch on {test_string!r}: "
        f"pattern={pattern!r} ({original_match}) vs serialized={serialized!r} ({serialized_match})"
    )


@given(pattern=_structured_regex())
@settings(max_examples=3000, suppress_health_check=list(HealthCheck))
def test_roundtrip_structured(pattern):
    """Structurally generated regex: parse → serialize → compile and idempotent."""
    parsed = sre_parse.parse(pattern)
    try:
        s1 = _serialize(list(parsed))
    except InternalError:
        assume(False)

    re.compile(s1)
    s2 = _serialize(list(sre_parse.parse(s1)))
    assert s1 == s2, f"Not idempotent: {pattern!r} → {s1!r} → {s2!r}"


@given(data=st.data())
@settings(max_examples=2000, suppress_health_check=list(HealthCheck))
def test_roundtrip_structured_semantic(data):
    """Structurally generated regex: semantic equivalence on random strings."""
    pattern = data.draw(_structured_regex())
    parsed = sre_parse.parse(pattern)
    try:
        serialized = _serialize(list(parsed))
    except InternalError:
        assume(False)

    test_string = data.draw(st.text(max_size=30))
    original_match = bool(re.search(pattern, test_string))
    serialized_match = bool(re.search(serialized, test_string))
    assert original_match == serialized_match, (
        f"Semantic mismatch on {test_string!r}: "
        f"pattern={pattern!r} ({original_match}) vs serialized={serialized!r} ({serialized_match})"
    )
