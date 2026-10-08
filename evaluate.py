import json, platform, statistics, tempfile, time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent

from edi import analyze, classify_request

cases = json.loads((ROOT / "samples/intents.json").read_text())
results = []
latencies = []
for c in cases:
    start = time.perf_counter()
    actual = classify_request(c["text"])["intent"]
    latencies.append((time.perf_counter() - start) * 1000)
    results.append({**c, "actual": actual, "correct": actual == c["expected"]})
# Seeded parser checks with explicit expected errors.
valid = (ROOT / "samples/valid.edi").read_text()
variants = [
    ("valid", valid, set()),
    (
        "invalid calendar date",
        valid.replace("20261101", "20260230"),
        {"INVALID_DATE", "MISSING_BEGIN"},
    ),
    ("missing ID", valid.replace("REF*0F*SYN001", "REF*0F*"), {"MISSING_MEMBER_ID"}),
    ("wrong count", valid.replace("SE*8", "SE*9"), {"SEGMENT_COUNT"}),
    ("control mismatch", valid.replace("SE*8*0001", "SE*8*9999"), {"CONTROL_NUMBER"}),
    ("missing start date", valid.replace("DTP*348", "DTP*349"), {"MISSING_BEGIN"}),
]
checks = []
tp = fp = fn = 0
for name, text, expected in variants:
    actual = {f["code"] for f in analyze(text)["findings"]}
    tp += len(actual & expected)
    fp += len(actual - expected)
    fn += len(expected - actual)
    checks.append(
        {"case": name, "expected": sorted(expected), "actual": sorted(actual)}
    )
report = {
    "run_utc": datetime.now(timezone.utc).isoformat(),
    "python": platform.python_version(),
    "dataset": "18 authored keyword intent examples + 6 seeded parser cases; no held-out or production data",
    "intent_accuracy": sum(c["correct"] for c in results) / len(results),
    "intent_cases": results,
    "parser_cases": checks,
    "rule_true_positives": tp,
    "rule_false_positives": fp,
    "rule_false_negatives": fn,
    "intent_p50_ms": round(statistics.median(latencies), 3),
    "limitations": "Small synthetic regression set. Keyword baseline, not a trained NLP model or full X12 validator.",
}
(ROOT / "docs/evaluation.json").write_text(json.dumps(report, indent=2) + "\n")
print(
    json.dumps(
        {k: v for k, v in report.items() if k not in {"intent_cases", "parser_cases"}},
        indent=2,
    )
)
