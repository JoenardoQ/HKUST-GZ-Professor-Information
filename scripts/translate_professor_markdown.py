#!/usr/bin/env python3
"""Generate Chinese Markdown from frozen English output and reviewed translations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from professor_information import (
    SourceDataError,
    build_translation_memory_translator,
    generate_chinese_documents,
)


def load_list(path: Path) -> list[dict]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise SourceDataError(f"{path} must contain a JSON list of objects")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True)
    parser.add_argument("--cutoff", required=True)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--translations", type=Path, default=Path("data/translations.zh-CN.json"))
    args = parser.parse_args()
    translation_path = args.translations if args.translations.is_absolute() else args.root / args.translations
    memory = json.loads(translation_path.read_text(encoding="utf-8"))
    if not isinstance(memory, dict):
        raise SourceDataError("translation memory must contain a JSON object")
    professors = load_list(args.root / "data/professors.json")
    analyses = load_list(args.root / "data/research-analysis.json")
    publications = load_list(args.root / "data/publications.json")
    generate_chinese_documents(
        args.root,
        professors,
        analyses,
        publications,
        args.start,
        args.cutoff,
        build_translation_memory_translator(memory),
    )
    print(f"{len(professors)}/{len(professors)}")


if __name__ == "__main__":
    main()
