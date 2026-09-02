#!/usr/bin/env python3
"""Generate validated English professor Markdown and public web indexes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from professor_information import SourceDataError, deduplicate_publications, generate_documents


def load_list(path: Path) -> list[dict]:
    if not path.exists():
        raise SourceDataError(f"required input does not exist: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise SourceDataError(f"{path} must contain a JSON list")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True)
    parser.add_argument("--cutoff", required=True)
    parser.add_argument("--generated-at", required=True, help="Fixed ISO timestamp for reproducible output")
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    professors = load_list(args.root / "data/professors.json")
    research_analysis = load_list(args.root / "data/research-analysis.json")
    candidates = load_list(args.root / "data/publications.json")
    publications, conflicts = deduplicate_publications(candidates)
    if conflicts:
        conflict_path = args.root / f"reports/{args.cutoff}-conflicts.json"
        conflict_path.parent.mkdir(parents=True, exist_ok=True)
        conflict_path.write_text(json.dumps(conflicts, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        raise SourceDataError(f"publication conflicts require review: {conflict_path}")
    generate_documents(args.root, professors, research_analysis, publications, args.start, args.cutoff, args.generated_at)
    print(f"{len(professors)}/{len(professors)}")


if __name__ == "__main__":
    main()
