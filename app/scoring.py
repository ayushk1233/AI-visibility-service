from __future__ import annotations

from math import sqrt
from typing import Any, Iterable

DEFAULT_WEIGHTS = {"inclusion": 0.40, "recommendation": 0.40, "non_negative": 0.20}
Z_95 = 1.96


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def wilson_interval(successes: int, n: int, z: float = Z_95) -> tuple[float | None, float | None]:
    if n <= 0:
        return None, None
    p = successes / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    margin = z * sqrt((p * (1 - p) + z * z / (4 * n)) / n) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(value, digits)


def _group(rows: list[dict[str, Any]], eligible: int, weights: dict[str, float]) -> dict[str, Any]:
    succeeded = [row for row in rows if row.get("status") == "succeeded"]
    n = len(succeeded)
    failed = sum(1 for row in rows if row.get("status") == "failed")
    skipped = sum(1 for row in rows if row.get("status") == "skipped")

    present = sum(1 for row in succeeded if row.get("labels", {}).get("kasparro_present"))
    recommended = sum(1 for row in succeeded if row.get("labels", {}).get("recommended"))
    negative = sum(1 for row in succeeded if row.get("labels", {}).get("negatively_characterised"))
    with_citations = sum(1 for row in succeeded if (row.get("citation_count") or 0) > 0)
    positions = [
        row["labels"].get("recommendation_position")
        for row in succeeded
        if row.get("labels", {}).get("recommendation_position") is not None
    ]

    inclusion_rate = _ratio(present, n)
    recommendation_rate = _ratio(recommended, n)
    negative_rate = _ratio(negative, n)
    visibility_index = None
    if n > 0:
        visibility_index = round(
            100
            * (
                weights["inclusion"] * (inclusion_rate or 0)
                + weights["recommendation"] * (recommendation_rate or 0)
                + weights["non_negative"] * (1 - (negative_rate or 0))
            ),
            2,
        )

    inclusion_ci = wilson_interval(present, n)
    recommendation_ci = wilson_interval(recommended, n)
    negative_ci = wilson_interval(negative, n)

    def component(numerator: int, weight: float) -> dict[str, Any]:
        rate = _ratio(numerator, n)
        return {
            "numerator": numerator,
            "denominator": n,
            "rate": _round(rate),
            "weight": weight,
            "weighted_contribution": _round((rate or 0) * weight if n else None),
        }

    return {
        "eligible": eligible,
        "n": n,
        "failed": failed,
        "skipped": skipped,
        "coverage": _round(_ratio(n, eligible)),
        "label_counts": {
            "present": present,
            "recommended": recommended,
            "negative": negative,
        },
        "components": {
            "inclusion": component(present, weights["inclusion"]),
            "recommendation": component(recommended, weights["recommendation"]),
            "non_negative": component(n - negative, weights["non_negative"]),
        },
        "inclusion_rate": _round(inclusion_rate),
        "recommendation_rate": _round(recommendation_rate),
        "negative_rate": _round(negative_rate),
        "visibility_index": visibility_index,
        "intervals": {
            "inclusion": [_round(inclusion_ci[0]), _round(inclusion_ci[1])],
            "recommendation": [_round(recommendation_ci[0]), _round(recommendation_ci[1])],
            "negative": [_round(negative_ci[0]), _round(negative_ci[1])],
        },
        "citation_presence": {
            "count": with_citations,
            "rate": _round(_ratio(with_citations, n)),
        },
        "position_distribution": {
            "count": len(positions),
            "positions": {str(position): positions.count(position) for position in sorted(set(positions))},
        },
    }


def score_rows(
    rows: list[dict[str, Any]],
    eligible: int | None = None,
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    weights = dict(weights or DEFAULT_WEIGHTS)
    if abs(sum(weights.values()) - 1.0) > 1e-9:
        raise ValueError("Scoring weights must sum to 1.0")
    total = eligible if eligible is not None else len(rows)
    overall = _group(rows, total, weights)
    overall["weights"] = weights

    engines: dict[str, list[dict[str, Any]]] = {}
    categories: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        engines.setdefault(row.get("engine", "unknown"), []).append(row)
        categories.setdefault(row.get("prompt_category", "general"), []).append(row)

    overall["by_engine"] = {
        engine: _group(group, len(group), weights) for engine, group in sorted(engines.items())
    }
    overall["by_category"] = {
        category: _group(group, len(group), weights)
        for category, group in sorted(categories.items())
    }
    return overall


def iter_succeeded(rows: Iterable[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    return (row for row in rows if row.get("status") == "succeeded")
