#!/usr/bin/env python3
"""Build and deduplicate publication candidates from reviewed source evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from professor_information import (
    SourceDataError,
    deduplicate_publications,
    publication_candidates_from_evidence,
)


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cutoff", required=True)
    parser.add_argument("--evidence", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=Path("data/publications.json"))
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args()
    evidence_path = args.evidence or Path(f"reports/{args.cutoff}-retrieval-evidence.json")
    report_path = args.report or Path(f"reports/{args.cutoff}-conflicts.json")
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    records = payload.get("records")
    if not isinstance(records, list):
        raise SourceDataError("retrieval evidence must contain a records list")
    candidates, quarantined = publication_candidates_from_evidence(records)
    publications, conflicts = deduplicate_publications(candidates)
    write_json(args.output, publications)
    write_json(report_path, {"quarantined": quarantined, "deduplicationConflicts": conflicts})
    print(f"{len(records)}/{len(records)}")


if __name__ == "__main__":
    main()
