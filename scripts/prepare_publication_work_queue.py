#!/usr/bin/env python3
"""Create the dated Codex work queue for the approved research-source pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from professor_information import SourceDataError, build_publication_work_queue


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True)
    parser.add_argument("--cutoff", required=True)
    parser.add_argument("--professors", type=Path, default=Path("data/professors.json"))
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    if not args.professors.is_file():
        raise SourceDataError(f"professor data does not exist: {args.professors}")
    professors = json.loads(args.professors.read_text(encoding="utf-8"))
    if not isinstance(professors, list) or not professors:
        raise SourceDataError("professor data must be a non-empty JSON list")
    queue = build_publication_work_queue(professors, args.start, args.cutoff)
    output = args.output or Path(f"reports/{args.cutoff}-publication-work-queue.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(queue, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{len(queue['professors'])}/{len(queue['professors'])}")


if __name__ == "__main__":
    main()
