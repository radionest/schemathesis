import re

import pytest

from schemathesis.specs.openapi.patterns import update_quantifier


@pytest.mark.parametrize(
    ("pattern", "min_length", "max_length", "expected"),
    [
        # multi-char inner length
        #
        # Old _measure_inner_length re-parsed inner string and returned 1
        # for anything non-literal (character classes, \d, nested quantifiers).
        (r"(\d{3})+", 6, 9, r"(\d{3}){2,3}"),
        (r"(\d{3})+", 6, 6, r"(\d{3}){2}"),
        (r"(\d{3})+", 3, 3, r"(\d{3}){1}"),
        (r"([A-Z]{2})+", 4, 10, r"([A-Z]{2}){2,5}"),
        (r"([A-Z]\d)+", 4, 8, r"([A-Z]\d){2,4}"),
        (r"(\w{3})+", 9, 15, r"(\w{3}){3,5}"),
        (r"(\d{2}){1,5}", 4, 8, r"(\d{2}){2,4}"),
        # double division in anchored multi-part
        #
        # Old code passed DP distribution counts through _handle_repeat_quantifier
        # which divided by inner_length again.
        (r"^(abc)+(def)+$", 6, 6, r"^(abc){1}(def){1}$"),
        (r"^(abc)+(def)+$", 9, 9, r"^(abc){1}(def){2}$"),
        (r"^(abc)+(def)+$", 12, 12, r"^(abc){1}(def){3}$"),
        (r"^(ab)+(cd)+(ef)+$", 6, 6, r"^(ab){1}(cd){1}(ef){1}$"),
        (r"^(ab)+(cd)+(ef)+$", 10, 10, r"^(ab){1}(cd){1}(ef){3}$"),
        # Multi-char quantified inner in anchored multi-part patterns.
        # Tests both bugs at once.
        (r"^(\d{3})+(\w{2})+$", 10, 10, r"^(\d{3}){2}(\w{2}){2}$"),
        (r"^(\d{3})+(\w{2})+$", 7, 7, r"^(\d{3}){1}(\w{2}){2}$"),
        # Old _distribute_length_range accepted repetition_lengths but never
        # used it. All parts treated as inner_length=1.
        (r"^(abc)+\d+$", 4, 10, r"^(abc){2,3}\d{1}$"),
        (r"^(abc)+(\d)+$", 7, 7, r"^(abc){1}(\d){4}$"),
        # Edge cases
        # inner_length > max_length -> noop
        (r"(\d{3})+", 1, 2, r"(\d{3})+"),
        # existing bounds further constrained by length
        (r"(\d{2}){3,7}", 8, 12, r"(\d{2}){4,6}"),
        # multi-char inner with fixed prefix
        (r"^abc(\d{3})+$", 6, 12, r"^abc(\d{3}){1,3}$"),
        # min-only constraint
        (r"(\d{3})+", 9, None, r"(\d{3}){3,}"),
        # max-only constraint
        (r"(\d{3})+", None, 9, r"(\d{3}){1,3}"),
        # Classifier returns "unknown" for multi-node patterns without anchors.
        # These are returned unchanged.
        (r"[a-z][a-z0-9]*", 3, 10, r"[a-z][a-z0-9]{2,9}"),
        (r"[a-z][a-z0-9]+", 5, 20, r"[a-z][a-z0-9]{4,19}"),
    ],
)
def test_update_quantifier_bugs(pattern, min_length, max_length, expected):
    assert update_quantifier(pattern, min_length, max_length) == expected
    re.compile(expected)
