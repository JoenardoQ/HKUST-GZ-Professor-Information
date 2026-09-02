#!/usr/bin/env python3
"""Validate normalized data, English output, and English/Chinese document parity."""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from professor_information import (
    SourceDataError,
    in_window,
    validate_research_analysis,
    validate_research_subdirections,
    validate_retrieval_statuses,
    validate_release_window,
)


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SourceDataError(f"cannot read valid JSON from {path}") from error


_DOCUMENT_SUFFIXES = {
    "profile": {"en": ".md", "zhCN": ".zh-CN.md"},
    "publications": {"en": ".publications.md", "zhCN": ".publications.zh-CN.md"},
}
_DIRECTORY_FIELDS = {
    "officialProfileId", "slug", "nameZh", "nameEn", "email", "phone",
    "titles", "hubs", "units", "researchFields",
    "subdirectionNames", "publicationCount", "lastVerifiedOn",
}
_SAFE_DIRECTORY_GROUP = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


def _record_map(records: object, label: str) -> dict[str, dict]:
    if not isinstance(records, list):
        raise SourceDataError(f"{label} records must be a list")
    result: dict[str, dict] = {}
    for record in records:
        if not isinstance(record, dict):
            raise SourceDataError(f"{label} record must be an object")
        profile_id = str(record.get("officialProfileId") or "")
        if not profile_id or profile_id in result:
            raise SourceDataError(f"{label} identities are not unique")
        result[profile_id] = record
    return result


def _validate_safe_document_paths(slug: str, paths: object) -> None:
    if not isinstance(paths, dict) or set(paths) != set(_DOCUMENT_SUFFIXES):
        raise SourceDataError(f"manifest record {slug} must contain exactly four document paths")
    for document_kind, language_suffixes in _DOCUMENT_SUFFIXES.items():
        language_paths = paths.get(document_kind)
        if not isinstance(language_paths, dict) or set(language_paths) != set(language_suffixes):
            raise SourceDataError(f"manifest record {slug} has an invalid {document_kind} path group")
        for language, suffix in language_suffixes.items():
            value = language_paths[language]
            path = PurePosixPath(value) if isinstance(value, str) else None
            parsed = urlsplit(value) if isinstance(value, str) else None
            if (
                not isinstance(value, str)
                or not value
                or "\\" in value
                or "%" in value
                or parsed.scheme
                or parsed.netloc
                or parsed.query
                or parsed.fragment
                or path.is_absolute()
                or path.as_posix() != value
                or len(path.parts) != 3
                or path.parts[0] != "professors"
                or not _SAFE_DIRECTORY_GROUP.fullmatch(path.parts[1])
                or path.name != f"{slug}{suffix}"
            ):
                raise SourceDataError(f"manifest record {slug} has an unsafe {document_kind}.{language} Markdown path")


