#!/usr/bin/env python3
"""Independently review and validate English professor sub-directions."""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

try:
    from scripts.prepare_subdirection_work_queue import (
        build_subdirection_candidates,
        build_subdirection_work_queue,
    )
    from scripts.professor_information import (
        SourceDataError,
        clean,
        validate_research_subdirections,
    )
except ModuleNotFoundError:  # Direct script execution adds scripts/, not the repository root.
    from prepare_subdirection_work_queue import (  # type: ignore[no-redef]
        build_subdirection_candidates,
        build_subdirection_work_queue,
    )
    from professor_information import (  # type: ignore[no-redef]
        SourceDataError,
        clean,
        validate_research_subdirections,
    )


_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_REVIEW_STATUSES = frozenset({"pass", "limited", "block"})
_GENERIC_WORDS = frozenset({
    "a", "an", "and", "application", "applications", "approach", "approaches",
    "advanced", "based", "for", "in", "of", "on", "research", "study", "studies",
    "the", "to",
})
_WEAK_SINGLE_TOKENS = frozenset({
    "analysis", "approach", "architecture", "computational", "design", "development",
    "effect", "impact", "management", "material", "method", "model", "network",
    "optimization", "process", "system", "technology",
})
_UNASSIGNED_REASON = (
    "The reviewed evidence does not support a reliable assignment of this publication "
    "to the accepted sub-directions."
)


def _ascii(value: str) -> str:
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().casefold()


