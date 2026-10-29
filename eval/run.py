from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import BASE_DIR, Settings, get_settings
from app.llm.base import AdapterResult
from app.llm.prompts import PROMPT_VERSION
from app.llm.replay import COMPETITORS, ReplayClassifier
from app.llm.service import ClassificationOutcome, ClassifierService
from app.models import ClassificationResult, Evidence
from app.scoring import DEFAULT_WEIGHTS
from app.util import canonical_json, content_hash, iso

LABEL_FIELDS = ("kasparro_present", "recommended", "negatively_characterised")


class InvalidEvidenceAdapter:
    mode = "controlled"

    def classify(self, question: str, answer: str, citations: list[dict]) -> AdapterResult:
        classification = ClassificationResult(
            kasparro_present=True,
            recommended=True,
            negatively_characterised=False,
            competitors=[],
            recommendation_position=None,
            evidence=[Evidence(quote="This quote is not present in the answer.", supports=["kasparro_present"])],
            notes="Controlled evidence-boundary failure.",
        )
        return AdapterResult(
            classification=classification,
            raw_output=classification.model_dump_json(),
            model_name="controlled-invalid-evidence",
        )


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_supplied() -> tuple[dict[str, dict], dict[str, dict], dict[str, list[dict]]]:
    prompts = {row["prompt_id"]: row for row in load_jsonl(BASE_DIR / "data" / "prompts.jsonl")}
    responses = {row["response_id"]: row for row in load_jsonl(BASE_DIR / "data" / "responses.jsonl")}
    citations: dict[str, list[dict]] = defaultdict(list)
    for row in load_jsonl(BASE_DIR / "data" / "citations.jsonl"):
        citations[row["response_id"]].append(row)
    return prompts, responses, citations


def baseline_classify(text: str) -> ClassificationResult:
    lowered = text.lower()
    present = "kasparro" in lowered
    positions = [match.start() for match in re.finditer(r"kasparro", lowered)]
    window = " ".join(
        text[max(0, position - 220) : position + 320].lower() for position in positions
    )
    positive = any(
        cue in window
        for cue in (
            "can help",
            "helps",
            "recommend",
            "worth",
            "useful",
            "suitable",
            "good option",
            "best",
            "designed specifically",
        )
    )
    negative = any(
        cue in window
        for cue in (
            "not reliable",
            "not a ",
            "scam",
            "gimmick",
            "overpriced",
            "expensive",
            "limited",
            "missing",
            "unproven",
            "skeptical",
            "concern",
            "risk",
            "hard to cancel",
        )
    )
    recommended = bool(present and positive and not negative)
    competitors = sorted(
        name
        for name, aliases in COMPETITORS.items()
        if any(alias in lowered for alias in aliases)
    )
    position_match = re.search(r"(?:^|\n)\s*(?:#\s*)?(\d{1,2})[.)-]\s*[^\n]{0,100}\bkasparro\b", text, re.I)
    return ClassificationResult(
        kasparro_present=present,
        recommended=recommended,
        negatively_characterised=bool(present and negative),
        competitors=competitors,
        recommendation_position=int(position_match.group(1)) if position_match else None,
        evidence=[],
        notes="Deterministic keyword/regex baseline.",
    )


def classify_approach(
    case: dict[str, Any],
    question: str,
    answer: str,
    citations: list[dict],
    settings: Settings,
) -> ClassificationOutcome:
    if case.get("synthetic") and case.get("expected_outcome") == "failed":
        adapter = InvalidEvidenceAdapter()
    elif settings.llm_mode == "replay":
        adapter = ReplayClassifier()
    else:
        from app.llm.openai_compat import OpenAICompatibleClassifier

        adapter = OpenAICompatibleClassifier(settings)
    service = ClassifierService(settings, adapter=adapter)
    return service.classify(question, answer, citations)


def validate_label(case: dict[str, Any], answer: str) -> None:
    if case["recommended"] and not case["kasparro_present"]:
        raise ValueError(f"{case['response_id']}: recommended requires kasparro_present")
    for quote in case.get("supporting_quotes") or []:
        if quote not in answer:
            raise ValueError(f"{case['response_id']}: supporting quote is not verbatim: {quote!r}")


