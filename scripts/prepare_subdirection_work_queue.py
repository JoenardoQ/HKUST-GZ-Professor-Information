#!/usr/bin/env python3
"""Prepare canonical evidence and unreviewed English sub-direction candidates."""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

try:
    from scripts.professor_information import (
        SourceDataError,
        _valid_evidence,
        _valid_official_subdirection_evidence,
        clean,
        in_window,
        validate_research_analysis,
    )
except ModuleNotFoundError:  # Direct script execution adds scripts/, not the repository root.
    from professor_information import (  # type: ignore[no-redef]
        SourceDataError,
        _valid_evidence,
        _valid_official_subdirection_evidence,
        clean,
        in_window,
        validate_research_analysis,
    )


def _professor_sort_key(record: dict[str, Any]) -> tuple[str, str]:
    return (
        unicodedata.normalize("NFKC", str(record.get("nameEn") or "")).casefold(),
        str(record.get("officialProfileId") or ""),
    )


def _publication_sort_key(record: dict[str, Any]) -> tuple[int, str, str]:
    try:
        ordinal = date.fromisoformat(str(record.get("effectiveDate"))).toordinal()
    except ValueError as error:
        raise SourceDataError("publication requires an ISO effective date") from error
    return (
        -ordinal,
        unicodedata.normalize("NFKC", str(record.get("title") or "")).casefold(),
        str(record.get("publicationId") or ""),
    )


def _ascii_text(value: str) -> str:
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().casefold()


def _direction_id(name: str, used: set[str]) -> str:
    base = "-".join(re.findall(r"[a-z0-9]+", _ascii_text(name))) or "research-direction"
    candidate, suffix = base, 2
    while candidate in used:
        candidate = f"{base}-{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def _approved_urls(values: Any, *, allow_official: bool = False) -> list[str]:
    if not isinstance(values, list):
        raise SourceDataError("evidence URLs must be a list")
    result: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise SourceDataError("evidence URL must be a string")
        if not _valid_evidence(value) and not (
            allow_official and _valid_official_subdirection_evidence(value)
        ):
            raise SourceDataError("work queue contains evidence outside approved sources")
        result.append(value)
    return sorted(set(result))


