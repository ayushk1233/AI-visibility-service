from __future__ import annotations

from typing import Any, Callable

from .util import quote_in_text


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _evidence(
    rows: list[dict[str, Any]],
    predicate: Callable[[dict[str, Any]], bool],
    limit: int = 3,
) -> list[dict[str, Any]]:
    evidence = []
    for row in rows:
        if row.get("status") != "succeeded" or not predicate(row):
            continue
        quotes = [item.get("quote") for item in row.get("evidence", []) if item.get("quote")]
        if not quotes:
            continue
        evidence.append(
            {
                "response_id": row["response_id"],
                "engine": row.get("engine"),
                "prompt_category": row.get("prompt_category"),
                "quote": quotes[0],
                "note": row.get("labels", {}).get("notes"),
            }
        )
        if len(evidence) >= limit:
            break
    return evidence


def _gap(
    scores: dict[str, Any],
    bucket: str,
    metric: str,
) -> tuple[tuple[str, dict[str, Any]], tuple[str, dict[str, Any]]] | None:
    values = [
        (name, value)
        for name, value in scores.get(bucket, {}).items()
        if value.get("n", 0) > 0 and value.get(metric) is not None
    ]
    if not values:
        return None
    high = max(values, key=lambda item: (item[1][metric], item[1]["n"]))
    low = min(values, key=lambda item: (item[1][metric], item[1]["n"]))
    return high, low


