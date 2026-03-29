import json
import os
import pathlib
import re
import subprocess
import sys

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

try:
    import re._parser as sre_parse
except ImportError:
    import sre_parse

from schemathesis.core.errors import InternalError
from schemathesis.specs.openapi.patterns import _serialize

CURRENT_DIR = pathlib.Path(__file__).parent.absolute()
sys.path.append(str(CURRENT_DIR.parents[2]))

from corpus.tools import extract_regex_patterns, iter_all_corpus_files  # noqa: E402

ORACLE_BINARY = os.path.join(
    os.path.dirname(__file__), "_regex_oracle", "target", "release", "regex-oracle"
)

# Skip the entire module if the oracle binary hasn't been built
pytestmark = pytest.mark.skipif(
    not os.path.isfile(ORACLE_BINARY),
    reason="Rust regex-oracle binary not built. Run: cargo build --release in test/specs/openapi/_regex_oracle/",
)


def _canonicalize_batch(patterns: list[str]) -> list[dict]:
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


def _check_pattern(pattern):
    if any(seq in pattern for seq in _RUST_DIALECT_DIFFS):
        return

    try:
        serialized = _serialize(list(sre_parse.parse(pattern)))
    except InternalError:
        return

    [orig], [ser] = _canonicalize_batch([pattern]), _canonicalize_batch([serialized])

    if orig["error"] or ser["error"]:
        return

    assert orig["canonical"] == ser["canonical"], (
        f"HIR mismatch:\n"
        f"  original:   {pattern!r}\n"
        f"  serialized: {serialized!r}\n"
        f"  rust(orig): {orig['canonical'][:100]}\n"
        f"  rust(ser):  {ser['canonical'][:100]}"
    )


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
    _check_pattern(pattern)


@given(pattern=st.text(min_size=1, max_size=60).filter(_is_valid_regex))
@settings(max_examples=2000, suppress_health_check=list(HealthCheck), deadline=None)
def test_rust_oracle_random(pattern):
    _check_pattern(pattern)


# Known limitations: patterns matching these regexes may produce HIR mismatches
# due to Python/Rust dialect differences, not serializer bugs.
_KNOWN_LIMITATION_DETECTORS = [
    re.compile(r"\|"),                    # sre_parse branch regrouping
    re.compile(r"\[\[:"),                 # POSIX classes
    re.compile(r"\[.*\[|\\\[|\\\]"),      # nested brackets
    re.compile(r"\(\?[aimsux]"),          # inline flags
    re.compile(r"\\[<>]"),               # Rust word boundaries
    re.compile(r"\{\d+,\s+\d+\}|---"),   # sre_parse quirks
]


def _is_known_limitation(pattern: str) -> bool:
    return any(d.search(pattern) for d in _KNOWN_LIMITATION_DETECTORS)


def _corpus_patterns():
    corpus = extract_regex_patterns(schema for _, _, schema in iter_all_corpus_files())
    result = []
    for p in corpus:
        if not _is_valid_regex(p):
            continue
        if _is_known_limitation(p):
            result.append(pytest.param(p, marks=pytest.mark.xfail(reason="known limitation", strict=False)))
        else:
            result.append(p)
    return result


@pytest.mark.parametrize("pattern", _corpus_patterns())
def test_rust_oracle_corpus(pattern):
    _check_pattern(pattern)


_EXPECTED_UNSUPPORTED_OPCODES = {
    "ASSERT", "ASSERT_NOT", "GROUPREF",
    "GROUPREF_EXISTS", "ATOMIC_GROUP", "FAILURE",
}


def test_corpus_unsupported_opcodes():
    corpus = extract_regex_patterns(schema for _, _, schema in iter_all_corpus_files())
    found: set[str] = set()
    for pattern in corpus:
        if not _is_valid_regex(pattern):
            continue
        try:
            _serialize(list(sre_parse.parse(pattern)))
        except InternalError as exc:
            msg = str(exc)
            opcode = msg.rsplit(": ", 1)[-1] if ": " in msg else msg
            found.add(opcode)
    new = found - _EXPECTED_UNSUPPORTED_OPCODES
    assert not new, f"New unsupported opcodes: {new}"
