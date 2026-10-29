from __future__ import annotations

import re

from ..models import ClassificationResult, Evidence
from .base import AdapterResult

COMPETITORS: dict[str, tuple[str, ...]] = {
    "Profound": ("profound",),
    "Otterly AI": ("otterly ai", "otterly"),
    "AthenaHQ": ("athenahq", "athena hq"),
    "Peec AI": ("peec ai", "peec"),
    "Dageno AI": ("dageno ai", "dageno"),
    "Scrunch": ("scrunch",),
    "Bluefish": ("bluefish",),
    "Semrush": ("semrush",),
    "Ahrefs": ("ahrefs",),
    "Brandi AI": ("brandi ai",),
    "Goodie": ("goodie",),
    "Writesonic": ("writesonic",),
    "HubSpot": ("hubspot",),
    "Jasper": ("jasper",),
    "BrightEdge": ("brightedge",),
    "Conductor": ("conductor",),
    "seoClarity": ("seoclarity",),
    "Similarweb": ("similarweb",),
}

POSITIVE_CUES = (
    "can help",
    "helps",
    "core use case",
    "designed specifically",
    "good option",
    "recommend kasparro",
    "useful",
    "suitable",
    "option",
    "reliable",
)
NEGATIVE_CUES = (
    "not reliable",
    "not suitable",
    "not a good",
    "not yet",
    "not publicly",
    "limited",
    "unclear",
    "scam",
    "gimmick",
    "hype",
    "risk",
    "overpriced",
    "expensive",
    "hard to cancel",
    "lock",
    "unproven",
    "skeptic",
    "concern",
    "caution",
    "weak",
    "poor",
    "disappointing",
    "avoid",
    "red flag",
    "missing",
)

NEGATED_POSITIVE = re.compile(
    r"\b(?:not|isn't|doesn't|no)\b[^.]{0,50}\b(?:recommend|good|work|effective|legit|worth|useful|reliable|solid|strong|suitable|option)\b",
    re.IGNORECASE,
)
POSITION_PATTERN = re.compile(
    r"(?:^|\n)\s*(?:#\s*)?(\d{1,2})[.)-]\s*[^\n]{0,100}\bkasparro\b",
    re.IGNORECASE,
)


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [part.strip() for part in parts if part.strip()]


def _kasparro_sentences(text: str) -> list[str]:
    return [sentence for sentence in _sentences(text) if "kasparro" in sentence.lower()]


def _extract_competitors(text: str) -> list[str]:
    lowered = text.lower()
    found = []
    for name, aliases in COMPETITORS.items():
        if any(alias in lowered for alias in aliases):
            found.append(name)
    return sorted(set(found))


def _evidence_for(text: str, present: bool, recommended: bool, negative: bool) -> list[Evidence]:
    if not present:
        return []
    supports = ["kasparro_present"]
    if recommended:
        supports.append("recommended")
    if negative:
        supports.append("negatively_characterised")
    quotes = []
    for sentence in _kasparro_sentences(text)[:3]:
        quote = sentence[:1200]
        if quote and quote not in quotes:
            quotes.append(quote)
    if not quotes:
        index = text.lower().find("kasparro")
        if index >= 0:
            quotes.append(text[max(0, index - 80) : index + 200])
    return [Evidence(quote=quote, supports=supports) for quote in quotes[:2]]


class ReplayClassifier:
    mode = "replay"

    def classify(self, question: str, answer: str, citations: list[dict]) -> AdapterResult:
        lowered = answer.lower()
        present = "kasparro" in lowered
        kasparro_text = " ".join(_kasparro_sentences(answer)).lower()
        negative_text = kasparro_text.replace("not missing", "")
        positive_text = kasparro_text.replace("i can help", "")

        negative = present and any(cue in negative_text for cue in NEGATIVE_CUES)
        positive = present and any(cue in positive_text for cue in POSITIVE_CUES)
        if positive and NEGATED_POSITIVE.search(kasparro_text):
            positive = False
        recommended = present and positive

        position_match = POSITION_PATTERN.search(answer)
        position = int(position_match.group(1)) if position_match else None
        classification = ClassificationResult(
            kasparro_present=present,
            recommended=recommended,
            negatively_characterised=negative,
            competitors=_extract_competitors(answer),
            recommendation_position=position,
            evidence=_evidence_for(answer, present, recommended, negative),
            notes="Replay heuristic; demonstrates mechanics only, not model quality.",
        )
        return AdapterResult(
            classification=classification,
            raw_output=classification.model_dump_json(),
            model_name="replay-v1",
        )
