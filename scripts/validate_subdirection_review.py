#!/usr/bin/env python3
"""Validate reviewed English professor sub-directions and compact review results."""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

try:
    from scripts.professor_information import (
        SourceDataError,
        clean,
        validate_research_subdirections,
    )
except ModuleNotFoundError:  # Direct script execution adds scripts/, not the repository root.
    from professor_information import (  # type: ignore[no-redef]
        SourceDataError,
        clean,
        validate_research_subdirections,
    )


_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_REVIEW_STATUSES = frozenset({"pass", "limited", "block"})


def build_subdirection_review_report(artifact: dict[str, Any]) -> dict[str, Any]:
    """Build the compact one-result-per-professor review ledger."""
    results: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    for record in artifact.get("professors") or []:
        status = record.get("reviewStatus")
        status_counts[str(status)] += 1
        result = {
            "officialProfileId": record.get("officialProfileId"),
            "reviewStatus": status,
            "subdirectionCount": len(record.get("subdirections") or []),
            "publicationAssignmentCount": len(
                record.get("publicationAssignments") or []
            ),
        }
        limitations = [
            direction.get("limitationEn")
            for direction in record.get("subdirections") or []
            if direction.get("reviewStatus") == "limited"
        ]
        if limitations:
            result["limitationsEn"] = limitations
        results.append(result)
    return {
        "schemaVersion": 1,
        "cutoff": artifact.get("cutoff"),
        "summary": {
            "total": len(results),
            "pass": status_counts["pass"],
            "limited": status_counts["limited"],
            "block": status_counts["block"],
        },
        "results": results,
    }


