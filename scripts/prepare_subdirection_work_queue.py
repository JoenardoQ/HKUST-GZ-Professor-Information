#!/usr/bin/env python3
"""Prepare and deterministically resolve the reviewed English sub-direction queue."""

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
        validate_research_subdirections,
    )
    from scripts.validate_subdirection_review import (
        build_subdirection_review_report,
        validate_subdirection_review_report,
    )
except ModuleNotFoundError:  # Direct script execution adds scripts/, not the repository root.
    from professor_information import (  # type: ignore[no-redef]
        SourceDataError,
        _valid_evidence,
        _valid_official_subdirection_evidence,
        clean,
        in_window,
        validate_research_analysis,
        validate_research_subdirections,
    )
    from validate_subdirection_review import (  # type: ignore[no-redef]
        build_subdirection_review_report,
        validate_subdirection_review_report,
    )


UNASSIGNED_REASON = (
    "The indexed metadata for this publication does not support a reliable "
    "assignment to the reviewed sub-directions."
)
LIMITED_DIRECTION_NAME = "Evidence-limited research profile"
LIMITED_EXPLANATION = [
    "The approved release data does not support a specific research sub-direction for this professor.",
    "The official profile identifies the professor, while no retrieved publication metadata provides a reviewed thematic match.",
    "This record is limited to documenting insufficient approved evidence and should not be read as a description of the professor's broader research agenda.",
]


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


def _direction_id(name: str, used: set[str]) -> str:
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    base = "-".join(re.findall(r"[a-z0-9]+", normalized.casefold())) or "research-direction"
    candidate = base
    suffix = 2
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
        approved = _valid_evidence(value) or (
            allow_official and _valid_official_subdirection_evidence(value)
        )
        if not approved:
            raise SourceDataError("work queue contains evidence outside approved sources")
        result.append(value)
    return sorted(set(result))


def build_subdirection_work_queue(
    professors: list[dict[str, Any]],
    analyses: list[dict[str, Any]],
    publications: list[dict[str, Any]],
    cutoff: str,
) -> dict[str, Any]:
    """Build a deterministic, professor-scoped queue from canonical English inputs."""
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

    analysis_by_id: dict[str, dict[str, Any]] = {}
    for analysis in analyses:
        profile_id = str(analysis["officialProfileId"])
        analysis_by_id[profile_id] = analysis

    publications_by_professor: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen_publications: set[tuple[str, str]] = set()
    for publication in publications:
        if not isinstance(publication, dict):
            raise SourceDataError("publication record must be an object")
        profile_id = clean(publication.get("officialProfileId"))
        publication_id = clean(publication.get("publicationId"))
        if not profile_id or profile_id not in professor_ids:
            raise SourceDataError("publication references an unknown professor")
        scoped_publication_id = (profile_id, publication_id or "")
        if not publication_id or scoped_publication_id in seen_publications:
            raise SourceDataError("publication IDs must be unique within each professor")
        seen_publications.add(scoped_publication_id)
        if not clean(publication.get("title")):
            raise SourceDataError("publication title must be non-empty")
        effective_date = str(publication.get("effectiveDate") or "")
        if not in_window(start, cutoff, effective_date):
            raise SourceDataError("publication falls outside the release window")
        if not isinstance(publication.get("keywords"), list) or any(
            not isinstance(value, str) for value in publication["keywords"]
        ):
            raise SourceDataError("publication keywords must be a list of strings")
        evidence_urls = _approved_urls(publication.get("evidenceUrls"))
        if not evidence_urls:
            raise SourceDataError("publication requires approved evidence")
        publications_by_professor[profile_id].append({
            "publicationId": publication_id,
            "title": clean(publication.get("title")),
            "effectiveDate": effective_date,
            "publicationType": clean(publication.get("publicationType")),
            "venue": clean(publication.get("venue")),
            "keywords": list(publication["keywords"]),
            "evidenceUrls": evidence_urls,
        })

    queued_professors: list[dict[str, Any]] = []
    for professor in sorted(professors, key=_professor_sort_key):
        profile_id = str(professor["officialProfileId"])
        analysis = analysis_by_id[profile_id]
        official_urls = _approved_urls(
            [
                url
                for url in (professor.get("officialProfileUrl"), professor.get("permaLink"))
                if url
            ],
            allow_official=True,
        )
        queued_professors.append({
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
            "publications": sorted(
                publications_by_professor.get(profile_id, []),
                key=_publication_sort_key,
            ),
        })

    return {
        "schemaVersion": 1,
        "cutoff": cutoff,
        "window": {"start": start, "end": cutoff, "inclusive": True},
        "professors": queued_professors,
    }


