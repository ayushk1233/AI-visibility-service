from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

from app.analysis import create_analysis, get_analysis_status, run_analysis
from app.config import get_settings
from app.db import Database
from app.ingestion import import_dataset
from app.reports import create_report, get_version
from app.util import iso


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import the supplied pack, run replay classification, and write an analysis JSON."
    )
    parser.add_argument("--source-dir", default=".", help="Pack root or data directory under DATA_DIR.")
    parser.add_argument(
        "--output",
        type=Path,
        help="Output path. Defaults to eval/results/<UTC>-replay/analysis.json.",
    )
    args = parser.parse_args()

    settings = get_settings()
    if settings.llm_mode != "replay":
        settings = replace(settings, llm_mode="replay", llm_model="replay-v1")
    database = Database(settings.database_path)
    database.init()

    with database.connect() as conn:
        dataset, _ = import_dataset(conn, settings, args.source_dir, "analyst")
        analysis = create_analysis(
            conn,
            settings,
            dataset["dataset_id"],
            "analyst",
            force=True,
        )

    run_analysis(database, settings, analysis["analysis_id"])
    with database.connect() as conn:
        status = get_analysis_status(conn, analysis["analysis_id"])
        if status["status"] != "completed":
            raise SystemExit(f"Analysis ended as {status['status']}: {status.get('error')}")
        report = create_report(
            conn,
            analysis["analysis_id"],
            "Kasparro AI visibility report",
            "analyst",
        )
        version = report["versions"][0]
        content = get_version(conn, report["report_id"], version["version_id"])["content"]

    content["active_time"] = {
        "instrumented": False,
        "note": (
            "Active time was not automatically instrumented. See docs/DECISION_NOTE.md for the "
            "honest session estimate and the six-hour cap disclosure."
        ),
    }
    output = args.output
    if output is None:
        stamp = iso().replace(":", "").replace("-", "").split(".")[0]
        output = Path("eval/results") / f"{stamp}-replay" / "analysis.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(content, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {output}")
    print(
        "coverage={coverage} index={index} diagnostics={diagnostics} remedies={remedies}".format(
            coverage=content["coverage"]["coverage"],
            index=content["scores"]["overall"]["visibility_index"],
            diagnostics=len(content["diagnostics"]),
            remedies=len(content["remedies"]),
        )
    )


if __name__ == "__main__":
    main()