def _review_stem(word: str) -> str:
    if len(word) > 5 and word.endswith("ies"):
        return word[:-3] + "y"
    for suffix in ("ing", "ed"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[:-len(suffix)]
    if len(word) > 4 and word.endswith("s"):
        return word[:-1]
    return word


def _review_tokens(value: str, *, topical: bool = False) -> set[str]:
    tokens = {_review_stem(token) for token in re.findall(r"[a-z0-9]+", _ascii(value)) if len(token) >= 2}
    if topical:
        tokens -= {_review_stem(token) for token in _GENERIC_WORDS}
    return tokens


def _ratio(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _name_similarity(left: str, right: str) -> float:
    return _ratio(_review_tokens(left, topical=True), _review_tokens(right, topical=True))


def _review_title_support(topic: str, publication: dict[str, Any]) -> bool:
    """Independent title oracle; subject labels cannot certify themselves."""
    topic_tokens = _review_tokens(topic, topical=True)
    title = str(publication.get("title") or "")
    title_tokens = _review_tokens(title)
    overlap = topic_tokens & title_tokens
    topic_coverage = len(overlap) / len(topic_tokens) if topic_tokens else 0.0
    if topic_coverage >= 0.5 and (
        len(overlap) >= 2
        or any(len(token) >= 6 and token not in _WEAK_SINGLE_TOKENS for token in overlap)
    ):
        return True
    topic_ascii, title_ascii = _ascii(topic), _ascii(title)
    compact_title = re.sub(r"[^a-z0-9]", "", title_ascii)
    multimodal_signal = (
        "multimodal" in compact_title
        or "visionlanguageaction" in compact_title
        or bool(re.search(r"\bvla\b", title_ascii))
    )
    if "multimodal" in topic_ascii and multimodal_signal:
        return True
    if "robot" in topic_ascii and "social" not in topic_ascii and (
        "visionlanguageaction" in compact_title or bool(re.search(r"\bvla\b", title_ascii))
    ):
        return True
    if "battery" in topic_ascii and title_tokens & {
        "anode", "battery", "cathode", "lithium", "sodium", "zinc", "zn"
    }:
        return True
    if "nanoplatform" in topic_ascii and {"cancer", "theranostic"} & topic_tokens:
        nano_signal = bool(title_tokens & {"biomaterial", "nano", "nanoplatform", "photosensitizer"})
        cancer_signal = bool(title_tokens & {"antitumor", "cancer", "tumor"})
        if nano_signal and cancer_signal:
            return True
    if "3d" in topic_tokens and "3d" in title_tokens:
        return True
    if "artificial intelligence" in topic_ascii or re.search(r"\bai\b", topic_ascii):
        return "artificial intelligence" in title_ascii or bool(re.search(r"\bai\b", title_ascii))
    return False


def _direction_urls(publications: list[dict[str, Any]]) -> list[str]:
    return sorted({url for publication in publications for url in publication["evidenceUrls"]})


def _format_titles(publications: list[dict[str, Any]], *, descriptor: str = "matched") -> str:
    titles = [f"“{publication['title']}”" for publication in publications[:2]]
    if len(publications) == 1:
        return titles[0]
    if len(publications) == 2:
        return f"{titles[0]} and {titles[1]}"
    remainder = len(publications) - 2
    suffix = "publication" if remainder == 1 else "publications"
    return f"{titles[0]}, {titles[1]}, and {remainder} other {descriptor} {suffix}"


def _format_terms(values: list[str]) -> str:
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} and {values[1]}"
    return f"{values[0]}, {values[1]}, and {values[2]}"


def _relevant_analysis_terms(name: str, keywords: list[str]) -> list[str]:
    topic_tokens = _review_tokens(name, topical=True)
    relevant = [
        keyword for keyword in keywords
        if topic_tokens & _review_tokens(keyword, topical=True)
    ]
    if "energy" in _ascii(name):
        relevant = [
            keyword for keyword in keywords
            if re.search(r"\b(?:carbon|electricity|energy|warming)\b", _ascii(keyword))
        ]
    return (relevant or keywords)[:3]


def _has_semantic_duplicates(directions: list[dict[str, Any]]) -> bool:
    for left_index, left in enumerate(directions):
        left_ids = set(left.get("evidencePublicationIds") or [])
        for right in directions[left_index + 1:]:
            right_ids = set(right.get("evidencePublicationIds") or [])
            if _name_similarity(str(left.get("nameEn") or ""), str(right.get("nameEn") or "")) >= 0.55:
                return True
            if left_ids and right_ids and _ratio(left_ids, right_ids) >= 0.9:
                return True
    return False


def _professor_basis(
    publications: list[dict[str, Any]],
    direction_evidence: list[set[str]],
    has_explicit_conflict: bool,
    has_analysis_only: bool,
) -> str:
    if has_explicit_conflict:
        return "conflicting-record"
    if not publications:
        return "analysis-only" if has_analysis_only else "insufficient-evidence"
    matched = set().union(*direction_evidence) if direction_evidence else set()
    match_ratio = len(matched) / len(publications)
    disjoint_pairs = sum(
        not (direction_evidence[left] & direction_evidence[right])
        for left in range(len(direction_evidence))
        for right in range(left + 1, len(direction_evidence))
    )
    if len(publications) >= 8 and len(direction_evidence) >= 2 and disjoint_pairs >= 1:
        return "incoherent-publication-record"
    if match_ratio < 0.2:
        return "conflicting-record"
    return "publication-record"


def _review_direction(
    queue_item: dict[str, Any],
    candidate: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], set[str]]:
    analysis = queue_item["researchAnalysis"]
    publications = queue_item["publications"]
    publication_by_id = {item["publicationId"]: item for item in publications}
    topics = {
        str(value).casefold()
        for value in [*analysis["researchInterests"], *analysis["researchAreas"]]
    }
    basis = candidate.get("evidenceBasis")
    source_names = candidate.get("sourceNamesEn")
    if not isinstance(source_names, list) or any(not isinstance(name, str) for name in source_names):
        raise SourceDataError("candidate source names must be an English string list")

    expected_publications: list[dict[str, Any]] = []
    if basis == "publication-title-and-analysis-topic":
        if not source_names or any(name.casefold() not in topics for name in source_names):
            raise SourceDataError("publication-backed candidate is not derived from analysis topics")
        expected_publications = [
            publication for publication in publications
            if any(_review_title_support(name, publication) for name in source_names)
        ]
        if not expected_publications:
            raise SourceDataError("publication-backed candidate lacks independent title support")
        expected_ids = sorted(item["publicationId"] for item in expected_publications)
        expected_urls = _direction_urls(expected_publications)
    elif basis == "analysis-only":
        if publications or len(source_names) != 1 or source_names[0].casefold() not in topics:
            raise SourceDataError("analysis-only candidate conflicts with canonical evidence")
        expected_ids = []
        expected_urls = sorted(set(queue_item["officialEvidenceUrls"]) | set(analysis["evidenceUrls"]))
    elif basis == "conflicting-record":
        if not publications:
            raise SourceDataError("conflicting candidate requires retrieved publications")
        expected_publications = publications[:3]
        expected_ids = [item["publicationId"] for item in expected_publications]
        expected_urls = sorted(
            set(_direction_urls(expected_publications))
            | set(queue_item["officialEvidenceUrls"])
            | set(analysis["evidenceUrls"])
        )
    elif basis == "insufficient-evidence":
        if publications or source_names:
            raise SourceDataError("insufficient-evidence candidate contradicts canonical evidence")
        expected_ids = []
        expected_urls = list(queue_item["officialEvidenceUrls"])
    else:
        raise SourceDataError("candidate has an unknown evidence basis")
    if candidate.get("evidencePublicationIds") != expected_ids or candidate.get("evidenceUrls") != expected_urls:
        raise SourceDataError("candidate evidence does not match independent source review")

    evidence_set = set(expected_ids)
    return ({
        "id": candidate["id"],
        "nameEn": candidate["nameEn"],
        "evidencePublicationIds": expected_ids,
        "evidenceUrls": expected_urls,
    }, {
        "subdirectionId": candidate["id"],
        "evidenceBasis": (
            "title-and-subject-metadata" if basis == "publication-title-and-analysis-topic" else basis
        ),
        "evidencePublicationIds": expected_ids,
        "evidenceUrls": expected_urls,
        "sourceTitles": [item["title"] for item in expected_publications],
    }, evidence_set)


