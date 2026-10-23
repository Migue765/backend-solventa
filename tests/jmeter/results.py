"""Fail-closed JTL evaluation, including failed assertions despite JMeter exit code zero."""

import csv
import math
from collections import defaultdict
from pathlib import Path

from tests.jmeter.plans import PRODUCTS


def percentile(values: list[int], percent: float) -> int:
    if not values:
        raise ValueError("Cannot compute a percentile without samples.")
    return sorted(values)[math.ceil(len(values) * percent) - 1]


def evaluate(
    path: Path,
    expected: int,
    performance: bool = False,
    required_labels: tuple[str, ...] = (),
) -> dict[str, object]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows or not {"label", "success", "elapsed"} <= rows[0].keys():
        raise ValueError("Missing or malformed JMeter samples.")
    failures = [row["label"] for row in rows if row["success"] != "true"]
    groups: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        if row["label"].startswith("journey."):
            groups[row["label"]].append(int(row["elapsed"]))
    errors = [f"Failed assertion/sample: {label}" for label in failures]
    for label in required_labels:
        if sum(row["label"] == label for row in rows) != 1:
            errors.append(f"Missing or duplicated required sample: {label}")
    metrics: dict[str, object] = {}
    if expected:
        for label in (
            *(f"journey.quote.{product}" for product in PRODUCTS),
            *(f"journey.emission.{product}" for product in PRODUCTS),
            "journey.dashboard",
        ):
            if len(groups[label]) != expected:
                errors.append(f"{label}: expected {expected}, observed {len(groups[label])}")
    for label, values in groups.items():
        if not values:
            continue
        p95, p99 = percentile(values, 0.95), percentile(values, 0.99)
        metrics[label] = {"samples": len(values), "p95_ms": p95, "p99_ms": p99}
        if performance:
            if label.startswith("journey.emission."):
                limit95, limit99 = 1500, 3000
            elif label == "journey.quote.mortgage-life":
                limit95, limit99 = 400, 800
            elif label == "journey.dashboard":
                limit95, limit99 = 150, 300
            else:
                limit95, limit99 = 250, 500
            if len(values) < 20:
                errors.append(f"{label}: at least 20 samples required for latency gate")
            if p95 > limit95 or p99 > limit99:
                errors.append(f"{label}: latency budget exceeded ({limit95}/{limit99} ms)")
    offered = sum(
        len(values) for label, values in groups.items() if label.startswith("journey.quote.")
    )
    completed = sum(
        1 for row in rows if row["label"].startswith("journey.quote.") and row["success"] == "true"
    )
    creations = [row for row in rows if row["label"].startswith("quote.create.")]
    accepted = sum(row.get("responseCode") == "201" for row in creations)
    rejected = sum(row.get("responseCode", "").startswith("4") for row in creations)
    return {
        "passed": not errors,
        "samples": len(rows),
        "errors": errors,
        "latency": metrics,
        "quote_journeys_offered": offered,
        "quote_journeys_completed": completed,
        "quote_journeys_failed": offered - completed,
        "quote_requests_accepted_http201": accepted,
        "quote_requests_rejected_http4xx": rejected,
        "quote_requests_technical_or_unknown": len(creations) - accepted - rejected,
        "scope": "Local SQLite; real services; academic providers/payment/policy; not ASR proof",
        "latency_gate": performance,
    }