def validate(root: Path, cutoff: str) -> dict[str, int]:
    manifest = load_json(root / "data/manifest.json")
    search = load_json(root / "data/search-index.json")
    directory = load_json(root / "data/directory-index.json")
    professors = load_json(root / "data/professors.json")
    research_analysis = load_json(root / "data/research-analysis.json")
    publications = load_json(root / "data/publications.json")
    subdirections = load_json(root / "data/research-subdirections.json")
    if not isinstance(professors, list) or not isinstance(publications, list) or not isinstance(research_analysis, list):
        raise SourceDataError("canonical professor, publication, and analysis data must be lists")
    professor_ids = {str(item["officialProfileId"]) for item in professors}
    if len(professor_ids) != len(professors):
        raise SourceDataError("professor identities are not unique")
    validate_research_analysis(research_analysis, professor_ids)
    validate_retrieval_statuses(research_analysis)
    analysis_by_professor = _record_map(research_analysis, "research analysis")
    if set(analysis_by_professor) != professor_ids:
        raise SourceDataError("research analysis identities do not match professor data")
    publication_owners: dict[str, set[str]] = defaultdict(set)
    for publication in publications:
        profile_id = str(publication.get("officialProfileId") or "")
        publication_id = str(publication.get("publicationId") or "")
        if profile_id not in professor_ids:
            raise SourceDataError("publication references an unknown professor")
        if not publication_id:
            raise SourceDataError("publication is missing an identity")
        publication_owners[publication_id].add(profile_id)
    validate_research_subdirections(subdirections, professor_ids, publication_owners)
    if any(artifact.get("cutoff") != cutoff for artifact in (manifest, search, directory, subdirections)):
        raise SourceDataError("release artifact cutoff mismatch")
    window = manifest.get("window")
    if not isinstance(window, dict) or window.get("end") != cutoff or window.get("inclusive") is not True:
        raise SourceDataError("manifest release window is invalid")
    try:
        validate_release_window(str(window.get("start")), cutoff)
    except ValueError as error:
        raise SourceDataError("manifest release window is invalid") from error
    if search.get("window") != window or directory.get("window") != window:
        raise SourceDataError("release artifact windows do not match")
    if manifest.get("schemaVersion") != 2:
        raise SourceDataError("manifest requires schema version 2")
    if search.get("schemaVersion") != 1 or directory.get("schemaVersion") != 1:
        raise SourceDataError("search and directory indexes require schema version 1")
    documents = manifest.get("documents")
    if not isinstance(documents, dict) or len(documents) != len(professors):
        raise SourceDataError("manifest document count does not match professor data")
    professor_by_id = _record_map(professors, "professor")
    expected_slugs = {record["slug"] for record in professors}
    if set(documents) != expected_slugs:
        raise SourceDataError("manifest identities do not match professor data")
    for slug, paths in documents.items():
        _validate_safe_document_paths(slug, paths)
    expected_counts = {
        "professors": len(professors),
        "publications": sum(1 for item in publications if in_window(window["start"], cutoff, item["effectiveDate"])),
        "documents": len(professors),
    }
    if manifest.get("counts") != expected_counts:
        raise SourceDataError("manifest counts do not match canonical data")
    search_by_professor = _record_map(search.get("records"), "search index")
    directory_by_professor = _record_map(directory.get("records"), "directory index")
    if set(search_by_professor) != professor_ids or set(directory_by_professor) != professor_ids:
        raise SourceDataError("index identities do not match professor data")
    subdirections_by_professor = {
        str(record["officialProfileId"]): record["subdirections"]
        for record in subdirections["professors"]
    }
    publication_counts: dict[str, int] = defaultdict(int)
    for publication in publications:
        if in_window(window["start"], cutoff, publication["effectiveDate"]):
            publication_counts[str(publication["officialProfileId"])] += 1
    for profile_id, professor in professor_by_id.items():
        analysis = analysis_by_professor[profile_id]
        expected_fields = sorted(set(analysis["researchInterests"] + analysis["researchAreas"]))
        expected_subdirections = sorted(
            {direction["nameEn"] for direction in subdirections_by_professor[profile_id]},
            key=str.casefold,
        )
        search_record = search_by_professor[profile_id]
        if search_record.get("slug") != professor["slug"] or search_record.get("researchDirections") != expected_fields:
            raise SourceDataError("search index identity or research fields do not match canonical data")
        directory_record = directory_by_professor[profile_id]
        if set(directory_record) != _DIRECTORY_FIELDS:
            raise SourceDataError("directory index contains unsupported payload fields")
        expected_directory = {
            "officialProfileId": profile_id,
            "slug": professor["slug"],
            "nameZh": professor["nameZh"] or "",
            "nameEn": professor["nameEn"],
            "email": professor["email"],
            "phone": professor["phone"],
            "titles": sorted({item["title"] for item in professor["affiliations"] if item.get("title")}),
            "hubs": sorted({item["hub"] for item in professor["affiliations"] if item.get("hub")}),
            "units": sorted({item["unit"] for item in professor["affiliations"] if item.get("unit")}),
            "researchFields": expected_fields,
            "subdirectionNames": expected_subdirections,
            "publicationCount": publication_counts[profile_id],
            "lastVerifiedOn": professor["lastVerifiedOn"],
        }
        if directory_record != expected_directory:
            raise SourceDataError("directory index does not match canonical professor data")
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
