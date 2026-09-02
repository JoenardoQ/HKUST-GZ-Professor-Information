#!/usr/bin/env python3
"""Validate normalized data, English output, and English/Chinese document parity."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from professor_information import (
    SourceDataError,
    validate_bilingual_parity,
    validate_research_analysis,
    validate_retrieval_statuses,
)


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SourceDataError(f"cannot read valid JSON from {path}") from error


def validate(root: Path, cutoff: str) -> dict[str, int]:
    manifest = load_json(root / "data/manifest.json")
    search = load_json(root / "data/search-index.json")
    professors = load_json(root / "data/professors.json")
    research_analysis = load_json(root / "data/research-analysis.json")
    publications = load_json(root / "data/publications.json")
    professor_ids = {str(item["officialProfileId"]) for item in professors}
    validate_research_analysis(research_analysis, professor_ids)
    validate_retrieval_statuses(research_analysis)
    if manifest.get("cutoff") != cutoff or search.get("cutoff") != cutoff:
        raise SourceDataError("manifest/search cutoff mismatch")
    documents = manifest.get("documents")
    if not isinstance(documents, dict) or len(documents) != len(professors):
        raise SourceDataError("manifest document count does not match professor data")
    if len(search.get("records") or []) != len(professors):
        raise SourceDataError("search record count does not match professor data")
    if len(professor_ids) != len(professors):
        raise SourceDataError("professor identities are not unique")
    for publication in publications:
        if str(publication.get("officialProfileId")) not in professor_ids:
            raise SourceDataError("publication references an unknown professor")
    parity_errors = []
    for slug, paths in sorted(documents.items()):
        en_path = root / paths["en"]
        zh_path = root / paths["zhCN"]
        if not en_path.is_file() or not zh_path.is_file():
            parity_errors.append(f"{slug}: bilingual file is missing")
            continue
        english = en_path.read_text(encoding="utf-8")
        chinese = zh_path.read_text(encoding="utf-8")
        parity_errors.extend(f"{slug}: {message}" for message in validate_bilingual_parity(english, chinese))
        record = next(item for item in search["records"] if item["slug"] == slug)
        for title in record["publicationTitles"]:
            if title not in chinese:
                parity_errors.append(f"{slug}: Chinese document does not retain English publication title: {title}")
    overview_en = root / "All_Prof_Info.md"
    overview_zh = root / "All_Prof_Info.zh-CN.md"
    if not overview_en.is_file() or not overview_zh.is_file():
        parity_errors.append("bilingual overview file is missing")
    elif validate_bilingual_parity(overview_en.read_text(encoding="utf-8"), overview_zh.read_text(encoding="utf-8")):
        parity_errors.append("bilingual overview structure differs")
    if parity_errors:
        raise SourceDataError("bilingual validation failed:\n- " + "\n- ".join(parity_errors))
    return {"professors": len(professors), "publications": len(publications), "bilingualDocuments": len(documents)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cutoff", required=True)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    result = validate(args.root, args.cutoff)
    print(f"{result['professors']}/{result['professors']}")


if __name__ == "__main__":
    main()
