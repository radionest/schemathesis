"""Benchmark for update_quantifier: old (string-surgery) vs new (AST transform + serialize).

Run on each branch to compare:
    uv run python test/specs/openapi/bench_patterns.py

Clears lru_cache between iterations to measure cold-call performance.
"""

import statistics
import time

from schemathesis.specs.openapi.patterns import update_quantifier

# Representative patterns from real-world OpenAPI schemas and test suite
CASES = [
    # Simple: single quantifier
    (".*", 1, 3),
    (".+", 1, 3),
    ("[a-z]*", 3, 5),
    (r"\d*", 1, None),
    ("a", 3, 3),
    # Anchored single
    ("^[a-z]*$", 3, 5),
    ("^.+$", 0, 5),
    ("^[a-z]+$", 0, 5),
    # Anchored multi-part
    ("^abc[0-9]*$", None, 5),
    (r"^[a-z]{2,4}-\d{4,15}$", 7, 7),
    (r"^[a-z]{2,4}-\d{4,15}$", 20, 20),
    ("^[A-Z]{1,3}-[0-9]{2,4}-[a-z]{1,5}$", 8, 8),
    (r"^\w{2,4}:\d{3,5}:[A-F]{1,2}$", 10, 10),
    (r"^[a-zA-Z0-9]{2,4}-\d{4,15}$", 19, 19),
    # Complex / real-world
    (r"^[a-zA-Z0-9]+([-a-zA-Z0-9]?[a-zA-Z0-9])*$", 5, 64),
    (r"^\+[0-9]{5,}$", 6, 6),
    (r"^[+][\s0-9()-]+$", 1, 20),
    ("^abc[0-9]{1,3}def[a-z]{2,5}ghi$", 12, 12),
    ("^(((?:DB|BR)[-a-zA-Z0-9_]+),?){1,}$", None, 6000),
    (r"^geo:\w*\*?$", 5, 200),
    (r"^[\w\W]+$", 1, 3),
    (r"^prefix[|]+(?:,prefix[|]+)*$", 4000, 4000),
    (r"^bar\.spam\.[^,]+(?:,bar\.spam\.[^,]+)*$", 10, 10),
    # Multi-char inner (bug B patterns)
    (r"(\d{3})+", 6, 9),
    ("(abc)+", 1, 10),
    ("(hello){2,5}", None, 12),
    # Noop cases (returned unchanged)
    ("abc*def*", 1, 3),
    ("b{30,35}", 1, 3),
    (r"^abcd[a-zA-Z0-9]{2,4}$", 5, 1),
    # Large schema pattern
    (
        r"^[a-z0-9]+((\.|-|__|-+)[a-z0-9]+)*(\/[a-z0-9]+((\.|-|__|-+)[a-z0-9]+)*)*(:[a-zA-Z0-9_][a-zA-Z0-9._-]{0,127}|@sha256:[a-fA-F0-9]{64}){0,1}$",
        None,
        500,
    ),
]

ROUNDS = 200
WARMUP = 20


def bench_once():
    """Run all cases once, returning total time in µs."""
    update_quantifier.cache_clear()
    t0 = time.perf_counter_ns()
    for pattern, min_l, max_l in CASES:
        update_quantifier(pattern, min_l, max_l)
    return (time.perf_counter_ns() - t0) / 1000  # ns → µs


def main():
    # Warmup
    for _ in range(WARMUP):
        bench_once()

    # Measure
    times = [bench_once() for _ in range(ROUNDS)]

    p50 = statistics.median(times)
    p95 = sorted(times)[int(ROUNDS * 0.95)]
    p99 = sorted(times)[int(ROUNDS * 0.99)]
    mean = statistics.mean(times)
    stdev = statistics.stdev(times)
    per_call = mean / len(CASES)

    print(f"Patterns: {len(CASES)}")
    print(f"Rounds:   {ROUNDS}")
    print()
    print(f"Total per round:")
    print(f"  mean:  {mean:8.1f} µs  (stdev {stdev:.1f})")
    print(f"  p50:   {p50:8.1f} µs")
    print(f"  p95:   {p95:8.1f} µs")
    print(f"  p99:   {p99:8.1f} µs")
    print()
    print(f"Per call: {per_call:6.1f} µs")
    print()

    # Per-pattern breakdown (top 5 slowest)
    print("Top 5 slowest patterns:")
    pattern_times = []
    for pattern, min_l, max_l in CASES:
        update_quantifier.cache_clear()
        t0 = time.perf_counter_ns()
        for _ in range(100):
            update_quantifier.cache_clear()
            update_quantifier(pattern, min_l, max_l)
        elapsed = (time.perf_counter_ns() - t0) / 1000 / 100  # µs per call
        pattern_times.append((elapsed, pattern, min_l, max_l))

    pattern_times.sort(reverse=True)
    for elapsed, pattern, min_l, max_l in pattern_times[:5]:
        display = pattern if len(pattern) <= 60 else pattern[:57] + "..."
        print(f"  {elapsed:7.1f} µs  ({min_l}, {max_l})  {display}")


if __name__ == "__main__":
    main()