def build_subdirection_work_queue(
    professors: list[dict[str, Any]],
    analyses: list[dict[str, Any]],
    publications: list[dict[str, Any]],
    cutoff: str,
) -> dict[str, Any]:
    """Build deterministic professor-scoped evidence without Scholar-derived fields."""
    try:
        cutoff_date = date.fromisoformat(cutoff)
        start = cutoff_date.replace(year=cutoff_date.year - 2).isoformat()
    except ValueError as error:
        raise SourceDataError("subdirection cutoff must be an ISO date") from error
    if (cutoff_date.month, cutoff_date.day) not in {(3, 1), (9, 1)}:
        raise SourceDataError("subdirection cutoff must be March 1 or September 1")
    if not isinstance(professors, list) or not professors:
        raise SourceDataError("professor data must be a non-empty list")
    if not isinstance(analyses, list) or not isinstance(publications, list):
        raise SourceDataError("research analysis and publications must be lists")

    professor_by_id: dict[str, dict[str, Any]] = {}
    for professor in professors:
        if not isinstance(professor, dict):
            raise SourceDataError("professor record must be an object")
        profile_id = clean(professor.get("officialProfileId"))
        if not profile_id or profile_id in professor_by_id:
            raise SourceDataError("professor IDs must be present and unique")
        professor_by_id[profile_id] = professor
    professor_ids = set(professor_by_id)
    validate_research_analysis(analyses, professor_ids)
    analysis_by_id = {str(item["officialProfileId"]): item for item in analyses}

    publications_by_professor: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen_publications: set[tuple[str, str]] = set()
    for publication in publications:
        if not isinstance(publication, dict):
            raise SourceDataError("publication record must be an object")
        profile_id = clean(publication.get("officialProfileId"))
        publication_id = clean(publication.get("publicationId"))
        if not profile_id or profile_id not in professor_ids:
            raise SourceDataError("publication references an unknown professor")
        scoped_id = (profile_id, publication_id or "")
        if not publication_id or scoped_id in seen_publications:
            raise SourceDataError("publication IDs must be unique within each professor")
        seen_publications.add(scoped_id)
        title = clean(publication.get("title"))
        if not title:
            raise SourceDataError("publication title must be non-empty")
        effective_date = str(publication.get("effectiveDate") or "")
        if not in_window(start, cutoff, effective_date):
            raise SourceDataError("publication falls outside the release window")
        keywords = publication.get("keywords")
        if not isinstance(keywords, list) or any(not isinstance(value, str) for value in keywords):
            raise SourceDataError("publication keywords must be a list of strings")
        evidence_urls = _approved_urls(publication.get("evidenceUrls"))
        if not evidence_urls:
            raise SourceDataError("publication requires approved evidence")
        publications_by_professor[profile_id].append({
            "publicationId": publication_id,
            "title": title,
            "effectiveDate": effective_date,
            "publicationType": clean(publication.get("publicationType")),
            "venue": clean(publication.get("venue")),
            "keywords": list(keywords),
            "evidenceUrls": evidence_urls,
        })

    queued: list[dict[str, Any]] = []
    for professor in sorted(professors, key=_professor_sort_key):
        profile_id = str(professor["officialProfileId"])
        analysis = analysis_by_id[profile_id]
        official_urls = _approved_urls([
            url for url in (professor.get("officialProfileUrl"), professor.get("permaLink")) if url
        ], allow_official=True)
        queued.append({
            "officialProfileId": profile_id,
            "nameEn": clean(professor.get("nameEn")),
            "officialEvidenceUrls": official_urls,
            "researchAnalysis": {
                "researchInterests": list(analysis["researchInterests"]),
                "researchAreas": list(analysis["researchAreas"]),
                "keywords": list(analysis["keywords"]),
                "summaryEn": clean(analysis.get("summaryEn")),
                "status": analysis.get("status"),
                "notes": list(analysis["notes"]),
                "evidenceUrls": _approved_urls(analysis["evidenceUrls"]),
                "lastVerifiedOn": analysis.get("lastVerifiedOn"),
            },
            "publications": sorted(publications_by_professor.get(profile_id, []), key=_publication_sort_key),
        })
    return {
        "schemaVersion": 1,
        "cutoff": cutoff,
        "window": {"start": start, "end": cutoff, "inclusive": True},
        "professors": queued,
    }


def build_subdirection_candidates(queue: dict[str, Any]) -> dict[str, Any]:
    """Emit every normalized analysis topic without judging evidence or assignments."""
    candidate_records: list[dict[str, Any]] = []
    for item in queue.get("professors") or []:
        analysis = item["researchAnalysis"]
        topics: list[str] = []
        for raw in [*analysis["researchInterests"], *analysis["researchAreas"]]:
            topic = clean(raw)
            normalized = unicodedata.normalize("NFKC", topic or "").casefold()
            if topic and normalized not in {
                unicodedata.normalize("NFKC", value).casefold() for value in topics
            }:
                topics.append(topic)
        if not topics:
            topics = ["Evidence-limited research profile"]

        used_ids: set[str] = set()
        directions = [{
            "id": _direction_id(topic, used_ids),
            "nameEn": topic,
            "sourceNamesEn": [] if topic == "Evidence-limited research profile" else [topic],
        } for topic in topics]
        candidate_records.append({
            "officialProfileId": item["officialProfileId"],
            "subdirections": directions,
        })
    return {"schemaVersion": 1, "cutoff": queue.get("cutoff"), "professors": candidate_records}


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SourceDataError(f"cannot read valid JSON from {path}") from error


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--cutoff", required=True)
    args = parser.parse_args()
    root = args.root
    queue = build_subdirection_work_queue(
        _load_json(root / "data/professors.json"),
        _load_json(root / "data/research-analysis.json"),
        _load_json(root / "data/publications.json"),
        args.cutoff,
    )
    candidates = build_subdirection_candidates(queue)
    _write_json(root / f"reports/{args.cutoff}-subdirection-work-queue.json", queue)
    _write_json(root / f"reports/{args.cutoff}-subdirection-candidates.json", candidates)
    print(f"{len(candidates['professors'])}/{len(queue['professors'])}")


if __name__ == "__main__":
    main()