def _kappa(tp: int, tn: int, fp: int, fn: int) -> float | None:
    n = tp + tn + fp + fn
    if n == 0:
        return None
    observed = (tp + tn) / n
    expected = ((tp + fp) * (tp + fn) + (tn + fn) * (tn + fp)) / (n * n)
    if expected == 1.0:
        return None
    return round((observed - expected) / (1 - expected), 4)


def label_metrics(records: list[dict[str, Any]], system: str) -> dict[str, Any]:
    usable = [
        record
        for record in records
        if record[system]["status"] == "succeeded"
        and record["expected_outcome"] in (None, "succeeded")
    ]
    confusion = {}
    for field in LABEL_FIELDS:
        tp = fp = fn = tn = 0
        for record in usable:
            expected = bool(record["label"][field])
            actual = bool(record[system]["labels"].get(field))
            if expected and actual:
                tp += 1
            elif not expected and actual:
                fp += 1
            elif expected and not actual:
                fn += 1
            else:
                tn += 1
        precision = tp / (tp + fp) if tp + fp else None
        recall = tp / (tp + fn) if tp + fn else None
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision is not None and recall is not None and precision + recall
            else None
        )
        confusion[field] = {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "tn": tn,
            "precision": round(precision, 4) if precision is not None else None,
            "recall": round(recall, 4) if recall is not None else None,
            "f1": round(f1, 4) if f1 is not None else None,
            "kappa": _kappa(tp, tn, fp, fn),
        }
    f1_values = [value["f1"] for value in confusion.values() if value["f1"] is not None]
    exact = sum(
        1
        for record in usable
        if all(bool(record["label"][field]) == bool(record[system]["labels"].get(field)) for field in LABEL_FIELDS)
    )
    return {
        "eligible": len(records),
        "succeeded": sum(1 for record in records if record[system]["status"] == "succeeded"),
        "failed": sum(1 for record in records if record[system]["status"] == "failed"),
        "skipped": sum(1 for record in records if record[system]["status"] == "skipped"),
        "label_metrics_n": len(usable),
        "labels": confusion,
        "macro_f1": round(sum(f1_values) / len(f1_values), 4) if f1_values else None,
        "raw_agreement": round(exact / len(usable), 4) if usable else None,
    }


def competitor_metrics(records: list[dict[str, Any]], system: str) -> dict[str, Any]:
    tp = fp = fn = 0
    usable = 0
    for record in records:
        if record[system]["status"] != "succeeded":
            continue
        usable += 1
        expected = {name.lower().strip() for name in record["label"].get("competitors", [])}
        actual = {name.lower().strip() for name in record[system]["labels"].get("competitors", [])}
        tp += len(expected & actual)
        fp += len(actual - expected)
        fn += len(expected - actual)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * precision * recall / (precision + recall) if precision and recall else None
    return {
        "cases": usable,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "f1": round(f1, 4) if f1 is not None else None,
    }


def evidence_metrics(records: list[dict[str, Any]], system: str) -> dict[str, Any]:
    total = valid = 0
    for record in records:
        if record[system]["status"] != "succeeded":
            continue
        for evidence in record[system]["labels"].get("evidence", []):
            total += 1
            if evidence.get("quote") and evidence["quote"] in record["answer_text"]:
                valid += 1
    return {
        "quotes": total,
        "valid": valid,
        "rate": round(valid / total, 4) if total else None,
    }


def citation_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    checks = []
    for record in records:
        if record["synthetic"]:
            continue
        expected = bool(record["label"].get("citation_present"))
        actual = bool(record["citation_count"])
        checks.append(expected == actual)
    return {
        "cases": len(checks),
        "matches": sum(checks),
        "rate": round(sum(checks) / len(checks), 4) if checks else None,
    }


def per_group_agreement(records: list[dict[str, Any]], system: str, group: str) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if record[system]["status"] == "succeeded" and record["expected_outcome"] in (None, "succeeded"):
            grouped[str(record.get(group))].append(record)
    result = {}
    for key, items in sorted(grouped.items()):
        exact = sum(
            1
            for record in items
            if all(
                bool(record["label"][field]) == bool(record[system]["labels"].get(field))
                for field in LABEL_FIELDS
            )
        )
        result[key] = {
            "n": len(items),
            "exact_matches": exact,
            "raw_agreement": round(exact / len(items), 4) if items else None,
        }
    return result