def _sentences_and_reason(
    direction: dict[str, Any],
    direction_review: dict[str, Any],
    professor_basis: str,
    analysis_status: str,
    analysis_keywords: list[str],
) -> tuple[list[str], str, str]:
    name = direction["nameEn"]
    basis = direction_review["evidenceBasis"]
    if basis == "title-and-subject-metadata":
        titles = _format_titles([{"title": title} for title in direction_review["sourceTitles"]])
        sentence_one = f"This sub-direction covers {name.lower()} as represented in the reviewed release record."
        sentence_two = f"Concrete support comes from {titles}; the reviewed title concepts provide bounded support for this analysis topic."
        reason = "The direction name is an analysis topic and every cited publication has independent title-level support."
    elif basis == "analysis-only":
        sentence_one = f"This evidence-bounded sub-direction concerns {name.lower()} in the approved analysis record."
        relevant_terms = _relevant_analysis_terms(name, analysis_keywords)
        indexed_terms = _format_terms(relevant_terms) if relevant_terms else "no additional indexed terms"
        sentence_two = f"The approved analysis explicitly lists {name}, with indexed terminology including {indexed_terms}."
        reason = "The topic is explicit in approved analysis metadata, but no canonical release-window publication confirms it."
    elif basis == "conflicting-record":
        titles = _format_titles(
            [{"title": title} for title in direction_review["sourceTitles"]],
            descriptor="sampled",
        )
        sentence_one = "The available analysis labels and retrieved publications do not form a coherent research sub-direction."
        sentence_two = f"The conflict review sampled {titles}, which do not consistently support the analysis topics."
        reason = "The candidate was retained only to document a material conflict between analysis topics and publication metadata."
    else:
        sentence_one = "The approved release data does not support a specific research sub-direction for this professor."
        sentence_two = "The official profile identifies the professor, but no approved thematic analysis or canonical publication is available for this release."
        reason = "Only official identity evidence is available."

    if professor_basis == "conflicting-record":
        limitation = "Because the analysis topics and owned-publication metadata conflict, no substantive publication assignment is asserted for this release."
    elif professor_basis == "incoherent-publication-record":
        limitation = "Because the retrieved record spans disconnected subject areas, author identity remains uncertain and no publication-to-direction assignment is made."
    elif basis == "analysis-only":
        limitation = "This direction is limited to approved analysis metadata because no canonical release-window publication is available for confirmation."
    elif basis == "insufficient-evidence":
        limitation = "This record is limited to documenting insufficient approved evidence and should not be read as a description of the professor's broader research agenda."
    elif analysis_status != "complete":
        limitation = "This interpretation is limited to incomplete release-window index metadata and does not establish the professor's complete or current research agenda."
    else:
        limitation = "This bounded interpretation describes only the cited release-window evidence and does not imply activity beyond it."
    disposition = "pass" if (
        professor_basis == "publication-record"
        and basis == "title-and-subject-metadata"
        and analysis_status == "complete"
    ) else "limited"
    return [sentence_one, sentence_two, limitation], disposition, reason


