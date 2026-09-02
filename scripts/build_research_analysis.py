#!/usr/bin/env python3
"""Build original English research analysis from the dated retrieval report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from professor_information import SourceDataError, build_research_analysis_from_evidence


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cutoff", required=True)
    parser.add_argument("--professors", type=Path, default=Path("data/professors.json"))
    parser.add_argument("--evidence", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=Path("data/research-analysis.json"))
    args = parser.parse_args()
    evidence_path = args.evidence or Path(f"reports/{args.cutoff}-retrieval-evidence.json")
    professors = json.loads(args.professors.read_text(encoding="utf-8"))
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    records = payload.get("records")
    if not isinstance(professors, list) or not professors or not isinstance(records, list):
        raise SourceDataError("professor and retrieval evidence inputs must be non-empty lists")
    analyses = build_research_analysis_from_evidence(professors, records, args.cutoff)
    args.output.write_text(json.dumps(analyses, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{len(analyses)}/{len(professors)}")


if __name__ == "__main__":
    main()
