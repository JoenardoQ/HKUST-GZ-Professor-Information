#!/usr/bin/env python3
"""Prepare missing exact-key strings for reviewed Chinese translation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from professor_information import SourceDataError, collect_translation_inputs


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
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    if not (args.root / "All_Prof_Info.md").is_file() or not (args.root / "data/manifest.json").is_file():
        raise SourceDataError("English documents must be generated first")
    translation_path = args.translations if args.translations.is_absolute() else args.root / args.translations
    memory = json.loads(translation_path.read_text(encoding="utf-8")) if translation_path.is_file() else {}
    if not isinstance(memory, dict):
        raise SourceDataError("translation memory must contain a JSON object")
    inputs = collect_translation_inputs(
        load_list(args.root / "data/professors.json"),
        load_list(args.root / "data/research-analysis.json"),
        load_list(args.root / "data/publications.json"),
        args.start,
        args.cutoff,
    )
    missing = [value for value in inputs if not str(memory.get(value) or "").strip()]
    output = args.output or args.root / f"reports/{args.cutoff}-translation-inputs.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps({
        "schemaVersion": 1,
        "cutoff": args.cutoff,
        "totalInputs": len(inputs),
        "existingTranslations": len(inputs) - len(missing),
        "inputs": missing,
    }, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(output)
    print(f"{len(missing)}/{len(inputs)}")


if __name__ == "__main__":
    main()