def make_results_dir(mode: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = BASE_DIR / "eval" / "results"
    path = root / f"{stamp}-{mode}"
    suffix = 1
    while path.exists():
        path = root / f"{stamp}-{mode}-{suffix}"
        suffix += 1
    path.mkdir(parents=True)
    (path / "raw").mkdir()
    return path


def write_report(path: Path, metrics: dict[str, Any], records: list[dict[str, Any]]) -> None:
    supplied = metrics["supplied"]
    approach = supplied["approach"]
    baseline = supplied["baseline"]
    lines = [
        "# Kasparro visibility evaluation",
        "",
        f"- Mode: `{metrics['mode']}`",
        f"- Model: `{metrics['model']}`",
        f"- Generated: `{metrics['generated_at']}`",
        f"- Label set: `{metrics['label_set_version']}`",
        f"- Supplied cases: {metrics['cases']['supplied']} ({metrics['cases']['supplied_tuning']} tuning, "
        f"{metrics['cases']['supplied_final_check']} final-check)",
        f"- Controlled edge cases: {metrics['cases']['edge']} (reported separately)",
        "",
        "## Coverage",
        "",
        "| System | Eligible | Succeeded | Failed | Skipped | Label metric n |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name in ("baseline", "approach"):
        item = supplied[name]
        lines.append(
            f"| {name} | {item['eligible']} | {item['succeeded']} | {item['failed']} | "
            f"{item['skipped']} | {item['label_metrics_n']} |"
        )
    lines += [
        "",
        "Failed and skipped responses are excluded from label denominators and remain visible above.",
        "",
        "## Core label metrics (supplied cases)",
        "",
        "| System | Label | TP | FP | FN | TN | Precision | Recall | F1 | Kappa |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("baseline", "approach"):
        for label in LABEL_FIELDS:
            value = supplied[name]["labels"][label]
            lines.append(
                f"| {name} | {label} | {value['tp']} | {value['fp']} | {value['fn']} | {value['tn']} | "
                f"{value['precision']} | {value['recall']} | {value['f1']} | {value['kappa']} |"
            )
    lines += [
        "",
        f"- Baseline macro F1: `{baseline['macro_f1']}`; raw agreement: `{baseline['raw_agreement']}`",
        f"- Approach macro F1: `{approach['macro_f1']}`; raw agreement: `{approach['raw_agreement']}`",
        "",
        "## Agreement by engine and split",
        "",
        "| System | Group | Value | n | Exact matches | Raw agreement |",
        "|---|---|---|---:|---:|---:|",
    ]
    for name in ("baseline", "approach"):
        for group in ("by_engine", "by_split"):
            for value, item in supplied[name][group].items():
                lines.append(
                    f"| {name} | {group} | {value} | {item['n']} | {item['exact_matches']} | "
                    f"{item['raw_agreement']} |"
                )
    lines += [
        "",
        "## Evidence, citations, and competitors",
        "",
        "| Check | Baseline | Approach |",
        "|---|---:|---:|",
        f"| Evidence quote validity rate | n/a | {supplied['approach_evidence']['rate']} "
        f"({supplied['approach_evidence']['valid']}/{supplied['approach_evidence']['quotes']}) |",
        f"| Competitor precision | {supplied['baseline_competitors']['precision']} | "
        f"{supplied['approach_competitors']['precision']} |",
        f"| Competitor recall | {supplied['baseline_competitors']['recall']} | "
        f"{supplied['approach_competitors']['recall']} |",
        f"| Competitor F1 | {supplied['baseline_competitors']['f1']} | "
        f"{supplied['approach_competitors']['f1']} |",
        f"| Citation-presence matches | {supplied['citation_presence']['matches']}/"
        f"{supplied['citation_presence']['cases']} | same deterministic check |",
        "",
        "Evidence validity is not claim support. It only checks that returned quotes occur verbatim in the answer. "
        "Citation presence is deterministic; source support was not inspected.",
        "",
        "## Controlled edge cases",
        "",
        "| Case | Expected | Approach status | Reason |",
        "|---|---|---|---|",
    ]
    for record in records:
        if not record["synthetic"]:
            continue
        lines.append(
            f"| {record['response_id']} | {record['expected_outcome'] or 'succeeded'} | "
            f"{record['approach']['status']} | {record['approach'].get('reason_code') or ''} |"
        )
    failures = [record for record in records if record["approach"]["status"] == "failed"]
    lines += [
        "",
        "## Failures and limits",
        "",
        f"- Approach failures across all cases: {len(failures)} (expected controlled failures: "
        f"{sum(1 for record in failures if record['expected_outcome'] == 'failed')}).",
        "- The sample is 12 supplied responses plus 3 controlled synthetic cases; estimates are unstable.",
        "- One response per prompt and engine means no measure of run-to-run variability.",
        "- ChatGPT used `force_web_search=true`; AI Mode and Gemini recorded null search settings, so engine comparisons are confounded.",
        "- Replay mode demonstrates mechanics, not live-model quality, latency, or cost.",
        "- Citation support is unverified; this evaluation measures citation presence only.",
        "- The labels were produced by one annotator without a second adjudicator.",
        "- The replay heuristic was revised after inspecting labelled cases; its replay agreement is not independent generalization evidence.",
        "- The final-check split was not a fully blind holdout because the same author had inspected all 12 supplied answers while labelling.",
        "",
        "## Reproduce",
        "",
        "```bash",
        "python -m eval.run --mode replay",
        "```",
        "",
        "Live mode requires the configured OpenAI-compatible endpoint and does not change the label set.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Kasparro evaluation harness.")
    parser.add_argument("--mode", choices=("replay", "live"), default="replay")
    parser.add_argument("--labels", default="eval/cases/label_set_v1.jsonl")
    parser.add_argument("--edges", default="eval/cases/edge_cases_v1.jsonl")
    args = parser.parse_args()

    settings = replace(get_settings(), llm_mode=args.mode)
    if args.mode == "replay":
        settings = replace(settings, llm_model="replay-v1")

    prompts, responses, citations = load_supplied()
    supplied_labels = load_jsonl(BASE_DIR / args.labels)
    edge_labels = load_jsonl(BASE_DIR / args.edges)
    if len(supplied_labels) < 12:
        raise ValueError("At least 12 supplied labelled cases are required")
    if len(edge_labels) < 3:
        raise ValueError("At least 3 controlled edge cases are required")

    records = []
    for case in supplied_labels + edge_labels:
        if case.get("synthetic"):
            answer = case["answer_text"]
            question = case["prompt_id"]
            case_citations = []
        else:
            response = responses[case["response_id"]]
            answer = response["answer_text"]
            question = prompts[response["prompt_id"]]["text"]
            case_citations = citations.get(response["response_id"], [])
        validate_label(case, answer)
        approach = classify_approach(case, question, answer, case_citations, settings)
        if settings.llm_mode == "live":
            time.sleep(2.5)
        baseline = baseline_classify(answer)
        approach_labels = (
            approach.classification.model_dump(mode="json") if approach.classification else {}
        )
        record = {
            "response_id": case["response_id"],
            "prompt_id": case.get("prompt_id"),
            "engine": case.get("engine"),
            "prompt_category": case.get("prompt_category"),
            "split": case.get("split"),
            "synthetic": bool(case.get("synthetic")),
            "source": case.get("source"),
            "expected_outcome": case.get("expected_outcome"),
            "expected_reason": case.get("expected_reason"),
            "label": {field: case[field] for field in LABEL_FIELDS}
            | {
                "competitors": case.get("competitors", []),
                "recommendation_position": case.get("recommendation_position"),
                "citation_present": case.get("citation_present"),
            },
            "citation_count": len(case_citations),
            "answer_text": answer,
            "baseline": {
                "status": "succeeded",
                "labels": baseline.model_dump(mode="json"),
                "reason_code": None,
                "raw_output": None,
            },
            "approach": {
                "status": approach.status,
                "labels": approach_labels,
                "reason_code": approach.reason_code,
                "reason_detail": approach.reason_detail,
                "raw_output": approach.raw_output,
                "attempts": approach.attempts,
                "attempt_log": approach.attempt_log,
                "model_name": approach.model_name,
                "latency_ms": approach.latency_ms,
                "input_tokens": approach.input_tokens,
                "output_tokens": approach.output_tokens,
                "cost_estimate": approach.cost_estimate,
            },
        }
        records.append(record)

    supplied_records = [record for record in records if not record["synthetic"]]
    edge_records = [record for record in records if record["synthetic"]]
    approach_evidence = evidence_metrics(supplied_records, "approach")
    supplied_metrics = {
        "baseline": label_metrics(supplied_records, "baseline"),
        "approach": label_metrics(supplied_records, "approach"),
        "baseline_competitors": competitor_metrics(supplied_records, "baseline"),
        "approach_competitors": competitor_metrics(supplied_records, "approach"),
        "approach_evidence": approach_evidence,
        "citation_presence": citation_metrics(supplied_records),
    }
    for name in ("baseline", "approach"):
        supplied_metrics[name]["by_engine"] = per_group_agreement(supplied_records, name, "engine")
        supplied_metrics[name]["by_split"] = per_group_agreement(supplied_records, name, "split")
    supplied_metrics["edge"] = {
        "baseline": label_metrics(edge_records, "baseline"),
        "approach": label_metrics(edge_records, "approach"),
    }
    supplied_metrics["approach_failures"] = [
        {
            "response_id": record["response_id"],
            "expected_outcome": record["expected_outcome"],
            "actual_status": record["approach"]["status"],
            "reason_code": record["approach"]["reason_code"],
            "reason_detail": record["approach"]["reason_detail"],
        }
        for record in records
        if record["approach"]["status"] == "failed"
    ]
    supplied_metrics["edge_expectations"] = [
        {
            "response_id": record["response_id"],
            "expected_outcome": record["expected_outcome"],
            "expected_reason": record["expected_reason"],
            "actual_status": record["approach"]["status"],
            "actual_reason": record["approach"]["reason_code"],
        }
        for record in edge_records
    ]

    results_dir = make_results_dir(args.mode)
    for record in records:
        raw_path = results_dir / "raw" / f"{record['response_id']}.json"
        raw_path.write_text(
            json.dumps(
                {
                    "response_id": record["response_id"],
                    "engine": record["engine"],
                    "split": record["split"],
                    "synthetic": record["synthetic"],
                    "baseline": record["baseline"],
                    "approach": record["approach"],
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    generated_at = iso()
    metrics = {
        "generated_at": generated_at,
        "mode": args.mode,
        "model": settings.llm_model,
        "label_set_version": "v1",
        "labels_file": args.labels,
        "edges_file": args.edges,
        "prompt_version": PROMPT_VERSION,
        "scoring": {"weights": DEFAULT_WEIGHTS, "interval": "wilson-95"},
        "config_hash": content_hash(
            {
                "mode": args.mode,
                "model": settings.llm_model,
                "labels_file": args.labels,
                "edges_file": args.edges,
                "prompt_version": PROMPT_VERSION,
                "weights": DEFAULT_WEIGHTS,
            }
        ),
        "cases": {
            "supplied": len(supplied_records),
            "supplied_tuning": sum(1 for record in supplied_records if record["split"] == "tuning"),
            "supplied_final_check": sum(1 for record in supplied_records if record["split"] == "final_check"),
            "edge": len(edge_records),
        },
        "supplied": supplied_metrics,
        "limitations": [
            "Small single-annotator sample; estimates are unstable.",
            "The replay heuristic was revised after inspecting labelled cases; replay agreement is not independent generalization evidence.",
            "The final-check split was not a fully blind holdout because the author had inspected all supplied answers while labelling.",
            "One response per prompt and engine; no run-to-run variability measure.",
            "ChatGPT force_web_search=true while AI Mode and Gemini are null; engine comparison is confounded.",
            "Citation presence is not claim support.",
            "Replay mode demonstrates mechanics, not live-model quality.",
            "Controlled synthetic cases are not observations of the supplied providers.",
        ],
    }
    (results_dir / "metrics.json").write_text(
        json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    write_report(results_dir / "report.md", metrics, records)
    print(f"Wrote {results_dir / 'report.md'}")
    print(f"Approach failures across all cases: {len(supplied_metrics['approach_failures'])}")
    for item in supplied_metrics["edge_expectations"]:
        print(
            f"Edge {item['response_id']}: expected {item['expected_outcome']} "
            f"({item['expected_reason']}), got {item['actual_status']} ({item['actual_reason']})"
        )


if __name__ == "__main__":
    main()
