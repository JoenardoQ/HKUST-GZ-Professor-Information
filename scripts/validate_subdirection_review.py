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
_DUPLICATE_MODIFIERS = frozenset({
    "advanced", "advancement", "analysis", "application", "material", "optimization",
    "research", "study", "technique", "technology",
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


def _has_ai_signal(value: str) -> bool:
    normalized = _ascii(value)
    return bool(
        "artificial intelligence" in normalized
        or "large language model" in normalized
        or re.search(r"\b(?:ai|llms?)\b", normalized)
    )


def _has_3d_signal(value: str) -> bool:
    return bool(re.search(r"\b3[ -]?d(?:imensional)?\b", _ascii(value)))


def _has_any(value: str, terms: set[str]) -> bool:
    tokens = _review_tokens(value)
    normalized = _ascii(value)
    return bool(tokens & terms) or any(term in normalized for term in terms if " " in term)


def _review_text_support(topic: str, value: str) -> bool:
    """Require topic-discriminative concepts, not a generic acronym or dimension token."""
    topic_tokens = _review_tokens(topic, topical=True)
    value_tokens = _review_tokens(value)
    overlap = topic_tokens & value_tokens
    topic_ascii = _ascii(topic)

    if "artificial intelligence" in topic_ascii or re.search(r"\bai\b", topic_ascii):
        if not _has_ai_signal(value):
            return False
        if "game" in topic_ascii:
            return _has_any(value, {"game", "gaming", "gameplay", "play", "player"})
        if "healthcare" in topic_ascii or "education" in topic_ascii:
            return _has_any(value, {
                "clinical", "diagnosis", "diagnostic", "education", "educational",
                "health", "healthcare", "medical", "medicine", "patient", "teaching",
            })
        if "cancer" in topic_ascii:
            return _has_any(value, {"cancer", "oncology", "tumor"})
        if "service" in topic_ascii:
            return _has_any(value, {"customer", "interaction", "service"})
        if "ethic" in topic_ascii or "social" in topic_ascii:
            return _has_any(value, {"bias", "ethic", "fairness", "responsible", "social"})
        remaining = topic_tokens - {"ai", "artificial", "intelligence"}
        return bool(remaining & value_tokens)

    if "3d" in topic_tokens:
        if not _has_3d_signal(value):
            return False
        if "biomedical" in topic_ascii:
            return _has_any(value, {
                "biofabrication", "biological", "biology", "biomedical", "cardiac",
                "cell", "medical", "medicine", "tissue",
            })
        if "shape" in topic_ascii or "model" in topic_ascii:
            return _has_any(value, {"generation", "layout", "model", "reconstruction", "scene", "shape"})
        if "survey" in topic_ascii or "heritage" in topic_ascii:
            return _has_any(value, {"cultural", "heritage", "reconstruction", "survey", "surveying"})
        if "print" in topic_ascii or "additive" in topic_ascii:
            return _has_any(value, {"additive", "fabrication", "manufacturing", "print", "printing"})
        return bool((topic_tokens - {"3d"}) & value_tokens)

    if "ad hoc" in topic_ascii:
        return bool(
            "ad hoc" in _ascii(value)
            or re.search(r"\bvanets?\b", _ascii(value))
            or _has_any(value, {"vehicular"})
        )

    topic_coverage = len(overlap) / len(topic_tokens) if topic_tokens else 0.0
    if topic_coverage >= 0.5 and (
        len(overlap) >= 2
        or any(len(token) >= 6 and token not in _WEAK_SINGLE_TOKENS for token in overlap)
    ):
        return True
    value_ascii = _ascii(value)
    compact_value = re.sub(r"[^a-z0-9]", "", value_ascii)
    multimodal_signal = (
        "multimodal" in compact_value
        or "visionlanguageaction" in compact_value
        or bool(re.search(r"\bvla\b", value_ascii))
    )
    if "multimodal" in topic_ascii and multimodal_signal:
        return True
    if "robot" in topic_ascii and "social" not in topic_ascii and (
        "visionlanguageaction" in compact_value or bool(re.search(r"\bvla\b", value_ascii))
    ):
        return _has_any(value, {"action", "manipulation", "policy", "pose", "robot"})
    if "battery" in topic_ascii:
        electrochemical = value_tokens & {
            "anode", "battery", "cathode", "electrolyte", "lithium", "sodium", "zinc", "zn"
        }
        if "battery" in electrochemical and "battery swapping" not in value_ascii:
            return True
        if len(electrochemical) >= 2:
            return True
    if "nanoplatform" in topic_ascii and {"cancer", "theranostic"} & topic_tokens:
        nano_signal = bool(value_tokens & {"biomaterial", "nano", "nanoplatform", "photosensitizer"})
        cancer_signal = bool(value_tokens & {"antitumor", "cancer", "tumor"})
        if nano_signal and cancer_signal:
            return True
    return False


def _exact_subject_has_context(topic: str, publication: dict[str, Any]) -> bool:
    title = str(publication.get("title") or "")
    other_subjects = " ".join(
        str(keyword) for keyword in publication.get("keywords") or []
        if _ascii(str(keyword)) != _ascii(topic)
    )
    context = f"{title} {other_subjects}"
    topic_ascii = _ascii(topic)
    if "artificial intelligence" in topic_ascii or re.search(r"\bai\b", topic_ascii):
        if not _has_ai_signal(context):
            return False
        if "game" in topic_ascii:
            return _has_any(context, {"game", "gaming", "gameplay", "play", "player"})
        if "healthcare" in topic_ascii or "education" in topic_ascii:
            return _has_any(context, {
                "clinical", "diagnosis", "diagnostic", "education", "educational",
                "health", "healthcare", "medical", "medicine", "patient", "teaching",
            })
        if "cancer" in topic_ascii:
            return _has_any(context, {"cancer", "oncology", "tumor"})
        return bool(
            (_review_tokens(topic, topical=True) - {"ai", "artificial", "intelligence"})
            & _review_tokens(context)
        )
    if "3d" in _review_tokens(topic, topical=True):
        if "survey" in topic_ascii or "heritage" in topic_ascii:
            return _has_3d_signal(context) and _has_any(
                context, {"cultural", "heritage", "reconstruction", "survey", "surveying"}
            )
        if "biomedical" in topic_ascii:
            printing_signal = _has_3d_signal(context) or _has_any(
                context, {"additive", "fabrication", "print", "printing"}
            )
            biomedical_signal = _has_any(
                context, {"biofabrication", "biological", "biomedical", "cardiac", "cell", "medical", "tissue"}
            )
            return printing_signal and biomedical_signal
        if "shape" in topic_ascii or "model" in topic_ascii:
            return _has_3d_signal(context) and _has_any(
                context, {"generation", "layout", "model", "reconstruction", "scene", "shape"}
            )
        return _has_3d_signal(context) and _has_any(
            context, {"additive", "fabrication", "manufacturing", "print", "printing"}
        )
    return _review_text_support(topic, context)


def _publication_judgment(topic: str, publication: dict[str, Any]) -> dict[str, Any] | None:
    title = str(publication["title"])
    title_support = _review_text_support(topic, title)
    matched_subjects: list[str] = []
    for keyword in publication.get("keywords") or []:
        keyword_text = str(keyword)
        is_exact_topic = _ascii(keyword_text) == _ascii(topic)
        if (
            is_exact_topic and _exact_subject_has_context(topic, publication)
        ) or (
            not is_exact_topic and _review_text_support(topic, keyword_text)
        ):
            matched_subjects.append(keyword_text)
    matched_subjects = sorted(set(matched_subjects), key=lambda value: (_ascii(value), value))
    if not title_support and not matched_subjects:
        return None
    reason = _judgment_reason(title_support, bool(matched_subjects))
    return {
        "publicationId": publication["publicationId"],
        "sourceTitle": title,
        "titleSupport": title_support,
        "indexedSubjectSupport": bool(matched_subjects),
        "matchedSubjectLabels": matched_subjects,
        "reasonEn": reason,
    }


def _judgment_reason(title_support: bool, subject_support: bool) -> str:
    if title_support and subject_support:
        basis = "title and indexed-subject concepts"
    elif title_support:
        basis = "topic-discriminative title concepts"
    else:
        basis = "topic-discriminative indexed-subject concepts"
    return f"Accepted on {basis}; generic abbreviations alone are insufficient."


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


def _direction_core(name: str) -> set[str]:
    return _review_tokens(name, topical=True) - _DUPLICATE_MODIFIERS


def _overlap_coefficient(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def _near_duplicate(
    left: tuple[dict[str, Any], dict[str, Any], set[str]],
    right: tuple[dict[str, Any], dict[str, Any], set[str]],
) -> bool:
    left_direction, _, left_ids = left
    right_direction, _, right_ids = right
    left_core = _direction_core(str(left_direction["nameEn"]))
    right_core = _direction_core(str(right_direction["nameEn"]))
    if left_core and left_core == right_core:
        return True
    if _name_similarity(str(left_direction["nameEn"]), str(right_direction["nameEn"])) >= 0.72:
        return True
    core_subset = bool(left_core and right_core) and (
        left_core <= right_core or right_core <= left_core
    )
    return core_subset and _overlap_coefficient(left_ids, right_ids) >= 0.75


def _merge_reviewed_direction(
    target: tuple[dict[str, Any], dict[str, Any], set[str]],
    incoming: tuple[dict[str, Any], dict[str, Any], set[str]],
) -> tuple[dict[str, Any], dict[str, Any], set[str]]:
    direction, review, evidence_ids = target
    incoming_direction, incoming_review, incoming_ids = incoming
    source_names = [
        *review["sourceNamesEn"],
        *[name for name in incoming_review["sourceNamesEn"] if name not in review["sourceNamesEn"]],
    ]
    judgments_by_id = {
        judgment["publicationId"]: dict(judgment)
        for judgment in review["evidenceJudgments"]
    }
    for judgment in incoming_review["evidenceJudgments"]:
        if judgment["publicationId"] not in judgments_by_id:
            judgments_by_id[judgment["publicationId"]] = dict(judgment)
            continue
        existing = judgments_by_id[judgment["publicationId"]]
        existing["titleSupport"] = existing["titleSupport"] or judgment["titleSupport"]
        existing["indexedSubjectSupport"] = (
            existing["indexedSubjectSupport"] or judgment["indexedSubjectSupport"]
        )
        existing["matchedSubjectLabels"] = sorted(set(
            existing["matchedSubjectLabels"] + judgment["matchedSubjectLabels"]
        ), key=lambda value: (_ascii(value), value))
        existing["reasonEn"] = _judgment_reason(
            existing["titleSupport"], existing["indexedSubjectSupport"]
        )
    merged_ids = evidence_ids | incoming_ids
    merged_judgments = sorted(
        judgments_by_id.values(), key=lambda item: str(item["publicationId"])
    )
    direction["evidencePublicationIds"] = sorted(merged_ids)
    direction["evidenceUrls"] = sorted(set(direction["evidenceUrls"]) | set(incoming_direction["evidenceUrls"]))
    review.update({
        "sourceNamesEn": source_names,
        "evidencePublicationIds": sorted(merged_ids),
        "evidenceUrls": list(direction["evidenceUrls"]),
        "sourceTitles": [item["sourceTitle"] for item in merged_judgments],
        "evidenceJudgments": merged_judgments,
    })
    return direction, review, merged_ids


def _professor_basis(
    publications: list[dict[str, Any]],
    direction_evidence: list[set[str]],
    has_analysis_only: bool,
) -> str:
    if not publications:
        return "analysis-only" if has_analysis_only else "insufficient-evidence"
    matched = set().union(*direction_evidence) if direction_evidence else set()
    match_ratio = len(matched) / len(publications)
    if match_ratio < 0.2:
        return "evidence-conflict"
    return "publication-record"


def _review_topic_candidate(
    queue_item: dict[str, Any],
    candidate: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], set[str]] | None:
    analysis = queue_item["researchAnalysis"]
    publications = queue_item["publications"]
    topics = {
        str(value).casefold()
        for value in [*analysis["researchInterests"], *analysis["researchAreas"]]
    }
    source_names = candidate.get("sourceNamesEn")
    name = candidate.get("nameEn")
    if (
        not isinstance(name, str)
        or not isinstance(source_names, list)
        or source_names != [name]
        or name.casefold() not in topics
    ):
        raise SourceDataError("candidate source names must be an English string list")
    judgments = [
        judgment for publication in publications
        if (judgment := _publication_judgment(name, publication)) is not None
    ]
    if not judgments:
        return None
    publications_by_id = {item["publicationId"]: item for item in publications}
    evidence_set = {str(item["publicationId"]) for item in judgments}
    evidence_publications = [publications_by_id[publication_id] for publication_id in sorted(evidence_set)]
    expected_urls = _direction_urls(evidence_publications)
    return ({
        "id": candidate["id"],
        "nameEn": name,
        "evidencePublicationIds": sorted(evidence_set),
        "evidenceUrls": expected_urls,
    }, {
        "subdirectionId": candidate["id"],
        "sourceNamesEn": list(source_names),
        "evidenceBasis": "title-or-indexed-subject support",
        "evidencePublicationIds": sorted(evidence_set),
        "evidenceUrls": expected_urls,
        "sourceTitles": [item["sourceTitle"] for item in judgments],
        "evidenceJudgments": judgments,
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
    if basis == "title-or-indexed-subject support":
        titles = _format_titles([{"title": title} for title in direction_review["sourceTitles"]])
        judgments = direction_review["evidenceJudgments"]
        title_count = sum(item["titleSupport"] for item in judgments)
        subject_count = sum(item["indexedSubjectSupport"] for item in judgments)
        publication_word = "publication" if len(judgments) == 1 else "publications"
        title_verb = "has" if title_count == 1 else "have"
        subject_verb = "has" if subject_count == 1 else "have"
        sentence_one = f"This sub-direction covers {name.lower()} as represented in the reviewed release record."
        sentence_two = (
            f"The review cites {titles}; {title_count} {title_verb} topic-discriminative title support "
            f"and {subject_count} {subject_verb} corroborating indexed-subject support."
        )
        reason = (
            f"The reviewer accepted {len(judgments)} cited {publication_word} using topic-discriminative "
            "title-or-indexed-subject evidence; generic abbreviations alone were insufficient."
        )
    elif basis == "analysis-only":
        sentence_one = f"This evidence-bounded sub-direction concerns {name.lower()} in the approved analysis record."
        relevant_terms = _relevant_analysis_terms(name, analysis_keywords)
        indexed_terms = _format_terms(relevant_terms) if relevant_terms else "no additional indexed terms"
        sentence_two = f"The approved analysis explicitly lists {name}, with indexed terminology including {indexed_terms}."
        reason = "The topic is explicit in approved analysis metadata, but no canonical release-window publication confirms it."
    elif basis == "evidence-conflict":
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

    if professor_basis == "evidence-conflict":
        limitation = "Because the analysis topics and most owned-publication metadata diverge in this release, no substantive publication assignment is asserted."
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
        and basis == "title-or-indexed-subject support"
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
        direction_ids = [item.get("id") for item in candidate_directions]
        if any(not isinstance(item, str) for item in direction_ids) or len(direction_ids) != len(set(direction_ids)):
            raise SourceDataError(f"professor {profile_id} candidate direction IDs are invalid or duplicated")
        canonical_topics: list[str] = []
        for raw_name in [
            *queue_item["researchAnalysis"]["researchInterests"],
            *queue_item["researchAnalysis"]["researchAreas"],
        ]:
            name = clean(raw_name)
            if name and name.casefold() not in {topic.casefold() for topic in canonical_topics}:
                canonical_topics.append(name)
        expected_names = canonical_topics or ["Evidence-limited research profile"]
        if [item.get("nameEn") for item in candidate_directions] != expected_names:
            raise SourceDataError(f"professor {profile_id} candidate must contain every normalized analysis topic")
        for candidate, expected_name in zip(candidate_directions, expected_names):
            expected_sources = [] if not canonical_topics else [expected_name]
            if set(candidate) != {"id", "nameEn", "sourceNamesEn"} or candidate.get("sourceNamesEn") != expected_sources:
                raise SourceDataError(f"professor {profile_id} candidate must remain status-free and unadjudicated")

        reviewed: list[tuple[dict[str, Any], dict[str, Any], set[str]]] = []
        if queue_item["publications"] and canonical_topics:
            supported = [
                result for candidate in candidate_directions
                if (result := _review_topic_candidate(queue_item, candidate)) is not None
            ]
            for result in supported:
                duplicate_index = next(
                    (index for index, existing in enumerate(reviewed) if _near_duplicate(existing, result)),
                    None,
                )
                if duplicate_index is None:
                    reviewed.append(result)
                else:
                    reviewed[duplicate_index] = _merge_reviewed_direction(
                        reviewed[duplicate_index], result
                    )
            reviewed = reviewed[:3]
            if not reviewed:
                sampled = queue_item["publications"][:3]
                sampled_ids = [item["publicationId"] for item in sampled]
                evidence_urls = sorted(
                    set(_direction_urls(sampled))
                    | set(queue_item["officialEvidenceUrls"])
                    | set(queue_item["researchAnalysis"]["evidenceUrls"])
                )
                reviewed = [({
                    "id": "conflicting-indexed-research-evidence",
                    "nameEn": "Conflicting indexed research evidence",
                    "evidencePublicationIds": sampled_ids,
                    "evidenceUrls": evidence_urls,
                }, {
                    "subdirectionId": "conflicting-indexed-research-evidence",
                    "sourceNamesEn": canonical_topics,
                    "evidenceBasis": "evidence-conflict",
                    "evidencePublicationIds": sampled_ids,
                    "evidenceUrls": evidence_urls,
                    "sourceTitles": [item["title"] for item in sampled],
                    "evidenceJudgments": [{
                        "publicationId": item["publicationId"],
                        "sourceTitle": item["title"],
                        "titleSupport": False,
                        "indexedSubjectSupport": False,
                        "matchedSubjectLabels": [],
                        "reasonEn": "Sampled to document divergence; it does not substantively support an analysis topic.",
                    } for item in sampled],
                }, set())]
        elif canonical_topics:
            for candidate in candidate_directions:
                evidence_urls = sorted(
                    set(queue_item["officialEvidenceUrls"])
                    | set(queue_item["researchAnalysis"]["evidenceUrls"])
                )
                result = ({
                    "id": candidate["id"],
                    "nameEn": candidate["nameEn"],
                    "evidencePublicationIds": [],
                    "evidenceUrls": evidence_urls,
                }, {
                    "subdirectionId": candidate["id"],
                    "sourceNamesEn": list(candidate["sourceNamesEn"]),
                    "evidenceBasis": "analysis-only",
                    "evidencePublicationIds": [],
                    "evidenceUrls": evidence_urls,
                    "sourceTitles": [],
                    "evidenceJudgments": [],
                }, set())
                duplicate_index = next(
                    (index for index, existing in enumerate(reviewed) if _near_duplicate(existing, result)),
                    None,
                )
                if duplicate_index is None:
                    reviewed.append(result)
                else:
                    reviewed[duplicate_index] = _merge_reviewed_direction(
                        reviewed[duplicate_index], result
                    )
            reviewed = reviewed[:3]
        else:
            candidate = candidate_directions[0]
            if candidate.get("sourceNamesEn") != []:
                raise SourceDataError(f"professor {profile_id} insufficient candidate is invalid")
            evidence_urls = list(queue_item["officialEvidenceUrls"])
            reviewed = [({
                "id": candidate["id"],
                "nameEn": candidate["nameEn"],
                "evidencePublicationIds": [],
                "evidenceUrls": evidence_urls,
            }, {
                "subdirectionId": candidate["id"],
                "sourceNamesEn": [],
                "evidenceBasis": "insufficient-evidence",
                "evidencePublicationIds": [],
                "evidenceUrls": evidence_urls,
                "sourceTitles": [],
                "evidenceJudgments": [],
            }, set())]

        evidence_sets = [item[2] for item in reviewed if item[2]]
        professor_basis = _professor_basis(
            queue_item["publications"],
            evidence_sets,
            any(item[1]["evidenceBasis"] == "analysis-only" for item in reviewed),
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
            judgments = direction_review.pop("evidenceJudgments")
            direction_review["titleSupportedPublicationIds"] = [
                item["publicationId"] for item in judgments if item["titleSupport"]
            ]
            direction_review["indexedSubjectSupportedPublicationIds"] = [
                item["publicationId"] for item in judgments if item["indexedSubjectSupport"]
            ]
            direction_review["matchedSubjectLabels"] = sorted({
                label for item in judgments for label in item["matchedSubjectLabels"]
            }, key=lambda value: (_ascii(value), value))
            direction_review["matchedPublicationCount"] = len(judgments)
            direction_review["sourceTitles"] = direction_review["sourceTitles"][:3]
            final_directions.append(direction)
            direction_reviews.append(direction_review)

        force_unassigned = professor_basis == "evidence-conflict"
        direction_by_id = {item["id"]: item for item in final_directions}
        assignments: list[dict[str, Any]] = []
        assigned_count = 0
        for publication in queue_item["publications"]:
            assigned_ids = [] if force_unassigned else [
                direction_id for direction_id, direction in direction_by_id.items()
                if publication["publicationId"] in direction["evidencePublicationIds"]
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
        if professor_basis == "evidence-conflict":
            professor_reason = "The analysis topics and most retrieved publication metadata diverge, so assignments are withheld for this release."
        elif professor_basis == "analysis-only":
            professor_reason = "Specific analysis topics are available, but canonical release-window publications are absent."
        elif professor_basis == "insufficient-evidence":
            professor_reason = "Only official identity evidence is available for this release."
        else:
            professor_reason = "Accepted directions have topic-discriminative title-or-indexed-subject support and same-professor evidence resolution."
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
