#!/usr/bin/env python3
"""Initialize non-publishable research-analysis records for a dated run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from professor_information import SourceDataError, build_research_analysis_skeleton


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cutoff", required=True)
    parser.add_argument("--professors", type=Path, default=Path("data/professors.json"))
    parser.add_argument("--output", type=Path, default=Path("data/research-analysis.json"))
    args = parser.parse_args()
    if args.output.exists():
        raise SourceDataError("research-analysis output already exists; refusing to overwrite progress")
    professors = json.loads(args.professors.read_text(encoding="utf-8"))
    if not isinstance(professors, list) or not professors:
        raise SourceDataError("professor data must be a non-empty JSON list")
    analyses = build_research_analysis_skeleton(professors, args.cutoff)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(analyses, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"{len(analyses)}/{len(analyses)}")


if __name__ == "__main__":
    main()
