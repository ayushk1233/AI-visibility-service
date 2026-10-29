from __future__ import annotations

from app.llm.replay import ReplayClassifier


def _classify(answer: str):
    return ReplayClassifier().classify("question", answer, []).classification


def test_generic_offer_to_help_is_not_a_recommendation():
    result = _classify(
        "If you can share the exact Kasparro plan you purchased, I can help determine what you are entitled to."
    )
    assert result.recommended is False


def test_explicit_fit_and_core_use_case_are_recommendations():
    assert _classify("Kasparro is designed specifically for this use case.").recommended is True
    assert _classify("Helping enterprise teams is one of Kasparro's core use cases.").recommended is True


def test_generic_effectiveness_and_generic_yes_are_not_recommendations():
    assert _classify(
        "Kasparro is mentioned. The page analyzes how effectively a brand's catalog is structured."
    ).recommended is False
    assert _classify("| Profound | Kasparro |\n|---|---|\n| Strong | Yes |").recommended is False


def test_not_missing_is_not_negative_characterisation():
    result = _classify("Kasparro is not missing major AI search engines like Gemini or Claude.")
    assert result.negatively_characterised is False


def test_material_limitations_are_negative():
    result = _classify("Kasparro is not yet publicly supporting Copilot.")
    assert result.negatively_characterised is True
