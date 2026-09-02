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


_GENERIC_TOPIC_WORDS = frozenset({
    "a", "an", "and", "application", "applications", "approach", "approaches",
    "advanced", "based", "for", "in", "of", "on", "research", "study", "studies",
    "the", "to",
})
_WEAK_SINGLE_TOKENS = frozenset({
    "analysis", "approach", "architecture", "computational", "design", "development",
    "effect", "impact", "management", "material", "method", "model", "network",
    "optimization", "process", "system", "technology",
})


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


def _stem(word: str) -> str:
    if len(word) > 5 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 5 and word.endswith("ing"):
        return word[:-3]
    if len(word) > 4 and word.endswith("ed"):
        return word[:-2]
    if len(word) > 4 and word.endswith("s"):
        return word[:-1]
    return word


def _tokens(value: str, *, drop_generic: bool = False) -> set[str]:
    result = {_stem(token) for token in re.findall(r"[a-z0-9]+", _ascii_text(value)) if len(token) >= 2}
    if drop_generic:
        result -= {_stem(token) for token in _GENERIC_TOPIC_WORDS}
    return result


def _similarity(left: str, right: str) -> float:
    left_tokens, right_tokens = _tokens(left, drop_generic=True), _tokens(right, drop_generic=True)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _set_similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _topic_matches_publication(topic: str, publication: dict[str, Any]) -> bool:
    """Require title-level support; keyword labels alone are not sufficient."""
    topic_tokens = _tokens(topic, drop_generic=True)
    title = str(publication.get("title") or "")
    title_tokens = _tokens(title)
    overlap = topic_tokens & title_tokens
    topic_coverage = len(overlap) / len(topic_tokens) if topic_tokens else 0.0
    if topic_coverage >= 0.5 and (
        len(overlap) >= 2
        or any(len(token) >= 6 and token not in _WEAK_SINGLE_TOKENS for token in overlap)
    ):
        return True
    topic_ascii, title_ascii = _ascii_text(topic), _ascii_text(title)
    compact_title = re.sub(r"[^a-z0-9]", "", title_ascii)
    if "multimodal" in topic_ascii and (
        "multimodal" in compact_title
        or "visionlanguageaction" in compact_title
        or re.search(r"\bvla\b", title_ascii)
    ):
        return True
    if "robot" in topic_ascii and "social" not in topic_ascii and (
        "visionlanguageaction" in compact_title or re.search(r"\bvla\b", title_ascii)
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


def _merge_direction(directions: list[dict[str, Any]], name: str, matches: list[dict[str, Any]]) -> None:
    match_ids = {item["publicationId"] for item in matches}
    for direction in directions:
        existing_ids = set(direction["evidencePublicationIds"])
        if _similarity(name, direction["nameEn"]) >= 0.55 or _set_similarity(match_ids, existing_ids) >= 0.9:
            if name not in direction["sourceNamesEn"]:
                direction["sourceNamesEn"].append(name)
            direction["evidencePublicationIds"] = sorted(existing_ids | match_ids)
            direction["evidenceUrls"] = sorted(set(direction["evidenceUrls"]) | {
                url for publication in matches for url in publication["evidenceUrls"]
            })
            return
    directions.append({
        "nameEn": name,
        "sourceNamesEn": [name],
        "evidencePublicationIds": sorted(item["publicationId"] for item in matches),
        "evidenceUrls": sorted({url for item in matches for url in item["evidenceUrls"]}),
        "evidenceBasis": "publication-title-and-analysis-topic",
    })


def build_subdirection_candidates(queue: dict[str, Any]) -> dict[str, Any]:
    """Generate status-free candidates; review and disposition happen separately."""
    candidate_records: list[dict[str, Any]] = []
    for item in queue.get("professors") or []:
        analysis, publications = item["researchAnalysis"], item["publications"]
        topics: list[str] = []
        for raw in [*analysis["researchInterests"], *analysis["researchAreas"]]:
            topic = clean(raw)
            if topic and topic.casefold() not in {value.casefold() for value in topics}:
                topics.append(topic)
        directions: list[dict[str, Any]] = []
        if publications:
            for topic in topics:
                matches = [publication for publication in publications if _topic_matches_publication(topic, publication)]
                if matches:
                    _merge_direction(directions, topic, matches)
                if len(directions) >= 3:
                    break
            if not directions:
                sampled = publications[:3]
                directions = [{
                    "nameEn": "Conflicting indexed research evidence",
                    "sourceNamesEn": topics[:3],
                    "evidencePublicationIds": [entry["publicationId"] for entry in sampled],
                    "evidenceUrls": sorted(
                        {url for publication in sampled for url in publication["evidenceUrls"]}
                        | set(item["officialEvidenceUrls"])
                        | set(analysis["evidenceUrls"])
                    ),
                    "evidenceBasis": "conflicting-record",
                }]
        elif topics:
            for topic in topics:
                if any(_similarity(topic, direction["nameEn"]) >= 0.55 for direction in directions):
                    continue
                directions.append({
                    "nameEn": topic,
                    "sourceNamesEn": [topic],
                    "evidencePublicationIds": [],
                    "evidenceUrls": sorted(set(item["officialEvidenceUrls"]) | set(analysis["evidenceUrls"])),
                    "evidenceBasis": "analysis-only",
                })
                if len(directions) == 3:
                    break
        else:
            directions = [{
                "nameEn": "Evidence-limited research profile",
                "sourceNamesEn": [],
                "evidencePublicationIds": [],
                "evidenceUrls": list(item["officialEvidenceUrls"]),
                "evidenceBasis": "insufficient-evidence",
            }]

        used_ids: set[str] = set()
        for direction in directions:
            direction["id"] = _direction_id(direction["nameEn"], used_ids)
        candidate_records.append({
            "officialProfileId": item["officialProfileId"],
            "subdirections": directions,
            "publicationAssignments": [{
                "publicationId": publication["publicationId"],
                "subdirectionIds": [
                    direction["id"] for direction in directions
                    if publication["publicationId"] in direction["evidencePublicationIds"]
                    and direction["evidenceBasis"] == "publication-title-and-analysis-topic"
                ],
            } for publication in publications],
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