def _normalized(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _require_english_only(record: dict[str, Any]) -> None:
    for direction in record.get("subdirections") or []:
        values = [direction.get("nameEn"), *(direction.get("explanationEn") or [])]
        if "limitationEn" in direction:
            values.append(direction.get("limitationEn"))
        if any(isinstance(value, str) and _CJK.search(value) for value in values):
            raise SourceDataError("research subdirections must contain English-only presentation text")
    for assignment in record.get("publicationAssignments") or []:
        reason = assignment.get("unassignedReasonEn")
        if isinstance(reason, str) and _CJK.search(reason):
            raise SourceDataError("research subdirections must contain English-only presentation text")


def validate_subdirection_review_report(
    report: dict[str, Any],
    artifact: dict[str, Any],
    professor_ids: set[str],
    publications: list[dict[str, Any]],
) -> None:
    """Validate complete review coverage and exact metadata-based semantic support."""
    if (
        not isinstance(report, dict)
        or type(report.get("schemaVersion")) is not int
        or report.get("schemaVersion") != 1
    ):
        raise SourceDataError("subdirection review requires schema version 1")
    if report.get("cutoff") != artifact.get("cutoff"):
        raise SourceDataError("subdirection review cutoff does not match the artifact")
    records = artifact.get("professors")
    results = report.get("results")
    if not isinstance(records, list) or not isinstance(results, list):
        raise SourceDataError("subdirection review records and results must be lists")

    publication_by_owner_id: dict[tuple[str, str], dict[str, Any]] = {}
    publication_owners: dict[str, set[str]] = defaultdict(set)
    for publication in publications:
        if not isinstance(publication, dict):
            raise SourceDataError("review publication must be an object")
        publication_id = clean(publication.get("publicationId"))
        owner = clean(publication.get("officialProfileId"))
        scoped_publication_id = (owner or "", publication_id or "")
        if not publication_id or scoped_publication_id in publication_by_owner_id or not owner:
            raise SourceDataError("review publications require IDs unique within each professor")
        publication_by_owner_id[scoped_publication_id] = publication
        publication_owners[publication_id].add(owner)
    validate_research_subdirections(artifact, professor_ids, publication_owners)

    candidate_by_id = {
        str(record.get("officialProfileId")): record for record in records
    }
    if set(candidate_by_id) != professor_ids or len(candidate_by_id) != len(records):
        raise SourceDataError("review candidate professor coverage is incomplete or duplicated")

    seen_results: set[str] = set()
    for result in results:
        if not isinstance(result, dict):
            raise SourceDataError("subdirection review result must be an object")
        profile_id = clean(result.get("officialProfileId"))
        if not profile_id or profile_id not in professor_ids:
            raise SourceDataError("subdirection review references an unknown professor")
        if profile_id in seen_results:
            raise SourceDataError(f"duplicate subdirection review result for professor {profile_id}")
        seen_results.add(profile_id)
        status = result.get("reviewStatus")
        if status not in _REVIEW_STATUSES:
            raise SourceDataError(f"professor {profile_id} review has an invalid status")
        if status == "block":
            raise SourceDataError(f"professor {profile_id} review is blocked")
        candidate = candidate_by_id[profile_id]
        if status != candidate.get("reviewStatus"):
            raise SourceDataError(f"professor {profile_id} review status does not match the artifact")
        if result.get("subdirectionCount") != len(candidate["subdirections"]):
            raise SourceDataError(f"professor {profile_id} subdirection review count differs")
        if result.get("publicationAssignmentCount") != len(
            candidate["publicationAssignments"]
        ):
            raise SourceDataError(f"professor {profile_id} assignment review count differs")
        limitations = [
            direction["limitationEn"]
            for direction in candidate["subdirections"]
            if direction["reviewStatus"] == "limited"
        ]
        if status == "limited" and result.get("limitationsEn") != limitations:
            raise SourceDataError(f"professor {profile_id} limited review requires explicit limitations")
        if status == "pass" and "limitationsEn" in result:
            raise SourceDataError(f"professor {profile_id} pass review cannot carry limitations")

    missing = professor_ids - seen_results
    if missing:
        raise SourceDataError(
            "subdirection review is missing professor IDs: " + ", ".join(sorted(missing))
        )

    actual_summary = {
        "total": len(results),
        "pass": sum(result["reviewStatus"] == "pass" for result in results),
        "limited": sum(result["reviewStatus"] == "limited" for result in results),
        "block": sum(result["reviewStatus"] == "block" for result in results),
    }
    if report.get("summary") != actual_summary:
        raise SourceDataError("subdirection review summary does not match its results")

    for record in records:
        _require_english_only(record)
        profile_id = str(record["officialProfileId"])
        direction_by_id = {
            direction["id"]: direction for direction in record["subdirections"]
        }
        for direction in record["subdirections"]:
            evidence_ids = direction["evidencePublicationIds"]
            if not evidence_ids:
                continue
            expected_urls: set[str] = set()
            normalized_name = _normalized(direction["nameEn"])
            for publication_id in evidence_ids:
                publication = publication_by_owner_id[(profile_id, publication_id)]
                keywords = publication.get("keywords")
                if not isinstance(keywords, list) or normalized_name not in {
                    _normalized(keyword)
                    for keyword in keywords
                    if isinstance(keyword, str)
                }:
                    raise SourceDataError(
                        f"subdirection {direction['id']} lacks matching subject metadata"
                    )
                expected_urls.update(publication.get("evidenceUrls") or [])
            if set(direction["evidenceUrls"]) != expected_urls:
                raise SourceDataError(
                    f"subdirection {direction['id']} evidence URLs do not resolve to its publications"
                )

        for assignment in record["publicationAssignments"]:
            publication = publication_by_owner_id[
                (profile_id, assignment["publicationId"])
            ]
            keywords = {
                _normalized(keyword)
                for keyword in publication.get("keywords") or []
                if isinstance(keyword, str)
            }
            expected_ids = [
                direction_id
                for direction_id, direction in direction_by_id.items()
                if assignment["publicationId"] in direction["evidencePublicationIds"]
                and _normalized(direction["nameEn"]) in keywords
            ]
            if assignment["subdirectionIds"] != expected_ids:
                raise SourceDataError(
                    f"publication {assignment['publicationId']} assignment is not supported by subject metadata"
                )
        owned_ids = {
            publication_id
            for publication_id, owners in publication_owners.items()
            if profile_id in owners
        }
        if {item["publicationId"] for item in record["publicationAssignments"]} != owned_ids:
            raise SourceDataError(f"professor {profile_id} review does not cover every publication")


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SourceDataError(f"cannot read valid JSON from {path}") from error


def validate(root: Path, cutoff: str) -> dict[str, int]:
    professors = _load_json(root / "data/professors.json")
    publications = _load_json(root / "data/publications.json")
    artifact = _load_json(root / "data/research-subdirections.json")
    report = _load_json(root / f"reports/{cutoff}-subdirection-self-review.json")
    if not isinstance(professors, list) or not isinstance(publications, list):
        raise SourceDataError("professor and publication data must be lists")
    professor_ids = {str(item.get("officialProfileId")) for item in professors}
    if len(professor_ids) != len(professors):
        raise SourceDataError("professor identities are not unique")
    if artifact.get("cutoff") != cutoff:
        raise SourceDataError("research subdirections cutoff does not match the requested release")
    validate_subdirection_review_report(
        report, artifact, professor_ids, publications
    )
    return {
        "professors": len(professors),
        "publications": len(publications),
        "reviewed": len(report["results"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--cutoff", required=True)
    args = parser.parse_args()
    result = validate(args.root, args.cutoff)
    print(f"{result['reviewed']}/{result['professors']}")


if __name__ == "__main__":
    main()