def build_diagnostics(
    scores: dict[str, Any],
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    diagnostics: list[dict[str, Any]] = []

    engine_gap = _gap(scores, "by_engine", "inclusion_rate")
    if engine_gap:
        high, low = engine_gap
        high_name, high_score = high
        low_name, low_score = low
        diagnostics.append(
            {
                "id": "D1",
                "title": "Engine inclusion gap",
                "observed": (
                    f"Inclusion was {_pct(high_score['inclusion_rate'])} on {high_name} "
                    f"({high_score['label_counts']['present']}/{high_score['n']}) versus "
                    f"{_pct(low_score['inclusion_rate'])} on {low_name} "
                    f"({low_score['label_counts']['present']}/{low_score['n']}). "
                    f"Overall coverage was {_pct(scores['coverage'])} with "
                    f"{scores['failed']} failed and {scores['skipped']} skipped responses."
                ),
                "evidence": _evidence(
                    rows,
                    lambda row: row.get("engine") == high_name
                    and row.get("labels", {}).get("kasparro_present"),
                )
                + _evidence(
                    rows,
                    lambda row: row.get("engine") == low_name
                    and not row.get("labels", {}).get("kasparro_present"),
                ),
                "hypothesis": (
                    "The gap may reflect engine-specific retrieval and answer composition, "
                    "or different capture conditions. This snapshot alone cannot identify the cause."
                ),
                "test": (
                    "Compare the same prompts across engines with matched search settings and more than one run; "
                    "inspect the cited sources behind both the strongest and weakest answers."
                ),
                "counter_check": (
                    "All ChatGPT responses used force_web_search=true while AI Mode and Gemini recorded null; "
                    "AI Mode model labels are missing. A raw engine comparison is therefore confounded."
                ),
            }
        )
    else:
        diagnostics.append(
            {
                "id": "D1",
                "title": "Engine inclusion gap unavailable",
                "observed": "No engine had a valid response to compare.",
                "evidence": [],
                "hypothesis": "Analysis failures or skips removed all comparable engine observations.",
                "test": "Resume failed responses and re-run with the same configuration.",
                "counter_check": "A score cannot be inferred from missing data.",
            }
        )

    recommendation_gap = _gap(scores, "by_engine", "recommendation_rate")
    if recommendation_gap:
        high, low = recommendation_gap
        high_name, high_score = high
        low_name, low_score = low
        diagnostics.append(
            {
                "id": "D2",
                "title": "Recommendation gap",
                "observed": (
                    f"Recommendation was {_pct(high_score['recommendation_rate'])} on {high_name} "
                    f"({high_score['label_counts']['recommended']}/{high_score['n']}) versus "
                    f"{_pct(low_score['recommendation_rate'])} on {low_name} "
                    f"({low_score['label_counts']['recommended']}/{low_score['n']}). "
                    "A mention is not counted as a recommendation."
                ),
                "evidence": _evidence(
                    rows,
                    lambda row: row.get("engine") == high_name
                    and row.get("labels", {}).get("recommended"),
                )
                + _evidence(
                    rows,
                    lambda row: row.get("engine") == low_name
                    and row.get("labels", {}).get("kasparro_present")
                    and not row.get("labels", {}).get("recommended"),
                ),
                "hypothesis": (
                    "The difference may come from how each engine frames options, the sources it retrieves, "
                    "or the prompt's comparison context. It is not evidence of a persistent ranking."
                ),
                "test": (
                    "Hold prompts and capture settings constant, repeat the run, and inspect the answers and sources "
                    "where Kasparro is mentioned but not presented as an option."
                ),
                "counter_check": (
                    "Recommendation labels overlap with negative characterisation, and one response per engine "
                    "cannot establish run-to-run stability."
                ),
            }
        )
    else:
        diagnostics.append(
            {
                "id": "D2",
                "title": "Recommendation gap unavailable",
                "observed": "No engine had a valid recommendation observation.",
                "evidence": [],
                "hypothesis": "Analysis failures or skips removed the comparable observations.",
                "test": "Resume failed responses and re-run the evaluation.",
                "counter_check": "Missing observations are not zero recommendations.",
            }
        )

    negative_rows = [
        row
        for row in rows
        if row.get("status") == "succeeded" and row.get("labels", {}).get("negatively_characterised")
    ]
    zero_citation_rows = [
        row
        for row in rows
        if row.get("status") == "succeeded" and (row.get("citation_count") or 0) == 0
    ]
    diagnostics.append(
        {
            "id": "D3",
            "title": "Credibility and citation coverage risk",
            "observed": (
                f"{scores['label_counts']['negative']} of {scores['n']} valid responses "
                f"({_pct(scores['negative_rate'])}) negatively characterised Kasparro, and "
                f"{len(zero_citation_rows)} valid responses returned no citations. "
                f"Citation presence is {_pct(scores['citation_presence']['rate'])}; "
                "returned links are not verified claim support."
            ),
            "evidence": _evidence(
                rows,
                lambda row: row.get("labels", {}).get("negatively_characterised"),
            )
            + _evidence(
                rows,
                lambda row: row.get("status") == "succeeded"
                and (row.get("citation_count") or 0) == 0,
                limit=2,
            ),
            "hypothesis": (
                "Negative framing may reflect missing independent proof, category ambiguity, or a mismatch between "
                "the question's expectations and the public evidence. The snapshot cannot establish which."
            ),
            "test": (
                "Inspect the sources behind negative claims, add dated first-party and independent proof where it "
                "exists, and repeat a bounded prompt set to see whether the observed labels change."
            ),
            "counter_check": (
                "Negative language can coexist with a recommendation; zero citations do not prove an unsupported "
                "answer, and citation support was not verified."
            ),
        }
    )

    remedies = [
        {
            "id": "R1",
            "priority": 1,
            "title": "Close the lowest-inclusion engine gap",
            "problem": "Kasparro is included at different rates across engines in this snapshot.",
            "evidence": diagnostics[0]["evidence"][:3],
            "action": (
                "Investigate the weakest engine's retrieved sources and answer framing for the same prompts; "
                "fix crawl or content-access blockers only where evidence supports them, and publish clear, "
                "crawlable pages for the missing use cases."
            ),
            "expected_benefit": "A better chance of consistent inclusion on the weakest engine.",
            "effort": "medium",
            "uncertainty": "high",
            "validation": (
                "Repeat the same prompt set at least three times on the same engine and compare inclusion rate, "
                "coverage, and failures against this baseline."
            ),
        },
        {
            "id": "R2",
            "priority": 2,
            "title": "Create recommendation-ready comparison and category pages",
            "problem": "Mentions do not reliably become recommendations.",
            "evidence": diagnostics[1]["evidence"][:3],
            "action": (
                "Build source-ready comparison, category, and use-case pages that explicitly explain who Kasparro is "
                "for and when it is a suitable option, without inventing proof or promising rankings."
            ),
            "expected_benefit": "More answer contexts where Kasparro can be described as a suitable option.",
            "effort": "high",
            "uncertainty": "high",
            "validation": (
                "Re-run the comparison prompts and track recommendation rate separately from inclusion rate, "
                "with the same denominators and failure visibility."
            ),
        },
        {
            "id": "R3",
            "priority": 3,
            "title": "Publish independent, dated proof for contested claims",
            "problem": "Some answers question credibility or return no citations.",
            "evidence": diagnostics[2]["evidence"][:3],
            "action": (
                "Audit the claims most often questioned, publish dated first-party evidence and seek independent "
                "third-party proof where available, and correct unsupported public claims."
            ),
            "expected_benefit": "A stronger evidence base for answers that currently question credibility.",
            "effort": "medium",
            "uncertainty": "high",
            "validation": (
                "Inspect the cited pages, then repeat a fixed sample and compare negative-characterisation and "
                "citation-presence rates; keep source inspection separate from citation presence."
            ),
        },
    ]
    return diagnostics, remedies


def audit_report_evidence(
    content: dict[str, Any],
    answer_text_by_id: dict[str, str],
) -> list[str]:
    errors: list[str] = []
    if content.get("schema_version") != "report-content-v1":
        errors.append("Report schema_version must be report-content-v1")
    for field in ("run", "analysis", "coverage", "scores"):
        if not isinstance(content.get(field), dict):
            errors.append(f"Report field {field} must be an object")
    diagnostics = content.get("diagnostics")
    remedies = content.get("remedies")
    if not isinstance(diagnostics, list) or len(diagnostics) != 3:
        errors.append("Report must contain exactly three diagnostics")
    if not isinstance(remedies, list) or len(remedies) != 3:
        errors.append("Report must contain exactly three remedies")

    required = {
        "diagnostics": ("id", "title", "observed", "hypothesis", "test", "counter_check"),
        "remedies": (
            "id",
            "priority",
            "title",
            "problem",
            "action",
            "expected_benefit",
            "effort",
            "uncertainty",
            "validation",
        ),
    }
    for section in ("diagnostics", "remedies"):
        items = content.get(section) or []
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                errors.append(f"{section} contains a non-object item")
                continue
            item_id = item.get("id", section)
            for field in required[section]:
                if item.get(field) in (None, "", []):
                    errors.append(f"{item_id} is missing required field {field}")
            evidence = item.get("evidence")
            if not isinstance(evidence, list):
                errors.append(f"{item_id} evidence must be a list")
                continue
            if not evidence and "unavailable" not in str(item.get("title", "")).lower():
                errors.append(f"{item_id} has no supporting response evidence")
            for entry in evidence:
                if not isinstance(entry, dict):
                    errors.append(f"{item_id} contains a non-object evidence entry")
                    continue
                response_id = entry.get("response_id")
                if response_id not in answer_text_by_id:
                    errors.append(f"{item_id} references unknown response {response_id}")
                    continue
                quote = entry.get("quote")
                if not quote:
                    errors.append(f"{item_id} evidence for {response_id} has no quote")
                elif quote not in answer_text_by_id[response_id]:
                    errors.append(f"{item_id} has a quote that is not present in {response_id}")

    index = content.get("response_index")
    if not isinstance(index, list):
        errors.append("response_index must be a list")
    else:
        overall = ((content.get("scores") or {}).get("overall") or {})
        recomputed = {
            "eligible": len(index),
            "n": sum(1 for row in index if row.get("status") == "succeeded"),
            "failed": sum(1 for row in index if row.get("status") == "failed"),
            "skipped": sum(1 for row in index if row.get("status") == "skipped"),
            "present": sum(1 for row in index if row.get("status") == "succeeded" and row.get("present")),
            "recommended": sum(
                1 for row in index if row.get("status") == "succeeded" and row.get("recommended")
            ),
            "negative": sum(1 for row in index if row.get("status") == "succeeded" and row.get("negative")),
        }
        checks = {
            "eligible": overall.get("eligible"),
            "n": overall.get("n"),
            "failed": overall.get("failed"),
            "skipped": overall.get("skipped"),
        }
        for key, expected in checks.items():
            if expected != recomputed[key]:
                errors.append(f"scores.{key} does not match response_index")
        counts = overall.get("label_counts") or {}
        for key in ("present", "recommended", "negative"):
            if counts.get(key) != recomputed[key]:
                errors.append(f"scores.label_counts.{key} does not match response_index")

    return errors
