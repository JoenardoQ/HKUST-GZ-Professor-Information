#!/usr/bin/env python3
"""Validate normalized data, English output, and English/Chinese document parity."""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

try:
    from professor_information import (
        SourceDataError, _chinese_overview_markdown, _chinese_profile_markdown,
        _chinese_publications_markdown, _normalized_title, _overview_markdown,
        _profile_markdown, _publications_markdown, build_translation_memory_translator,
        in_window, validate_bilingual_parity, validate_frozen_english,
        validate_research_analysis, validate_research_subdirections,
        validate_retrieval_statuses, validate_release_window,
    )
except ModuleNotFoundError:
    from scripts.professor_information import (
    SourceDataError,
    _chinese_overview_markdown,
    _chinese_profile_markdown,
    _chinese_publications_markdown,
    _normalized_title,
    _overview_markdown,
    _profile_markdown,
    _publications_markdown,
    build_translation_memory_translator,
    in_window,
    validate_bilingual_parity,
    validate_frozen_english,
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

PUBLISHED_BYTE_LIMITS = {
    "directory-index": 524_288,
    "profile": 16_384,
    "publications": 262_144,
    "overview": 131_072,
}


def validate_published_bytes(data: bytes, limit: int, label: str) -> None:
    if len(data) > limit:
        raise SourceDataError(f"{label} exceeds published byte limit {limit}")


def _read_published_text(path: Path, kind: str) -> str:
    data = path.read_bytes()
    validate_published_bytes(data, PUBLISHED_BYTE_LIMITS[kind], str(path))
    return data.decode("utf-8")


def validate_self_review_release_report(root: Path, cutoff: str, artifact: dict) -> None:
    path = root / f"reports/{cutoff}-subdirection-self-review.json"
    report = load_json(path)
    if not isinstance(report, dict) or report.get("schemaVersion") != 1 or report.get("cutoff") != cutoff:
        raise SourceDataError("subdirection self-review report metadata differs")
    results = report.get("results")
    if not isinstance(results, list):
        raise SourceDataError("subdirection self-review results must be a list")
    records = {str(item["officialProfileId"]): item for item in artifact.get("professors") or []}
    seen = set()
    for result in results:
        profile_id = str(result.get("officialProfileId") or "") if isinstance(result, dict) else ""
        if not profile_id or profile_id in seen:
            raise SourceDataError("duplicate or invalid subdirection self-review result")
        seen.add(profile_id)
        record = records.get(profile_id)
        if record is None or result.get("disposition") == "block" or result.get("disposition") != record.get("reviewStatus"):
            raise SourceDataError("subdirection self-review disposition differs or is blocked")
        expected_directions = {item["id"]: item["reviewStatus"] for item in record["subdirections"]}
        reviews = result.get("directionReviews")
        actual_directions = {
            str(item.get("subdirectionId") or ""): item.get("disposition")
            for item in reviews or [] if isinstance(item, dict)
        }
        if len(actual_directions) != len(reviews or []) or actual_directions != expected_directions:
            raise SourceDataError("subdirection self-review direction results differ")
        assignments = record["publicationAssignments"]
        expected_counts = (len(assignments), sum(bool(item["subdirectionIds"]) for item in assignments))
        actual_counts = (result.get("publicationAssignmentCount"), result.get("assignedPublicationCount"))
        if actual_counts != expected_counts or result.get("unassignedPublicationCount") != expected_counts[0] - expected_counts[1]:
            raise SourceDataError("subdirection self-review assignment counts differ")
    if seen != set(records):
        raise SourceDataError("subdirection self-review is missing professor IDs")


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
    directory_path = root / "data/directory-index.json"
    validate_published_bytes(directory_path.read_bytes(), PUBLISHED_BYTE_LIMITS["directory-index"], str(directory_path))
    manifest = load_json(root / "data/manifest.json")
    search = load_json(root / "data/search-index.json")
    directory = load_json(root / "data/directory-index.json")
    professors = load_json(root / "data/professors.json")
    research_analysis = load_json(root / "data/research-analysis.json")
    publications = load_json(root / "data/publications.json")
    subdirections = load_json(root / "data/research-subdirections.json")
    translations = load_json(root / "data/translations.zh-CN.json")
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
    validate_self_review_release_report(root, cutoff, subdirections)
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
    professor_by_slug = {record["slug"]: record for record in professors}
    expected_slugs = {record["slug"] for record in professors}
    if set(documents) != expected_slugs:
        raise SourceDataError("manifest identities do not match professor data")
    for slug, paths in documents.items():
        _validate_safe_document_paths(slug, paths)
    validate_frozen_english(
        root, manifest, professors, research_analysis, publications,
        subdirections, window["start"], cutoff,
    )
    if not isinstance(translations, dict):
        raise SourceDataError("translation memory must contain a JSON object")
    translator = build_translation_memory_translator(translations)
    subdirection_records = {
        str(record["officialProfileId"]): record
        for record in subdirections["professors"]
    }
    publications_by_professor: dict[str, list[dict]] = defaultdict(list)
    for publication in publications:
        if in_window(window["start"], cutoff, publication["effectiveDate"]):
            publications_by_professor[str(publication["officialProfileId"])].append(publication)

    for slug, paths in documents.items():
        document_texts = {}
        for document_kind in _DOCUMENT_SUFFIXES:
            english_path = root / paths[document_kind]["en"]
            chinese_path = root / paths[document_kind]["zhCN"]
            if not english_path.is_file() or not chinese_path.is_file():
                raise SourceDataError(f"manifest-selected {document_kind} document is missing for {slug}")
            document_texts[(document_kind, "en")] = _read_published_text(english_path, document_kind)
            document_texts[(document_kind, "zhCN")] = _read_published_text(chinese_path, document_kind)
            parity_errors = validate_bilingual_parity(document_texts[(document_kind, "en")], document_texts[(document_kind, "zhCN")])
            if parity_errors:
                raise SourceDataError(
                    f"bilingual document structure differs for {slug} {document_kind}: "
                    + "; ".join(parity_errors)
                )
        professor = professor_by_slug[slug]
        profile_id = str(professor["officialProfileId"])
        professor_publications = sorted(
            publications_by_professor[profile_id],
            key=lambda item: (item["effectiveDate"], _normalized_title(item["title"])),
            reverse=True,
        )
        subdirection_record = subdirection_records[profile_id]
        assignments = {
            item["publicationId"]: item
            for item in subdirection_record["publicationAssignments"]
        }
        directions_by_id = {
            item["id"]: item for item in subdirection_record["subdirections"]
        }
        expected_profile_en = _profile_markdown(
            professor, analysis_by_professor[profile_id], subdirection_record["subdirections"]
        )
        expected_profile_zh = _chinese_profile_markdown(
            professor, analysis_by_professor[profile_id], subdirection_record["subdirections"], translator
        )
        if document_texts[("profile", "en")] != expected_profile_en or document_texts[("profile", "zhCN")] != expected_profile_zh:
            raise SourceDataError(f"canonical profile document differs for {slug}")
        expected_english = _publications_markdown(
            professor, analysis_by_professor[profile_id], professor_publications,
            assignments, {key: value["nameEn"] for key, value in directions_by_id.items()},
            window["start"], cutoff,
        )
        expected_chinese = _chinese_publications_markdown(
            professor, analysis_by_professor[profile_id], professor_publications,
            assignments, directions_by_id, window["start"], cutoff, translator,
        )
        if (
            document_texts[("publications", "en")] != expected_english
            or document_texts[("publications", "zhCN")] != expected_chinese
        ):
            raise SourceDataError(f"canonical publication document differs for {slug}")
    overview_en = root / "All_Prof_Info.md"
    overview_zh = root / "All_Prof_Info.zh-CN.md"
    if not overview_en.is_file() or not overview_zh.is_file():
        raise SourceDataError("bilingual overview document is missing")
    overview_en_text = _read_published_text(overview_en, "overview")
    overview_zh_text = _read_published_text(overview_zh, "overview")
    overview_errors = validate_bilingual_parity(overview_en_text, overview_zh_text)
    if overview_errors:
        raise SourceDataError("bilingual overview structure differs: " + "; ".join(overview_errors))
    ordered = sorted(
        professors,
        key=lambda item: (
            unicodedata.normalize("NFKC", str(item["nameEn"])).casefold(),
            item["officialProfileId"],
        ),
    )
    publication_counts_for_overview = {
        profile_id: len(publications_by_professor.get(profile_id, [])) for profile_id in professor_ids
    }
    expected_overview_en = _overview_markdown(ordered, publication_counts_for_overview, window["start"], cutoff)
    expected_overview_zh = _chinese_overview_markdown(
        ordered, publication_counts_for_overview, documents, window["start"], cutoff, translator
    )
    if overview_en_text != expected_overview_en or overview_zh_text != expected_overview_zh:
        raise SourceDataError("canonical overview document differs")
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
