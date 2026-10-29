from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models import ClassificationResult
from app.scoring import wilson_interval, score_rows
from app.util import content_hash


def _rows():
    return [
        {
            "engine": "a",
            "prompt_category": "c",
            "status": "succeeded",
            "labels": {"kasparro_present": True, "recommended": True, "negatively_characterised": False},
            "citation_count": 1,
        },
        {
            "engine": "a",
            "prompt_category": "c",
            "status": "succeeded",
            "labels": {"kasparro_present": True, "recommended": False, "negatively_characterised": True},
            "citation_count": 0,
        },
        {
            "engine": "b",
            "prompt_category": "c",
            "status": "succeeded",
            "labels": {"kasparro_present": False, "recommended": False, "negatively_characterised": False},
            "citation_count": 1,
        },
        {"engine": "b", "prompt_category": "c", "status": "failed", "labels": {}, "citation_count": 0},
        {"engine": "b", "prompt_category": "c", "status": "skipped", "labels": {}, "citation_count": 0},
    ]


def test_score_components_and_denominators_are_exact():
    scores = score_rows(_rows(), eligible=5)
    assert scores["eligible"] == 5
    assert scores["n"] == 3
    assert scores["failed"] == 1
    assert scores["skipped"] == 1
    assert scores["coverage"] == 0.6
    assert scores["inclusion_rate"] == 0.6667
    assert scores["recommendation_rate"] == 0.3333
    assert scores["negative_rate"] == 0.3333
    assert scores["visibility_index"] == 53.33
    assert scores["label_counts"] == {"present": 2, "recommended": 1, "negative": 1}
    assert scores["components"]["inclusion"] == {
        "numerator": 2,
        "denominator": 3,
        "rate": 0.6667,
        "weight": 0.4,
        "weighted_contribution": 0.2667,
    }
    assert scores["components"]["non_negative"]["numerator"] == 2


def test_wilson_interval_matches_known_value():
    low, high = wilson_interval(2, 3)
    assert low == pytest.approx(0.2076549551264879)
    assert high == pytest.approx(0.9385096847238394)
    low, high = wilson_interval(0, 10)
    assert low == 0.0
    assert high == pytest.approx(0.2775401687666165)
    assert wilson_interval(0, 0) == (None, None)


def test_empty_denominator_is_null_not_zero():
    scores = score_rows([], eligible=0)
    assert scores["n"] == 0
    assert scores["visibility_index"] is None
    assert scores["coverage"] is None
    assert scores["inclusion_rate"] is None
    assert scores["intervals"]["inclusion"] == [None, None]


def test_weights_must_sum_to_one():
    with pytest.raises(ValueError):
        score_rows(_rows(), weights={"inclusion": 0.4, "recommendation": 0.4, "non_negative": 0.4})


def test_config_hash_is_stable_and_changes_with_config():
    config = {
        "scoring": {"weights": {"inclusion": 0.4, "recommendation": 0.4, "non_negative": 0.2}},
        "taxonomy": "v1",
    }
    assert content_hash(config) == content_hash(dict(reversed(list(config.items()))))
    changed = {**config, "taxonomy": "v2"}
    assert content_hash(changed) != content_hash(config)


def test_recommendation_without_presence_is_rejected():
    with pytest.raises(ValidationError):
        ClassificationResult(kasparro_present=False, recommended=True)