def review_subdirection_candidates(
    queue: dict[str, Any], candidates: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Recompute source grounding and produce final data plus substantive review evidence."""
    if queue.get("schemaVersion") != 1 or candidates.get("schemaVersion") != 1:
        raise SourceDataError("queue and candidate schema versions must be 1")
    if candidates.get("cutoff") != queue.get("cutoff"):
        raise SourceDataError("candidate cutoff does not match the evidence queue")
    queue_records, candidate_records = queue.get("professors"), candidates.get("professors")
    if not isinstance(queue_records, list) or not isinstance(candidate_records, list):
        raise SourceDataError("queue and candidate professors must be lists")
    candidate_by_id = {str(item.get("officialProfileId")): item for item in candidate_records}
    queue_ids = [str(item.get("officialProfileId")) for item in queue_records]
    if len(candidate_by_id) != len(candidate_records) or set(candidate_by_id) != set(queue_ids):
        raise SourceDataError("candidate professor coverage is incomplete or duplicated")

    final_records: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for queue_item in queue_records:
        profile_id = str(queue_item["officialProfileId"])
        candidate_record = candidate_by_id[profile_id]
        candidate_directions = candidate_record.get("subdirections")
        if not isinstance(candidate_directions, list) or not candidate_directions:
            raise SourceDataError(f"professor {profile_id} candidate requires at least one subdirection")
        if _has_semantic_duplicates(candidate_directions):
            raise SourceDataError(f"professor {profile_id} has a semantic duplicate subdirection")
        direction_ids = [item.get("id") for item in candidate_directions]
        if any(not isinstance(item, str) for item in direction_ids) or len(direction_ids) != len(set(direction_ids)):
            raise SourceDataError(f"professor {profile_id} candidate direction IDs are invalid or duplicated")

        reviewed: list[tuple[dict[str, Any], dict[str, Any], set[str]]] = [
            _review_direction(queue_item, candidate) for candidate in candidate_directions
        ]
        evidence_sets = [item[2] for item in reviewed if item[2]]
        has_explicit_conflict = any(
            item.get("evidenceBasis") == "conflicting-record" for item in candidate_directions
        )
        professor_basis = _professor_basis(
            queue_item["publications"],
            evidence_sets,
            has_explicit_conflict,
            any(item.get("evidenceBasis") == "analysis-only" for item in candidate_directions),
        )
        final_directions: list[dict[str, Any]] = []
        direction_reviews: list[dict[str, Any]] = []
        for direction, direction_review, _ in reviewed:
            sentences, disposition, reason = _sentences_and_reason(
                direction,
                direction_review,
                professor_basis,
                str(queue_item["researchAnalysis"].get("status")),
                list(queue_item["researchAnalysis"].get("keywords") or []),
            )
            direction.update({
                "explanationEn": sentences,
                "reviewStatus": disposition,
            })
            if disposition == "limited":
                direction["limitationEn"] = sentences[2]
            direction_review.update({
                "disposition": disposition,
                "reasonEn": reason,
            })
            final_directions.append(direction)
            direction_reviews.append(direction_review)

        force_unassigned = professor_basis in {"conflicting-record", "incoherent-publication-record"}
        direction_by_id = {item["id"]: item for item in final_directions}
        candidate_assignments = {
            item["publicationId"]: item.get("subdirectionIds") or []
            for item in candidate_record.get("publicationAssignments") or []
        }
        assignments: list[dict[str, Any]] = []
        assigned_count = 0
        for publication in queue_item["publications"]:
            assigned_ids = [] if force_unassigned else [
                direction_id for direction_id in candidate_assignments.get(publication["publicationId"], [])
                if direction_id in direction_by_id
                and publication["publicationId"] in direction_by_id[direction_id]["evidencePublicationIds"]
            ]
            assigned_count += bool(assigned_ids)
            assignments.append({
                "publicationId": publication["publicationId"],
                "subdirectionIds": assigned_ids,
                "unassignedReasonEn": None if assigned_ids else _UNASSIGNED_REASON,
            })
        professor_status = "limited" if any(
            direction["reviewStatus"] == "limited" for direction in final_directions
        ) else "pass"
        counts[professor_status] += 1
        final_records.append({
            "officialProfileId": profile_id,
            "reviewStatus": professor_status,
            "subdirections": final_directions,
            "publicationAssignments": assignments,
        })
        if professor_basis == "conflicting-record":
            professor_reason = "Analysis topics and retrieved publication titles do not provide a coherent identity-grounded record."
        elif professor_basis == "incoherent-publication-record":
            professor_reason = "The retrieved publications split across disconnected evidence clusters, so identity-level attribution is not reliable."
        elif professor_basis == "analysis-only":
            professor_reason = "Specific analysis topics are available, but canonical release-window publications are absent."
        elif professor_basis == "insufficient-evidence":
            professor_reason = "Only official identity evidence is available for this release."
        else:
            professor_reason = "Accepted directions have independent title-level support and same-professor evidence resolution."
        results.append({
            "officialProfileId": profile_id,
            "disposition": professor_status,
            "professorBasis": professor_basis,
            "professorReasonEn": professor_reason,
            "directionReviews": direction_reviews,
            "publicationAssignmentCount": len(assignments),
            "assignedPublicationCount": assigned_count,
            "unassignedPublicationCount": len(assignments) - assigned_count,
        })

    artifact = {"schemaVersion": 1, "cutoff": queue.get("cutoff"), "professors": final_records}
    report = {
        "schemaVersion": 1,
        "cutoff": queue.get("cutoff"),
        "summary": {
            "total": len(results),
            "pass": counts["pass"],
            "limited": counts["limited"],
            "block": 0,
        },
        "results": results,
    }
    return artifact, report


def _require_english_only(artifact: dict[str, Any]) -> None:
    for record in artifact.get("professors") or []:
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
    queue: dict[str, Any],
    candidates: dict[str, Any],
    professor_ids: set[str],
    publications: list[dict[str, Any]],
) -> None:
    """Fail closed unless tracked outputs equal an independently recomputed review."""
    if not isinstance(publications, list):
        raise SourceDataError("review publications must be a list")
    canonical_publications: dict[tuple[str, str], dict[str, Any]] = {}
    for publication in publications:
        owner = clean(publication.get("officialProfileId")) if isinstance(publication, dict) else None
        if not owner or owner not in professor_ids:
            raise SourceDataError("canonical publication references an unknown professor")
        publication_id = clean(publication.get("publicationId"))
        scoped_id = (owner, publication_id or "")
        if not publication_id or scoped_id in canonical_publications:
            raise SourceDataError("canonical publication IDs must be unique within each professor")
        canonical_publications[scoped_id] = publication
    queued_publications = {
        (str(record["officialProfileId"]), str(publication["publicationId"])): publication
        for record in queue.get("professors") or []
        for publication in record.get("publications") or []
    }
    if set(queued_publications) != set(canonical_publications):
        raise SourceDataError("review queue publication coverage differs from canonical publications")
    for scoped_id, queued in queued_publications.items():
        canonical = canonical_publications[scoped_id]
        expected = {
            "publicationId": clean(canonical.get("publicationId")),
            "title": clean(canonical.get("title")),
            "effectiveDate": str(canonical.get("effectiveDate") or ""),
            "publicationType": clean(canonical.get("publicationType")),
            "venue": clean(canonical.get("venue")),
            "keywords": list(canonical.get("keywords") or []),
            "evidenceUrls": sorted(set(canonical.get("evidenceUrls") or [])),
        }
        if queued != expected:
            raise SourceDataError("review queue canonical publication evidence differs")
    if not isinstance(report, dict) or report.get("schemaVersion") != 1:
        raise SourceDataError("subdirection review requires schema version 1")
    results = report.get("results")
    if not isinstance(results, list):
        raise SourceDataError("subdirection review results must be a list")
    seen: set[str] = set()
    for result in results:
        profile_id = clean(result.get("officialProfileId")) if isinstance(result, dict) else None
        if not profile_id or profile_id not in professor_ids:
            raise SourceDataError("subdirection review references an unknown professor")
        if profile_id in seen:
            raise SourceDataError(f"duplicate subdirection review result for professor {profile_id}")
        seen.add(profile_id)
        disposition = result.get("disposition")
        if disposition not in _REVIEW_STATUSES:
            raise SourceDataError(f"professor {profile_id} review has an invalid disposition")
        if disposition == "block":
            raise SourceDataError(f"professor {profile_id} review is blocked")
    missing = professor_ids - seen
    if missing:
        raise SourceDataError("subdirection review is missing professor IDs: " + ", ".join(sorted(missing)))

    _require_english_only(artifact)
    expected_artifact, expected_report = review_subdirection_candidates(queue, candidates)
    if artifact != expected_artifact or report != expected_report:
        raise SourceDataError("tracked artifact or report does not match independent review")

    publication_owners: dict[str, set[str]] = defaultdict(set)
    for publication in publications:
        publication_owners[str(publication["publicationId"])].add(str(publication["officialProfileId"]))
    validate_research_subdirections(artifact, professor_ids, publication_owners)


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SourceDataError(f"cannot read valid JSON from {path}") from error


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _canonical_inputs(root: Path, cutoff: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    professors = _load_json(root / "data/professors.json")
    analyses = _load_json(root / "data/research-analysis.json")
    publications = _load_json(root / "data/publications.json")
    if not isinstance(professors, list) or not isinstance(analyses, list) or not isinstance(publications, list):
        raise SourceDataError("canonical professor, analysis, and publication data must be lists")
    queue = build_subdirection_work_queue(professors, analyses, publications, cutoff)
    candidates = build_subdirection_candidates(queue)
    return professors, publications, queue, candidates


def _prepared_review_inputs(
    root: Path, cutoff: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    professors, publications, expected_queue, expected_candidates = _canonical_inputs(root, cutoff)
    queue = _load_json(root / f"reports/{cutoff}-subdirection-work-queue.json")
    candidates = _load_json(root / f"reports/{cutoff}-subdirection-candidates.json")
    if queue != expected_queue or candidates != expected_candidates:
        raise SourceDataError("prepared review inputs are stale or differ from canonical generation")
    return professors, publications, queue, candidates


def validate(root: Path, cutoff: str) -> dict[str, int]:
    professors, publications, queue, candidates = _canonical_inputs(root, cutoff)
    artifact = _load_json(root / "data/research-subdirections.json")
    report = _load_json(root / f"reports/{cutoff}-subdirection-self-review.json")
    professor_ids = {str(item.get("officialProfileId")) for item in professors}
    if len(professor_ids) != len(professors):
        raise SourceDataError("professor identities are not unique")
    validate_subdirection_review_report(
        report, artifact, queue, candidates, professor_ids, publications
    )
    return {"professors": len(professors), "publications": len(publications), "reviewed": len(report["results"])}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--cutoff", required=True)
    parser.add_argument("--write-reviewed", action="store_true")
    args = parser.parse_args()
    if args.write_reviewed:
        professors, publications, queue, candidates = _prepared_review_inputs(args.root, args.cutoff)
        artifact, report = review_subdirection_candidates(queue, candidates)
        professor_ids = {str(item["officialProfileId"]) for item in professors}
        validate_subdirection_review_report(
            report, artifact, queue, candidates, professor_ids, publications
        )
        _write_json(args.root / "data/research-subdirections.json", artifact)
        _write_json(args.root / f"reports/{args.cutoff}-subdirection-self-review.json", report)
        print(f"{len(report['results'])}/{len(professors)}")
        return
    result = validate(args.root, args.cutoff)
    print(f"{result['reviewed']}/{result['professors']}")


if __name__ == "__main__":
    main()