def build_research_subdirections(queue: dict[str, Any]) -> dict[str, Any]:
    """Resolve only exact analysis-topic/publication-metadata matches into directions."""
    records: list[dict[str, Any]] = []
    for item in queue.get("professors") or []:
        profile_id = str(item["officialProfileId"])
        publications = item["publications"]
        candidate_names: list[str] = []
        seen_names: set[str] = set()
        analysis = item["researchAnalysis"]
        for raw_name in [*analysis["researchInterests"], *analysis["researchAreas"]]:
            name = clean(raw_name)
            normalized = unicodedata.normalize("NFKC", name or "").casefold()
            if not name or normalized in seen_names:
                continue
            seen_names.add(normalized)
            if any(
                normalized
                in {
                    unicodedata.normalize("NFKC", keyword).casefold()
                    for keyword in publication["keywords"]
                }
                for publication in publications
            ):
                candidate_names.append(name)
            if len(candidate_names) == 3:
                break

        used_ids: set[str] = set()
        directions: list[dict[str, Any]] = []
        direction_matches: dict[str, set[str]] = {}
        for name in candidate_names:
            normalized = unicodedata.normalize("NFKC", name).casefold()
            matches = [
                publication
                for publication in publications
                if normalized
                in {
                    unicodedata.normalize("NFKC", keyword).casefold()
                    for keyword in publication["keywords"]
                }
            ]
            direction_id = _direction_id(name, used_ids)
            direction_matches[direction_id] = {
                publication["publicationId"] for publication in matches
            }
            directions.append({
                "id": direction_id,
                "nameEn": name,
                "explanationEn": [
                    f"{name} is a research sub-direction identified from subject metadata in this professor's release-window publications.",
                    f"The indexed metadata assigns this topic to {len(matches)} owned publication{'s' if len(matches) != 1 else ''} in the {queue['window']['start']} through {queue['window']['end']} release window.",
                    "This grouping reflects the available publication metadata and does not establish activity outside the reviewed release window.",
                ],
                "evidencePublicationIds": [
                    publication["publicationId"] for publication in matches
                ],
                "evidenceUrls": sorted({
                    url
                    for publication in matches
                    for url in publication["evidenceUrls"]
                }),
                "reviewStatus": "pass",
            })

        if not directions:
            if not item["officialEvidenceUrls"]:
                raise SourceDataError(
                    f"professor {profile_id} lacks official evidence for a limited direction"
                )
            direction_id = _direction_id(LIMITED_DIRECTION_NAME, used_ids)
            directions = [{
                "id": direction_id,
                "nameEn": LIMITED_DIRECTION_NAME,
                "explanationEn": list(LIMITED_EXPLANATION),
                "evidencePublicationIds": [],
                "evidenceUrls": list(item["officialEvidenceUrls"]),
                "reviewStatus": "limited",
                "limitationEn": LIMITED_EXPLANATION[2],
            }]
            review_status = "limited"
        else:
            review_status = "pass"

        assignments: list[dict[str, Any]] = []
        for publication in publications:
            assigned_ids = [
                direction["id"]
                for direction in directions
                if publication["publicationId"]
                in direction_matches.get(direction["id"], set())
            ]
            assignments.append({
                "publicationId": publication["publicationId"],
                "subdirectionIds": assigned_ids,
                "unassignedReasonEn": None if assigned_ids else UNASSIGNED_REASON,
            })
        records.append({
            "officialProfileId": profile_id,
            "reviewStatus": review_status,
            "subdirections": directions,
            "publicationAssignments": assignments,
        })
    return {
        "schemaVersion": 1,
        "cutoff": queue.get("cutoff"),
        "professors": records,
    }


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SourceDataError(f"cannot read valid JSON from {path}") from error


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--cutoff", required=True)
    args = parser.parse_args()
    root = args.root
    professors = _load_json(root / "data/professors.json")
    analyses = _load_json(root / "data/research-analysis.json")
    publications = _load_json(root / "data/publications.json")
    queue = build_subdirection_work_queue(
        professors, analyses, publications, args.cutoff
    )
    artifact = build_research_subdirections(queue)
    professor_ids = {str(item["officialProfileId"]) for item in professors}
    publication_owners: dict[str, set[str]] = defaultdict(set)
    for publication in publications:
        publication_owners[publication["publicationId"]].add(
            str(publication["officialProfileId"])
        )
    validate_research_subdirections(artifact, professor_ids, publication_owners)
    review = build_subdirection_review_report(artifact)
    validate_subdirection_review_report(
        review, artifact, professor_ids, publications
    )
    _write_json(
        root / f"reports/{args.cutoff}-subdirection-work-queue.json", queue
    )
    _write_json(root / "data/research-subdirections.json", artifact)
    _write_json(
        root / f"reports/{args.cutoff}-subdirection-self-review.json", review
    )
    print(f"{len(artifact['professors'])}/{len(professors)}")


if __name__ == "__main__":
    main()
