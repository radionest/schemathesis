import re
import string

import pytest

try:
    import re._parser as sre_parse
except ImportError:
    import sre_parse

from schemathesis.specs.openapi.patterns import _serialize

# Every regex metacharacter: must be escaped to match literally
_REGEX_METACHARACTERS = list(r"\.^$*+?{[|()")


@pytest.mark.parametrize("char", _REGEX_METACHARACTERS)
def test_literal_metachar_escaped(char):
    pattern = re.escape(char)
    parsed = sre_parse.parse(pattern)
    serialized = _serialize(list(parsed))
    assert re.fullmatch(serialized, char), (
        f"Escaped metachar {char!r}: serialized={serialized!r} does not match"
    )


@pytest.mark.parametrize("char", list(string.printable))
def test_literal_printable_roundtrip(char):
    pattern = re.escape(char)
    parsed = sre_parse.parse(pattern)
    serialized = _serialize(list(parsed))
    compiled = re.compile(serialized)
    assert compiled.fullmatch(char), (
        f"Printable char {char!r} (ord={ord(char)}): serialized={serialized!r} does not match"
    )


# Control characters and non-printable bytes
_CONTROL_CHARS = [
    ("\x00", "null"),
    ("\x01", "SOH"),
    ("\x07", "BEL"),
    ("\x08", "BS"),
    ("\t", "tab"),
    ("\n", "newline"),
    ("\r", "carriage return"),
    ("\x1b", "ESC"),
    ("\x7f", "DEL"),
    ("\x80", "high byte 0x80"),
    ("\xff", "high byte 0xff"),
]


@pytest.mark.parametrize("char,name", _CONTROL_CHARS)
def test_literal_control_char(char, name):
    pattern = re.escape(char)
    parsed = sre_parse.parse(pattern)
    serialized = _serialize(list(parsed))
    compiled = re.compile(serialized)
    assert compiled.fullmatch(char), (
        f"Control char {name} ({char!r}): serialized={serialized!r} does not match"
    )


# Unicode characters
_UNICODE_CHARS = [
    ("\u00e9", "e-acute"),
    ("\u00f1", "n-tilde"),
    ("\u0100", "A-macron"),
    ("\u4e2d", "CJK"),
    ("\U0001f600", "emoji"),
]


@pytest.mark.parametrize("char,name", _UNICODE_CHARS)
def test_literal_unicode_char(char, name):
    pattern = re.escape(char)
    parsed = sre_parse.parse(pattern)
    serialized = _serialize(list(parsed))
    compiled = re.compile(serialized)
    assert compiled.fullmatch(char), (
        f"Unicode char {name} ({char!r}): serialized={serialized!r} does not match"
    )


# Characters that are special inside character classes
_CLASS_SPECIAL_CHARS = [
    ("]", "close bracket"),
    ("^", "caret"),
    ("[", "open bracket"),
    ("\\", "backslash"),
    ("-", "hyphen"),
]


@pytest.mark.parametrize("char,name", _CLASS_SPECIAL_CHARS)
def test_class_special_char(char, name):
    # Build a character class containing just this char
    pattern = f"[{re.escape(char)}]"
    try:
        parsed = sre_parse.parse(pattern)
    except re.error:
        pytest.skip(f"Cannot parse [{re.escape(char)}]")
    serialized = _serialize(list(parsed))
    compiled = re.compile(serialized)
    assert compiled.fullmatch(char), (
        f"Class special char {name} ({char!r}): serialized={serialized!r} does not match"
    )


@pytest.mark.parametrize("char", _REGEX_METACHARACTERS)
def test_class_metachar_inside_class(char):
    pattern = f"[{re.escape(char)}]"
    try:
        parsed = sre_parse.parse(pattern)
    except re.error:
        pytest.skip(f"Cannot parse class pattern for {char!r}")
    serialized = _serialize(list(parsed))
    compiled = re.compile(serialized)
    assert compiled.fullmatch(char), (
        f"Metachar {char!r} inside class: serialized={serialized!r} does not match"
    )


@pytest.mark.parametrize(
    ("pattern", "should_match", "should_not_match"),
    [
        ("[^a]", "b", "a"),
        ("[^0-9]", "a", "5"),
        (r"[^\d]", "a", "5"),
        ("[^a-z]", "A", "a"),
    ],
)
def test_negated_class(pattern, should_match, should_not_match):
    parsed = sre_parse.parse(pattern)
    serialized = _serialize(list(parsed))
    compiled = re.compile(serialized)
    assert compiled.fullmatch(should_match), f"{serialized!r} should match {should_match!r}"
    assert not compiled.fullmatch(should_not_match), f"{serialized!r} should not match {should_not_match!r}"


@pytest.mark.parametrize(
    ("pattern", "samples_in", "samples_out"),
    [
        ("[a-z]", list("abcxyz"), list("ABC019")),
        ("[A-Z]", list("ABCXYZ"), list("abc019")),
        ("[0-9]", list("0159"), list("abcABC")),
        ("[a-zA-Z0-9]", list("aZ0"), list("!@# ")),
        ("[a-z0-9_-]", list("a0_-"), list("!@A")),
    ],
)
def test_range_in_class(pattern, samples_in, samples_out):
    parsed = sre_parse.parse(pattern)
    serialized = _serialize(list(parsed))
    compiled = re.compile(serialized)
    for ch in samples_in:
        assert compiled.fullmatch(ch), f"{serialized!r} should match {ch!r}"
    for ch in samples_out:
        assert not compiled.fullmatch(ch), f"{serialized!r} should not match {ch!r}"


@pytest.mark.parametrize(
    ("pattern", "should_match", "should_not_match"),
    [
        (r"[^a]", "b", "a"),
        (r"[^Z]", "a", "Z"),
        (r"[^0]", "a", "0"),
    ],
)
def test_not_literal(pattern, should_match, should_not_match):
    parsed = sre_parse.parse(pattern)
    serialized = _serialize(list(parsed))
    compiled = re.compile(serialized)
    assert compiled.fullmatch(should_match)
    assert not compiled.fullmatch(should_not_match)


@pytest.mark.parametrize(
    ("pattern", "should_match"),
    [
        (r"a\.b", "a.b"),
        (r"a\*b", "a*b"),
        (r"a\+b", "a+b"),
        (r"a\?b", "a?b"),
        (r"\(x\)", "(x)"),
        (r"\[x\]", "[x]"),
        (r"a\\b", "a\\b"),
        (r"\^\$", "^$"),
    ],
)
def test_escaped_in_context(pattern, should_match):
    parsed = sre_parse.parse(pattern)
    serialized = _serialize(list(parsed))
    compiled = re.compile(serialized)
    assert compiled.fullmatch(should_match), (
        f"pattern={pattern!r} serialized={serialized!r} should match {should_match!r}"
    )
