"""Opcode coverage tests for the regex AST serializer.

Verifies that the serializer handles all opcodes that sre_parse can produce,
and identifies which opcodes are intentionally unsupported.
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

SKIP_BEFORE_PY11 = pytest.mark.skipif(
    sys.version_info < (3, 11), reason="Possessive/atomic only available in Python 3.11+"
)


def _collect_opcodes(nodes) -> set[int]:
    """Recursively collect all opcodes from an sre_parse AST."""
    ops = set()
    for op, value in nodes:
        ops.add(op)
        if op in (sre.MAX_REPEAT, sre.MIN_REPEAT) or op == getattr(sre, "POSSESSIVE_REPEAT", -1):
            _, _, subpattern = value
            ops |= _collect_opcodes(subpattern)
        elif op == sre.SUBPATTERN:
            _, _, _, inner = value
            ops |= _collect_opcodes(inner)
        elif op == sre.BRANCH:
            _, alternatives = value
            for alt in alternatives:
                ops |= _collect_opcodes(alt)
        elif op == sre.IN:
            ops |= {item_op for item_op, _ in value}
        elif op in (sre.ASSERT, sre.ASSERT_NOT):
            _, inner = value
            ops |= _collect_opcodes(inner)
        elif op == getattr(sre, "ATOMIC_GROUP", -1):
            ops |= _collect_opcodes(value)
        elif op == sre.GROUPREF_EXISTS:
            _, yes_pattern, no_pattern = value
            ops |= _collect_opcodes(yes_pattern)
            if no_pattern:
                ops |= _collect_opcodes(no_pattern)
    return ops


# ---------------------------------------------------------------------------
# Supported opcodes: these must round-trip correctly
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("description", "pattern"),
    [
        # LITERAL
        ("ascii letter", "a"),
        ("digit", "0"),
        ("escaped metachar dot", r"\."),
        ("escaped metachar star", r"\*"),
        ("escaped metachar backslash", r"\\"),
        ("escaped metachar caret", r"\^"),
        ("escaped metachar dollar", r"\$"),
        ("escaped metachar pipe", r"\|"),
        ("escaped metachar paren open", r"\("),
        ("escaped metachar paren close", r"\)"),
        ("escaped metachar bracket open", r"\["),
        ("escaped metachar brace open", r"\{"),
        ("escaped metachar plus", r"\+"),
        ("escaped metachar question", r"\?"),
        # NOT_LITERAL
        ("not literal", r"[^a]"),
        # ANY
        ("dot", "."),
        # AT (anchors)
        ("caret", "^a"),
        ("dollar", "a$"),
        ("word boundary", r"\ba\b"),
        ("non-word boundary", r"\Ba\B"),
        ("beginning of string", r"\Aa"),
        ("end of string", r"a\Z"),
        # IN (character classes)
        ("char class simple", "[abc]"),
        ("char class range", "[a-z]"),
        ("char class negated", "[^abc]"),
        ("char class category", r"[\d]"),
        ("char class mixed", r"[a-z\d_]"),
        ("char class range+literal", "[a-zA-Z0-9]"),
        # CATEGORY (standalone via IN shorthand)
        ("digit shorthand", r"\d"),
        ("non-digit shorthand", r"\D"),
        ("word shorthand", r"\w"),
        ("non-word shorthand", r"\W"),
        ("space shorthand", r"\s"),
        ("non-space shorthand", r"\S"),
        # BRANCH
        ("alternation", "a|b"),
        ("multi alternation", "a|b|c"),
        ("alternation groups", "(a)|(b)"),
        # SUBPATTERN
        ("capturing group", "(abc)"),
        ("non-capturing group", "(?:abc)"),
        ("nested groups", "((a)(b))"),
        # MAX_REPEAT
        ("star", "a*"),
        ("plus", "a+"),
        ("question", "a?"),
        ("fixed repeat", "a{3}"),
        ("range repeat", "a{2,5}"),
        ("open repeat", "a{2,}"),
        ("group star", "(ab)*"),
        ("group plus", "(ab)+"),
        ("class star", "[a-z]*"),
        # MIN_REPEAT
        ("lazy star", "a*?"),
        ("lazy plus", "a+?"),
        ("lazy question", "a??"),
        ("lazy range", "a{2,5}?"),
        # Combinations
        ("anchored class repeat", "^[a-z]+$"),
        ("concat literals", "abc"),
        ("complex", r"^[A-Z]{2}\d{3}-[a-z]+$"),
        ("branch in group", "(a|b)+"),
        ("nested repeat", r"((\d{2})+)"),
    ],
)
def test_supported_opcode_roundtrip(description, pattern):
    """Each supported opcode round-trips through serialize correctly."""
    parsed = sre_parse.parse(pattern)
    serialized = _serialize(list(parsed))

    # Must compile
    re.compile(serialized)

    # Must be idempotent
    serialized2 = _serialize(list(sre_parse.parse(serialized)))
    assert serialized == serialized2, f"Not idempotent: {pattern!r} → {serialized!r} → {serialized2!r}"

    # Must be semantically equivalent (spot check)
    test_strings = ["", "a", "abc", "ABC", "123", "a-b", "hello world", "\t\n", "\x00"]
    for s in test_strings:
        assert bool(re.search(pattern, s)) == bool(re.search(serialized, s)), (
            f"Semantic mismatch for {description!r} on {s!r}: {pattern!r} vs {serialized!r}"
        )


@pytest.mark.parametrize(
    ("description", "pattern"),
    [
        pytest.param("possessive star", "a*+", marks=SKIP_BEFORE_PY11),
        pytest.param("possessive plus", "a++", marks=SKIP_BEFORE_PY11),
        pytest.param("possessive range", "a{2,5}+", marks=SKIP_BEFORE_PY11),
    ],
)
def test_py311_opcodes(description, pattern):
    """Python 3.11+ opcodes (POSSESSIVE_REPEAT)."""
    parsed = sre_parse.parse(pattern)
    serialized = _serialize(list(parsed))
    re.compile(serialized)
    serialized2 = _serialize(list(sre_parse.parse(serialized)))
    assert serialized == serialized2


# ---------------------------------------------------------------------------
# Unsupported opcodes: serializer should raise InternalError
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("description", "pattern"),
    [
        ("positive lookahead", r"(?=foo)bar"),
        ("negative lookahead", r"(?!foo)bar"),
        ("positive lookbehind", r"(?<=foo)bar"),
        ("negative lookbehind", r"(?<!foo)bar"),
        ("backreference", r"(foo)\1"),
        ("conditional backref", r"(x)(?(1)a|b)"),
        pytest.param("atomic group", "(?>abc)", marks=SKIP_BEFORE_PY11),
    ],
)
def test_unsupported_opcodes_raise(description, pattern):
    """Unsupported opcodes should raise InternalError, not silently produce wrong output."""
    parsed = sre_parse.parse(pattern)
    with pytest.raises(InternalError, match="Unsupported sre opcode"):
        _serialize(list(parsed))


# ---------------------------------------------------------------------------
# Fuzzing for undiscovered unsupported opcodes
# ---------------------------------------------------------------------------

def _is_valid_regex(pattern: str) -> bool:
    try:
        re.compile(pattern)
        return True
    except (re.error, RecursionError):
        return False


@given(pattern=st.text(min_size=1, max_size=60).filter(_is_valid_regex))
@settings(max_examples=10000, suppress_health_check=list(HealthCheck))
def test_no_unhandled_opcodes(pattern):
    """No valid regex should cause an unexpected exception (only InternalError for unsupported opcodes)."""
    parsed = sre_parse.parse(pattern)
    try:
        _serialize(list(parsed))
    except InternalError:
        pass  # expected for unsupported opcodes
