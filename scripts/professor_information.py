"""Pure normalization, deduplication, generation, and bilingual validation helpers."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from collections import Counter
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.parse import quote, urlencode, urlparse, urlsplit

FACULTY_BASE = "https://facultyprofiles.hkust-gz.edu.cn"
ARXIV_HOST = "arxiv.org"
OPENALEX_HOST = "openalex.org"
RESEARCH_EVIDENCE_HOSTS = (
    ARXIV_HOST,
    OPENALEX_HOST,
    "pubmed.ncbi.nlm.nih.gov",
    "biorxiv.org",
    "medrxiv.org",
    "chemrxiv.org",
    "semanticscholar.org",
    "doi.org",
)
OFFICIAL_SUBDIRECTION_EVIDENCE_HOSTS = (
    "facultyprofiles.hkust-gz.edu.cn",
    "repository.hkust.edu.hk",
)
RETRIEVAL_SOURCES = (
    "OpenAlex",
    "paperscraper:arxiv",
    "paperscraper:pubmed",
    "paperscraper:biorxiv",
    "paperscraper:medrxiv",
    "paperscraper:chemrxiv",
    "paperscraper:semantic-scholar",
    "arXivValidation",
)
APPROVED_PAPERSCRAPER_KEYS = frozenset(
    source.split(":", 1)[1] for source in RETRIEVAL_SOURCES if source.startswith("paperscraper:")
)


class SourceDataError(RuntimeError):
    """Raised when public source data is incomplete or internally inconsistent."""


def clean(value: Any) -> str | None:
    text = " ".join(str(value or "").split())
    return text or None


def in_window(start: str, end: str, candidate: str) -> bool:
    start_date, end_date = validate_release_window(start, end)
    candidate_date = date.fromisoformat(candidate)
    return start_date <= candidate_date <= end_date


def validate_release_window(start: str, end: str) -> tuple[date, date]:
    """Require the inclusive semiannual release window to span two calendar years."""
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)
    try:
        expected_end = start_date.replace(year=start_date.year + 2)
    except ValueError as error:
        raise ValueError("publication window must span exactly two calendar years") from error
    if end_date != expected_end:
        raise ValueError("publication window must span exactly two calendar years")
    return start_date, end_date


def validate_artifact_window(
    payload: dict[str, Any], start: str, end: str, label: str
) -> None:
    validate_release_window(start, end)
    expected = {"start": start, "end": end, "inclusive": True}
    if not isinstance(payload, dict) or payload.get("window") != expected:
        raise SourceDataError(f"{label} window does not match the requested release window")


STRONG_IDENTIFIER_FIELDS = ("doi", "arxivId", "openAlexId", "semanticScholarId", "pubmedId")


def _identifier_relationship(left: dict[str, Any], right: dict[str, Any]) -> tuple[bool, bool]:
    """Return (has_equal_identifier, has_conflicting_identifier)."""
    equal = False
    conflict = False
    for field in STRONG_IDENTIFIER_FIELDS:
        left_value, right_value = clean(left.get(field)), clean(right.get(field))
        if left_value and right_value:
            if left_value.casefold() == right_value.casefold():
                equal = True
            else:
                conflict = True
    return equal, conflict


def _title_year_match(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        clean(left.get("title")) is not None
        and clean(right.get("title")) is not None
        and _normalized_title(str(left["title"])) == _normalized_title(str(right["title"]))
        and abs(int(str(left["effectiveDate"])[:4]) - int(str(right["effectiveDate"])[:4])) <= 1
    )


def _slug(value: str, profile_id: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    words = re.findall(r"[a-z0-9]+", normalized)
    prefix = "-".join(words) or "professor"
    return f"{prefix}-{profile_id.lower()}"


def _scholar_id(row: dict[str, Any]) -> str | None:
    for identifier in row.get("rsidentifier") or []:
        if clean(identifier.get("label")) == "GoogleScholarID":
            return clean(identifier.get("id"))
    return None


def normalize_faculty_rows(
    rows: list[dict[str, Any]],
    *,
    baseline_ids: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    baseline_ids = set(baseline_ids or set())
    seen: set[str] = set()
    professors: list[dict[str, Any]] = []
    for row in rows:
        profile_id = clean(row.get("id"))
        name_en = clean(row.get("enName"))
        if not profile_id or not name_en:
            raise SourceDataError("faculty record is missing an official ID or English name")
        if profile_id in seen:
            raise SourceDataError(f"duplicate official profile ID: {profile_id}")
        seen.add(profile_id)
        affiliations = []
        for job in row.get("jobs") or []:
            if (clean(job.get("campus")) or "").lower() != "gz":
                continue
            if job.get("publicStatus") not in (None, 1, "1"):
                continue
            affiliations.append({
                "externalJobId": clean(job.get("id")),
                "title": clean(job.get("jobEnName")) or clean(job.get("jobName")),
                "unit": clean(job.get("departmentEnName")) or clean(job.get("departmentName")),
                "hub": clean(job.get("parentEnName")) or clean(job.get("parentName")),
                "sortOrder": int(clean(job.get("sort")) or 0),
            })
        affiliations.sort(key=lambda item: (item["sortOrder"], item["externalJobId"] or ""))
        professors.append({
            "officialProfileId": profile_id,
            "slug": _slug(name_en, profile_id),
            "nameZh": clean(row.get("name")),
            "nameEn": name_en,
            "email": clean(row.get("email")),
            "phone": clean(row.get("phone")),
            "website": clean(row.get("website")),
            "officialProfileUrl": f"{FACULTY_BASE}/faculty-personal-page?id={quote(profile_id, safe='')}",
            "permaLink": clean(row.get("permaLink")),
            "scholarId": _scholar_id(row),
            "affiliations": affiliations,
            "lastVerifiedOn": clean(row.get("lastVerifiedOn")) or "1970-01-01",
        })
    if not professors:
        raise SourceDataError("official faculty roster is empty")
    if baseline_ids and len(seen & baseline_ids) / len(baseline_ids) < 0.95:
        raise SourceDataError("candidate roster contains fewer than 95% of baseline profile IDs")
    removed = sorted(baseline_ids - seen, key=lambda value: (len(value), value))
    professors.sort(key=lambda item: (unicodedata.normalize("NFKC", item["nameEn"]).casefold(), item["officialProfileId"]))
    return professors, removed


def _normalized_title(title: str) -> str:
    return " ".join(re.findall(r"[\w]+", unicodedata.normalize("NFKC", title).casefold()))


def _publication_identity(record: dict[str, Any]) -> str:
    doi = clean(record.get("doi"))
    if doi:
        return f"doi:{doi.casefold()}"
    arxiv_id = clean(record.get("arxivId"))
    if arxiv_id:
        return f"arxiv:{arxiv_id.casefold()}"
    digest = hashlib.sha256(_normalized_title(str(record["title"])).encode()).hexdigest()
    return f"title-sha256:{digest}"


def _valid_evidence(url: str) -> bool:
    if not isinstance(url, str) or not url.startswith("https://"):
        return False
    host = (urlparse(url).hostname or "").casefold()
    return any(host == domain or host.endswith(f".{domain}") for domain in RESEARCH_EVIDENCE_HOSTS)


def _valid_official_subdirection_evidence(url: str) -> bool:
    if not isinstance(url, str) or not url.startswith("https://"):
        return False
    host = (urlparse(url).hostname or "").casefold()
    return host in OFFICIAL_SUBDIRECTION_EVIDENCE_HOSTS


def deduplicate_publications(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_professor: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in records:
        profile_id = clean(raw.get("officialProfileId"))
        title = clean(raw.get("title"))
        effective_date = clean(raw.get("effectiveDate"))
        evidence = [url for url in raw.get("evidenceUrls") or [] if isinstance(url, str) and _valid_evidence(url)]
        if not profile_id or not title or not effective_date or not evidence:
            raise SourceDataError("publication candidate lacks identity, title, effective date, or approved research evidence")
        date.fromisoformat(effective_date)
        record = dict(raw)
        record["officialProfileId"] = profile_id
        record["title"] = title
        record["effectiveDate"] = effective_date
        record["evidenceUrls"] = evidence
        by_professor[profile_id].append(record)

    merged: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    groups: list[tuple[str, list[dict[str, Any]]]] = []
    for profile_id, professor_records in sorted(by_professor.items()):
        parents = list(range(len(professor_records)))
        conflicted_indexes: set[int] = set()

        def find(index: int) -> int:
            while parents[index] != index:
                parents[index] = parents[parents[index]]
                index = parents[index]
            return index

        def union(left: int, right: int) -> None:
            left_root, right_root = find(left), find(right)
            if left_root != right_root:
                parents[right_root] = left_root

        def same_work(left: dict[str, Any], right: dict[str, Any]) -> bool:
            equal_identifier, conflicting_identifier = _identifier_relationship(left, right)
            if conflicting_identifier:
                return False
            return equal_identifier or _title_year_match(left, right)

        for left in range(len(professor_records)):
            for right in range(left + 1, len(professor_records)):
                equal_identifier, conflicting_identifier = _identifier_relationship(
                    professor_records[left], professor_records[right]
                )
                if conflicting_identifier and _title_year_match(professor_records[left], professor_records[right]):
                    conflicted_indexes.update((left, right))
                    conflicts.append({
                        "officialProfileId": profile_id,
                        "normalizedTitle": _normalized_title(professor_records[left]["title"]),
                        "reason": "conflicting-strong-identifiers",
                        "candidates": [professor_records[left], professor_records[right]],
                    })
                elif equal_identifier or same_work(professor_records[left], professor_records[right]):
                    union(left, right)
        components: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for index, record in enumerate(professor_records):
            if index in conflicted_indexes:
                continue
            components[find(index)].append(record)
        groups.extend((profile_id, versions) for versions in components.values())

    for profile_id, versions in groups:
        normalized_title = _normalized_title(versions[0]["title"])
        years = {int(item["effectiveDate"][:4]) for item in versions}
        if max(years) - min(years) > 1:
            conflicts.append({
                "officialProfileId": profile_id,
                "normalizedTitle": normalized_title,
                "reason": "conflicting-effective-year",
                "candidates": versions,
            })
            continue
        preprint_types = {"arxiv", "preprint", "biorxiv", "medrxiv", "chemrxiv"}
        formal = [item for item in versions if str(item.get("publicationType", "")).casefold() not in preprint_types]
        selected = dict(sorted(formal or versions, key=lambda item: (item["effectiveDate"], item["title"]), reverse=True)[0])
        selected["evidenceUrls"] = sorted({url for item in versions for url in item["evidenceUrls"]})
        selected["arxivId"] = next((clean(item.get("arxivId")) for item in versions if clean(item.get("arxivId"))), None)
        selected["doi"] = next((clean(item.get("doi")) for item in versions if clean(item.get("doi"))), None)
        selected["openAlexId"] = next((clean(item.get("openAlexId")) for item in versions if clean(item.get("openAlexId"))), None)
        selected["semanticScholarId"] = next((clean(item.get("semanticScholarId")) for item in versions if clean(item.get("semanticScholarId"))), None)
        selected["pubmedId"] = next((clean(item.get("pubmedId")) for item in versions if clean(item.get("pubmedId"))), None)
        selected["keywords"] = sorted({keyword for item in versions for keyword in item.get("keywords") or []})
        selected["publicationId"] = _publication_identity(selected)
        merged.append(selected)
    merged.sort(key=lambda item: (item["officialProfileId"], item["effectiveDate"], _normalized_title(item["title"])), reverse=True)
    return merged, conflicts


def publication_candidates_from_evidence(
    evidence_records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Create candidates while requiring arXiv name searches to match a disambiguated index."""
    candidates: list[dict[str, Any]] = []
    quarantined: list[dict[str, Any]] = []

    def matches(left: dict[str, Any], right: dict[str, Any]) -> bool:
        equal_identifier, conflicting_identifier = _identifier_relationship(left, right)
        if conflicting_identifier:
            return False
        return equal_identifier or _title_year_match(left, right)

    def candidate(profile_id: str, work: dict[str, Any]) -> dict[str, Any]:
        return {
            "officialProfileId": profile_id,
            "title": clean(work.get("title")),
            "effectiveDate": clean(work.get("effectiveDate")),
            "publicationType": clean(work.get("publicationType")) or "unknown",
            "venue": clean(work.get("venue")),
            "doi": clean(work.get("doi")),
            "arxivId": clean(work.get("arxivId")),
            "openAlexId": clean(work.get("openAlexId")),
            "semanticScholarId": clean(work.get("semanticScholarId")),
            "pubmedId": clean(work.get("pubmedId")),
            "keywords": sorted(set(work.get("keywords") or []) | set(work.get("topics") or [])),
            "evidenceUrls": [str(work["url"])] if _valid_evidence(str(work.get("url") or "")) else [],
        }

    for record in evidence_records:
        profile_id = str(record.get("officialProfileId") or "")
        trusted_works: list[dict[str, Any]] = []
        for source_key in ("openalex",):
            source = record.get(source_key) or {}
            if source.get("status") != "complete":
                continue
            for work in source.get("works") or []:
                normalized = candidate(profile_id, work)
                if normalized["title"] and normalized["effectiveDate"] and normalized["evidenceUrls"]:
                    candidates.append(normalized)
                    trusted_works.append(work)
        for source_key, source in sorted((record.get("paperscraper") or {}).items()):
            if source_key not in APPROVED_PAPERSCRAPER_KEYS:
                continue
            if source.get("status") != "complete":
                continue
            for work in source.get("works") or []:
                if any(matches(work, trusted) for trusted in trusted_works):
                    normalized = candidate(profile_id, work)
                    if normalized["title"] and normalized["effectiveDate"] and normalized["evidenceUrls"]:
                        candidates.append(normalized)
                else:
                    quarantined.append({
                        "officialProfileId": profile_id,
                        "reason": "unconfirmed-paperscraper-author",
                        "source": source_key,
                        "title": clean(work.get("title")),
                    })

        arxiv = record.get("arxivValidation") or record.get("arxiv") or {}
        for work in arxiv.get("works") or [] if arxiv.get("status") == "complete" else []:
            if any(matches(work, trusted) for trusted in trusted_works):
                normalized = candidate(profile_id, work)
                if normalized["title"] and normalized["effectiveDate"] and normalized["evidenceUrls"]:
                    candidates.append(normalized)
            else:
                quarantined.append({
                    "officialProfileId": profile_id,
                    "reason": "unmatched-arxiv-validation" if "arxivValidation" in record else "unconfirmed-arxiv-author",
                    "arxivId": clean(work.get("arxivId")),
                    "title": clean(work.get("title")),
                })
    return candidates, quarantined


def _hub_directory(professor: dict[str, Any]) -> str:
    hubs = [item["hub"] for item in professor["affiliations"] if item.get("hub")]
    value = hubs[0] if hubs else "other-units"
    return "-".join(re.findall(r"[a-z0-9]+", value.casefold())) or "other-units"


def _publication_markdown(publication: dict[str, Any]) -> list[str]:
    venue = clean(publication.get("venue")) or publication.get("publicationType") or "Unknown venue"
    lines = [
        f"### {publication['title']}",
        "",
        f"Publication identity: `{publication['publicationId']}`",
        "",
        f"- Effective date: {publication['effectiveDate']}",
        f"- Venue/type: {venue}",
    ]
    if publication.get("keywords"):
        lines.append(f"- Keywords: {', '.join(publication['keywords'])}")
    links = " · ".join(f"[Evidence {index + 1}]({url})" for index, url in enumerate(publication["evidenceUrls"]))
    lines.extend([f"- Verification: {links}", ""])
    return lines


def _profile_markdown(
    professor: dict[str, Any],
    analysis: dict[str, Any],
    subdirections: list[dict[str, Any]],
) -> str:
    lines = [
        f"# {professor['nameEn']}",
        "",
        f"Professor identity: `{professor['officialProfileId']}`",
        "",
        f"Chinese name: {professor['nameZh'] or 'Not published'}",
        "",
        "## Official profile and contact",
        "",
        f"- [HKUST(GZ) Faculty Profiles]({professor['officialProfileUrl']})",
        f"- Work email: {professor['email'] or 'Not published'}",
        f"- Office telephone: {professor['phone'] or 'Not published'}",
    ]
    if professor.get("website"):
        lines.append(f"- Website: [{professor['website']}]({professor['website']})")
    if professor.get("permaLink"):
        lines.append(f"- Additional official scholarly profile: [{professor['permaLink']}]({professor['permaLink']})")
    lines.extend(["", "## Current HKUST(GZ) affiliations", ""])
    lines.extend(
        f"- {item['title'] or 'Title not published'} — {item['unit'] or 'Unit not published'} / {item['hub'] or 'Parent unit not published'}"
        for item in professor["affiliations"]
    )
    if not professor["affiliations"]:
        lines.append("- No current public GZ affiliation was returned.")
    lines.extend(["", "## Research analysis", ""])
    lines.append(analysis["summaryEn"] or "No explanatory summary could be produced from the retrieved scholarly-index evidence.")
    lines.extend(["", "### Research interests", ""])
    lines.extend(f"- {value}" for value in analysis["researchInterests"])
    if not analysis["researchInterests"]:
        lines.append("- No research interest could be inferred from the retrieved evidence.")
    lines.extend(["", "### Research areas", ""])
    lines.extend(f"- {value}" for value in analysis["researchAreas"])
    if not analysis["researchAreas"]:
        lines.append("- No research area could be inferred from the retrieved evidence.")
    lines.extend(["", "### Keywords", ""])
    lines.append(", ".join(analysis["keywords"]) or "No keywords could be inferred from the retrieved evidence.")
    evidence = " · ".join(
        f"[Research evidence {index + 1}]({url})"
        for index, url in enumerate(analysis["evidenceUrls"])
    )
    if evidence:
        lines.extend(["", f"Analysis evidence: {evidence}"])
    lines.extend(["", "## Generated research sub-directions", ""])
    for direction in subdirections:
        lines.extend([f"### {direction['nameEn']}", ""])
        for sentence in direction["explanationEn"]:
            lines.extend([sentence, ""])
    lines.extend(["## Verification", "", f"Official basics last verified: {professor['lastVerifiedOn']}", f"Research-source analysis last verified: {analysis['lastVerifiedOn']}", ""])
    return "\n".join(lines)


def _publications_markdown(
    professor: dict[str, Any],
    analysis: dict[str, Any],
    publications: list[dict[str, Any]],
    assignments: dict[str, dict[str, Any]],
    direction_names: dict[str, str],
    start: str,
    end: str,
) -> str:
    lines = [
        f"# {professor['nameEn']} — Publications",
        "",
        f"Professor identity: `{professor['officialProfileId']}`",
        "",
        "## Publications retrieved in this update",
        "",
        f"Inclusive window: {start} through {end}. OpenAlex, paperscraper sources, and independent arXiv verification are not exhaustive; this is not a complete publication record.",
        "",
        f"Retrieval status: **{analysis['status']}**",
        "",
    ]
    if analysis["notes"]:
        lines.extend(f"- {note}" for note in analysis["notes"])
        lines.append("")
    if publications:
        for publication in publications:
            lines.extend(_publication_markdown(publication))
            assignment = assignments[publication["publicationId"]]
            names = [direction_names[item] for item in assignment["subdirectionIds"]]
            note = (
                f"Generated research sub-direction: {', '.join(names)}"
                if names else "No reliable generated sub-direction assignment."
            )
            lines.extend([f"*{note}*", ""])
    else:
        lines.extend(["No publication was retrieved in this update window. This does not establish that no publication exists.", ""])
    lines.extend(["## Verification", "", f"Official basics last verified: {professor['lastVerifiedOn']}", f"Research-source analysis last verified: {analysis['lastVerifiedOn']}", ""])
    return "\n".join(lines)


def _chinese_publications_markdown(
    professor: dict[str, Any],
    analysis: dict[str, Any],
    publications: list[dict[str, Any]],
    assignments: dict[str, dict[str, Any]],
    directions_by_id: dict[str, dict[str, Any]],
    start: str,
    end: str,
    zh: Callable[[Any], str],
) -> str:
    display_name = f"{professor['nameZh']} · {professor['nameEn']}" if professor.get("nameZh") else professor["nameEn"]
    lines = [
        f"# {display_name} — 论文",
        "",
        f"Professor identity: `{professor['officialProfileId']}`",
        "",
        "## 本轮检索到的论文",
        "",
        f"闭区间：{start} 至 {end}。OpenAlex、paperscraper 多来源结果与独立 arXiv 核验均不保证穷尽；这不是完整发表记录。",
        "",
        f"检索状态：**{analysis['status']}**",
        "",
    ]
    if analysis["notes"]:
        lines.extend(f"- {zh(note)}" for note in analysis["notes"])
        lines.append("")
    if publications:
        for publication in publications:
            title = publication["title"]
            venue = clean(publication.get("venue")) or publication.get("publicationType") or "未知 venue"
            lines.extend([
                f"### {title}｜{zh(title)}",
                "",
                f"Publication identity: `{publication['publicationId']}`",
                "",
                f"- 有效日期：{publication['effectiveDate']}",
                f"- 发表场所/类型：{venue}",
            ])
            if publication.get("keywords"):
                lines.append(f"- 关键词：{', '.join(publication['keywords'])}")
            links = " · ".join(f"[证据 {index + 1}]({url})" for index, url in enumerate(publication["evidenceUrls"]))
            lines.extend([f"- 核验：{links}", ""])
            assignment = assignments[publication["publicationId"]]
            names = [zh(directions_by_id[item]["nameEn"]) for item in assignment["subdirectionIds"]]
            note = (
                f"生成的研究细分方向：{', '.join(names)}"
                if names else "未作出可靠的生成研究细分方向归属。"
            )
            lines.extend([f"*{note}*", ""])
    else:
        lines.extend(["本轮时间窗内未检索到论文；这不能证明该教授没有论文。", ""])
    lines.extend([
        "## 核验信息",
        "",
        f"官方基础资料最近核验日期：{professor['lastVerifiedOn']}",
        f"研究来源分析最近核验日期：{analysis['lastVerifiedOn']}",
        "",
    ])
    return "\n".join(lines)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _translation_source_digest(
    professors: list[dict[str, Any]],
    research_analysis: list[dict[str, Any]],
    publications: list[dict[str, Any]],
    research_subdirections: dict[str, Any],
    start: str,
    end: str,
) -> str:
    payload = {
        "window": {"start": start, "end": end, "inclusive": True},
        "professors": sorted(professors, key=lambda item: str(item.get("officialProfileId") or "")),
        "researchAnalysis": sorted(research_analysis, key=lambda item: str(item.get("officialProfileId") or "")),
        "publications": sorted(
            [item for item in publications if in_window(start, end, str(item["effectiveDate"]))],
            key=lambda item: (
                str(item.get("officialProfileId") or ""),
                str(item.get("publicationId") or _publication_identity(item)),
            ),
        ),
        "researchSubdirections": {
            **research_subdirections,
            "professors": sorted(
                research_subdirections.get("professors") or [],
                key=lambda item: str(item.get("officialProfileId") or ""),
            ),
        },
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _sha256_bytes(encoded)


def validate_frozen_english(
    root: Path,
    manifest: dict[str, Any],
    professors: list[dict[str, Any]],
    research_analysis: list[dict[str, Any]],
    publications: list[dict[str, Any]],
    research_subdirections: dict[str, Any],
    start: str,
    end: str,
) -> None:
    validate_release_window(start, end)
    if manifest.get("window") != {"start": start, "end": end, "inclusive": True}:
        raise SourceDataError("frozen English window changed")
    binding = manifest.get("translationSource") or {}
    expected_source = _translation_source_digest(
        professors, research_analysis, publications, research_subdirections, start, end
    )
    if binding.get("sourceDataSha256") != expected_source:
        raise SourceDataError("frozen English inputs changed")
    hashes = binding.get("englishFileSha256")
    if not isinstance(hashes, dict) or not hashes:
        raise SourceDataError("frozen English file hashes are missing")
    documents = manifest.get("documents")
    if not isinstance(documents, dict):
        raise SourceDataError("frozen English file hashes are missing")
    expected_paths = {"All_Prof_Info.md"}
    for paths in documents.values():
        if not isinstance(paths, dict):
            raise SourceDataError("frozen English file hashes are missing")
        for document_kind in ("profile", "publications"):
            group = paths.get(document_kind)
            if not isinstance(group, dict) or not isinstance(group.get("en"), str):
                raise SourceDataError("frozen English file hashes are missing")
            expected_paths.add(group["en"])
    if set(hashes) != expected_paths:
        raise SourceDataError("frozen English file hashes do not cover every English document")
    for relative_path, expected_hash in hashes.items():
        path = root / str(relative_path)
        if not path.is_file() or _sha256_bytes(path.read_bytes()) != expected_hash:
            raise SourceDataError("frozen English file changed")


_MANIFEST_DOCUMENT_SUFFIXES = {
    "profile": {"en": ".md", "zhCN": ".zh-CN.md"},
    "publications": {"en": ".publications.md", "zhCN": ".publications.zh-CN.md"},
}
_SAFE_DOCUMENT_DIRECTORY = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


def validate_manifest_document_paths(manifest: dict[str, Any]) -> None:
    documents = manifest.get("documents")
    if not isinstance(documents, dict):
        raise SourceDataError("manifest documents must be an object")
    for slug, paths in documents.items():
        if not isinstance(slug, str) or not isinstance(paths, dict) or set(paths) != set(_MANIFEST_DOCUMENT_SUFFIXES):
            raise SourceDataError("manifest document paths are malformed")
        for kind, suffixes in _MANIFEST_DOCUMENT_SUFFIXES.items():
            group = paths.get(kind)
            if not isinstance(group, dict) or set(group) != set(suffixes):
                raise SourceDataError(f"manifest record {slug} has an invalid {kind} path group")
            for language, suffix in suffixes.items():
                value = group[language]
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
                    or not _SAFE_DOCUMENT_DIRECTORY.fullmatch(path.parts[1])
                    or path.name != f"{slug}{suffix}"
                ):
                    raise SourceDataError(f"manifest record {slug} has an unsafe {kind}.{language} path")


def generate_documents(
    root: Path,
    professors: list[dict[str, Any]],
    research_analysis: list[dict[str, Any]],
    publications: list[dict[str, Any]],
    start: str,
    end: str,
    generated_at: str,
    *,
    research_subdirections: dict[str, Any],
) -> dict[str, Any]:
    validate_release_window(start, end)
    professor_ids = {item["officialProfileId"] for item in professors}
    validate_research_analysis(research_analysis, professor_ids)
    validate_retrieval_statuses(research_analysis)
    publication_owners: dict[str, set[str]] = defaultdict(set)
    for publication in publications:
        publication_owners[str(publication["publicationId"])].add(str(publication["officialProfileId"]))
    validate_research_subdirections(research_subdirections, professor_ids, publication_owners)
    if research_subdirections.get("cutoff") != end:
        raise SourceDataError("research subdirections cutoff does not match document cutoff")
    analysis_by_professor = {item["officialProfileId"]: item for item in research_analysis}
    subdirections_by_professor = {
        item["officialProfileId"]: item["subdirections"]
        for item in research_subdirections["professors"]
    }
    subdirection_records = {
        item["officialProfileId"]: item
        for item in research_subdirections["professors"]
    }
    selected = [item for item in publications if in_window(start, end, item["effectiveDate"])]
    by_professor: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in selected:
        by_professor[item["officialProfileId"]].append(item)
    unknown = sorted(set(by_professor) - professor_ids)
    if unknown:
        raise SourceDataError(f"publication records reference unknown professor IDs: {', '.join(unknown)}")
    ordered = sorted(professors, key=lambda item: (unicodedata.normalize("NFKC", item["nameEn"]).casefold(), item["officialProfileId"]))
    documents: dict[str, dict[str, str]] = {}
    search_records = []
    directory_records = []
    overview = [
        "# All HKUST(GZ) Professor Information",
        "",
        f"Cutoff: {end}  ",
        f"Inclusive publication window: {start} through {end}  ",
        f"Professors: {len(ordered)}  ",
        f"Publications retrieved in this update: {len(selected)}",
        "",
        "OpenAlex, paperscraper sources, and independent arXiv verification are not exhaustive. This report is not a complete publication record.",
        "",
        "## Professor directory",
        "",
    ]
    for professor in ordered:
        slug = professor["slug"]
        directory = _hub_directory(professor)
        en_path = f"professors/{directory}/{slug}.md"
        zh_path = f"professors/{directory}/{slug}.zh-CN.md"
        publications_en_path = f"professors/{directory}/{slug}.publications.md"
        publications_zh_path = f"professors/{directory}/{slug}.publications.zh-CN.md"
        documents[slug] = {
            "profile": {"en": en_path, "zhCN": zh_path},
            "publications": {"en": publications_en_path, "zhCN": publications_zh_path},
        }
        professor_publications = sorted(
            by_professor.get(professor["officialProfileId"], []),
            key=lambda item: (item["effectiveDate"], _normalized_title(item["title"])),
            reverse=True,
        )
        destination = root / en_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        analysis = analysis_by_professor[professor["officialProfileId"]]
        subdirection_record = subdirection_records[professor["officialProfileId"]]
        directions = subdirection_record["subdirections"]
        destination.write_text(
            _profile_markdown(professor, analysis, directions), encoding="utf-8"
        )
        assignments = {
            item["publicationId"]: item
            for item in subdirection_record["publicationAssignments"]
        }
        direction_names = {item["id"]: item["nameEn"] for item in directions}
        publications_destination = root / publications_en_path
        publications_destination.write_text(
            _publications_markdown(
                professor, analysis, professor_publications, assignments,
                direction_names, start, end,
            ),
            encoding="utf-8",
        )
        overview.extend([
            f"### [{professor['nameEn']}]({en_path})",
            "",
            f"{professor['nameZh'] or 'Chinese name not published'} · {' / '.join(item['hub'] or '' for item in professor['affiliations']) or 'Unit not published'} · {len(professor_publications)} publications retrieved",
            "",
        ])
        titles = sorted({item["title"] for item in professor_publications})
        search_records.append({
            "officialProfileId": professor["officialProfileId"],
            "slug": slug,
            "nameZh": professor["nameZh"] or "",
            "nameEn": professor["nameEn"],
            "email": professor["email"],
            "phone": professor["phone"],
            "titles": sorted({item["title"] for item in professor["affiliations"] if item.get("title")}),
            "hubs": sorted({item["hub"] for item in professor["affiliations"] if item.get("hub")}),
            "units": sorted({item["unit"] for item in professor["affiliations"] if item.get("unit")}),
            "researchDirections": sorted(set(analysis["researchInterests"] + analysis["researchAreas"])),
            "publicationYears": sorted({int(item["effectiveDate"][:4]) for item in professor_publications}),
            "publicationTitles": titles,
            "keywords": sorted(set(analysis["keywords"]) | {keyword for item in professor_publications for keyword in item.get("keywords") or []}),
            "venues": sorted({item.get("venue") or item.get("publicationType") for item in professor_publications if item.get("venue") or item.get("publicationType")}),
            "publicationCount": len(professor_publications),
            "lastVerifiedOn": professor["lastVerifiedOn"],
        })
        directory_records.append({
            "officialProfileId": professor["officialProfileId"],
            "slug": slug,
            "nameZh": professor["nameZh"] or "",
            "nameEn": professor["nameEn"],
            "email": professor["email"],
            "phone": professor["phone"],
            "titles": sorted({item["title"] for item in professor["affiliations"] if item.get("title")}),
            "hubs": sorted({item["hub"] for item in professor["affiliations"] if item.get("hub")}),
            "units": sorted({item["unit"] for item in professor["affiliations"] if item.get("unit")}),
            "researchFields": sorted(set(analysis["researchInterests"] + analysis["researchAreas"])),
            "subdirectionNames": sorted(
                {direction["nameEn"] for direction in subdirections_by_professor[professor["officialProfileId"]]},
                key=str.casefold,
            ),
            "publicationCount": len(professor_publications),
            "lastVerifiedOn": professor["lastVerifiedOn"],
        })
    manifest = {
        "schemaVersion": 2,
        "cutoff": end,
        "window": {"start": start, "end": end, "inclusive": True},
        "generatedAt": generated_at,
        "counts": {"professors": len(ordered), "publications": len(selected), "documents": len(ordered)},
        "documents": documents,
    }
    search_index = {
        "schemaVersion": 1,
        "cutoff": end,
        "window": {"start": start, "end": end, "inclusive": True},
        "records": search_records,
    }
    directory_index = {
        "schemaVersion": 1,
        "cutoff": end,
        "window": {"start": start, "end": end, "inclusive": True},
        "records": directory_records,
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / "All_Prof_Info.md").write_text("\n".join(overview), encoding="utf-8")
    english_paths = [
        "All_Prof_Info.md",
        *(paths[kind]["en"] for paths in documents.values() for kind in ("profile", "publications")),
    ]
    manifest["translationSource"] = {
        "sourceDataSha256": _translation_source_digest(
            professors, research_analysis, publications, research_subdirections, start, end
        ),
        "englishFileSha256": {
            relative_path: _sha256_bytes((root / relative_path).read_bytes())
            for relative_path in sorted(english_paths)
        },
    }
    _write_json(root / "data/manifest.json", manifest)
    _write_json(root / "data/search-index.json", search_index)
    _write_json(root / "data/directory-index.json", directory_index)
    return {"manifest": manifest, "searchIndex": search_index, "directoryIndex": directory_index}


def build_translation_memory_translator(memory: dict[str, Any]) -> Callable[[str], str]:
    def translate(source: str) -> str:
        translated = clean(memory.get(source))
        if not translated:
            raise SourceDataError(f"missing reviewed Chinese translation for: {source}")
        return translated

    return translate


def collect_translation_inputs(
    professors: list[dict[str, Any]],
    research_analysis: list[dict[str, Any]],
    publications: list[dict[str, Any]],
    start: str,
    end: str,
    *,
    research_subdirections: dict[str, Any] | None = None,
) -> list[str]:
    values: set[str] = set()
    for professor in professors:
        for affiliation in professor.get("affiliations") or []:
            values.update(value for field in ("title", "unit", "hub") if (value := clean(affiliation.get(field))))
    for analysis in research_analysis:
        if summary := clean(analysis.get("summaryEn")):
            values.add(summary)
        for field in ("researchInterests", "researchAreas", "keywords", "notes"):
            values.update(value for item in analysis.get(field) or [] if (value := clean(item)))
    for publication in publications:
        if not in_window(start, end, publication["effectiveDate"]):
            continue
        if title := clean(publication.get("title")):
            values.add(title)
    for record in (research_subdirections or {}).get("professors") or []:
        for direction in record.get("subdirections") or []:
            if name := clean(direction.get("nameEn")):
                values.add(name)
            values.update(
                sentence for item in direction.get("explanationEn") or []
                if (sentence := clean(item))
            )
        for assignment in record.get("publicationAssignments") or []:
            if reason := clean(assignment.get("unassignedReasonEn")):
                values.add(reason)
    return sorted(values, key=str.casefold)


def generate_chinese_documents(
    root: Path,
    professors: list[dict[str, Any]],
    research_analysis: list[dict[str, Any]],
    publications: list[dict[str, Any]],
    start: str,
    end: str,
    translator: Callable[[str], str],
    *,
    research_subdirections: dict[str, Any],
) -> None:
    """Translate frozen English output while preserving bibliographic structure."""
    manifest_path = root / "data/manifest.json"
    overview_en = root / "All_Prof_Info.md"
    if not manifest_path.is_file() or not overview_en.is_file():
        raise SourceDataError("English documents must be generated first")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    validate_manifest_document_paths(manifest)
    validate_frozen_english(
        root, manifest, professors, research_analysis, publications,
        research_subdirections, start, end
    )
    documents = manifest.get("documents") or {}
    professor_ids = {item["officialProfileId"] for item in professors}
    validate_research_analysis(research_analysis, professor_ids)
    validate_retrieval_statuses(research_analysis)
    analysis_by_professor = {item["officialProfileId"]: item for item in research_analysis}
    publication_owners: dict[str, set[str]] = defaultdict(set)
    for publication in publications:
        publication_owners[str(publication["publicationId"])].add(
            str(publication["officialProfileId"])
        )
    validate_research_subdirections(research_subdirections, professor_ids, publication_owners)
    subdirection_records = {
        item["officialProfileId"]: item
        for item in research_subdirections["professors"]
    }
    selected = [item for item in publications if in_window(start, end, item["effectiveDate"])]
    by_professor: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for publication in selected:
        by_professor[publication["officialProfileId"]].append(publication)
    ordered = sorted(professors, key=lambda item: (unicodedata.normalize("NFKC", item["nameEn"]).casefold(), item["officialProfileId"]))
    cache: dict[str, str] = {}

    def zh(value: Any) -> str:
        source = clean(value) or ""
        if not source:
            return ""
        if source not in cache:
            translated = clean(translator(source))
            if not translated:
                raise SourceDataError("translator returned an empty value")
            cache[source] = translated
        return cache[source]

    for source in collect_translation_inputs(
        professors, research_analysis, publications, start, end,
        research_subdirections=research_subdirections,
    ):
        zh(source)

    overview = [
        "# 香港科技大学（广州）教授信息总览",
        "",
        f"截止日期：{end}  ",
        f"论文闭区间：{start} 至 {end}  ",
        f"教授人数：{len(ordered)}  ",
        f"本轮检索到的论文：{len(selected)}",
        "",
        "OpenAlex、paperscraper 多来源结果与独立 arXiv 核验均不保证穷尽；本报告不是完整发表记录。",
        "",
        "## 教授目录",
        "",
    ]
    for professor in ordered:
        slug = professor["slug"]
        paths = documents.get(slug) or {}
        profile_paths = paths.get("profile") or {}
        publication_paths = paths.get("publications") or {}
        en_path = root / str(profile_paths.get("en") or "")
        zh_path = root / str(profile_paths.get("zhCN") or "")
        publications_en_path = root / str(publication_paths.get("en") or "")
        publications_zh_path = root / str(publication_paths.get("zhCN") or "")
        if (
            not en_path.is_file()
            or not publications_en_path.is_file()
            or not profile_paths.get("zhCN")
            or not publication_paths.get("zhCN")
        ):
            raise SourceDataError("English documents must be generated first")
        analysis = analysis_by_professor[professor["officialProfileId"]]
        subdirection_record = subdirection_records[professor["officialProfileId"]]
        directions = subdirection_record["subdirections"]
        directions_by_id = {item["id"]: item for item in directions}
        assignments = {
            item["publicationId"]: item
            for item in subdirection_record["publicationAssignments"]
        }
        professor_publications = sorted(
            by_professor.get(professor["officialProfileId"], []),
            key=lambda item: (item["effectiveDate"], _normalized_title(item["title"])),
            reverse=True,
        )
        display_name = f"{professor['nameZh']} · {professor['nameEn']}" if professor.get("nameZh") else professor["nameEn"]
        lines = [
            f"# {display_name}",
            "",
            f"Professor identity: `{professor['officialProfileId']}`",
            "",
            f"中文姓名：{professor['nameZh'] or '未公开'}",
            "",
            "## 官方资料与联系方式",
            "",
            f"- [香港科技大学（广州）Faculty Profiles]({professor['officialProfileUrl']})",
            f"- 工作邮箱：{professor['email'] or '未公开'}",
            f"- 办公电话：{professor['phone'] or '未公开'}",
        ]
        if professor.get("website"):
            lines.append(f"- 个人网站：[{professor['website']}]({professor['website']})")
        if professor.get("permaLink"):
            lines.append(f"- 其他官方学术主页：[{professor['permaLink']}]({professor['permaLink']})")
        lines.extend(["", "## 当前香港科技大学（广州）任职关系", ""])
        if professor["affiliations"]:
            for affiliation in professor["affiliations"]:
                title = zh(affiliation.get("title")) if affiliation.get("title") else "职称未公开"
                unit = zh(affiliation.get("unit")) if affiliation.get("unit") else "单位未公开"
                hub = zh(affiliation.get("hub")) if affiliation.get("hub") else "上级单位未公开"
                lines.append(f"- {title} — {unit} / {hub}")
        else:
            lines.append("- 官方来源未返回当前广州校区任职关系。")
        lines.extend(["", "## 研究分析", "", zh(analysis["summaryEn"])])
        lines.extend(["", "### 研究兴趣", ""])
        lines.extend(f"- {zh(value)}" for value in analysis["researchInterests"])
        if not analysis["researchInterests"]:
            lines.append("- 无法从本轮证据中推断稳定的研究兴趣。")
        lines.extend(["", "### 研究领域", ""])
        lines.extend(f"- {zh(value)}" for value in analysis["researchAreas"])
        if not analysis["researchAreas"]:
            lines.append("- 无法从本轮证据中推断稳定的研究领域。")
        lines.extend(["", "### 关键词", ""])
        lines.append("、".join(zh(value) for value in analysis["keywords"]) or "无法从本轮证据中推断关键词。")
        evidence = " · ".join(f"[研究证据 {index + 1}]({url})" for index, url in enumerate(analysis["evidenceUrls"]))
        if evidence:
            lines.extend(["", f"分析证据：{evidence}"])
        lines.extend(["", "## 生成的研究细分方向", ""])
        for direction in directions:
            lines.extend([f"### {zh(direction['nameEn'])}", ""])
            for sentence in direction["explanationEn"]:
                lines.extend([zh(sentence), ""])
        lines.extend([
            "## 核验信息",
            "",
            f"官方基础资料最近核验日期：{professor['lastVerifiedOn']}",
            f"研究来源分析最近核验日期：{analysis['lastVerifiedOn']}",
            "",
        ])
        zh_path.parent.mkdir(parents=True, exist_ok=True)
        zh_path.write_text("\n".join(lines), encoding="utf-8")

        publications_zh_path.write_text(
            _chinese_publications_markdown(
                professor, analysis, professor_publications, assignments,
                directions_by_id, start, end, zh,
            ),
            encoding="utf-8",
        )
        overview.extend([
            f"### [{display_name}]({profile_paths['zhCN']})",
            "",
            f"{' / '.join(zh(item['hub']) for item in professor['affiliations'] if item.get('hub')) or '单位未公开'} · 本轮检索到 {len(professor_publications)} 篇论文",
            "",
        ])
    (root / "All_Prof_Info.zh-CN.md").write_text("\n".join(overview), encoding="utf-8")


def _identities(markdown: str, label: str) -> list[str]:
    return re.findall(rf"^{re.escape(label)}: `([^`]+)`$", markdown, flags=re.MULTILINE)


def _links(markdown: str) -> Counter[str]:
    return Counter(re.findall(r"\[[^\]]*\]\((https?://[^)]+)\)", markdown))


_HEADING_KEYS = {
    "Professor directory": "professor-directory",
    "教授目录": "professor-directory",
    "Official profile and contact": "official-contact",
    "官方资料与联系方式": "official-contact",
    "Current HKUST(GZ) affiliations": "affiliations",
    "当前香港科技大学（广州）任职关系": "affiliations",
    "Research analysis": "research-analysis",
    "研究分析": "research-analysis",
    "Research interests": "research-interests",
    "研究兴趣": "research-interests",
    "Research areas": "research-areas",
    "研究领域": "research-areas",
    "Keywords": "keywords",
    "关键词": "keywords",
    "Generated research sub-directions": "subdirections",
    "生成的研究细分方向": "subdirections",
    "Publications retrieved in this update": "publications",
    "本轮检索到的论文": "publications",
    "Verification": "verification",
    "核验信息": "verification",
}


def _heading_signature(markdown: str) -> list[tuple[int, str]]:
    signature: list[tuple[int, str]] = []
    current_h2 = ""
    for hashes, title in re.findall(r"^(#{1,4}) (.+)$", markdown, flags=re.MULTILINE):
        level = len(hashes)
        if level == 1:
            key = "document-title"
        else:
            key = _HEADING_KEYS.get(title)
            if level == 2:
                current_h2 = key or f"unknown:{title}"
            if key is None and level == 3 and current_h2 == "publications":
                key = "publication"
            if key is None and level == 3 and current_h2 == "subdirections":
                key = "subdirection"
            if key is None and level == 3 and current_h2 == "professor-directory":
                key = "professor"
            if key is None:
                key = f"unknown:{title}"
        signature.append((level, key))
    return signature


def validate_bilingual_parity(english: str, chinese: str) -> list[str]:
    errors = []
    if _identities(english, "Professor identity") != _identities(chinese, "Professor identity"):
        errors.append("professor identities differ")
    if _identities(english, "Publication identity") != _identities(chinese, "Publication identity"):
        errors.append("publication identities differ")
    if _links(english) != _links(chinese):
        errors.append("external links differ")
    if _heading_signature(english) != _heading_signature(chinese):
        errors.append("heading structure differs")
    return errors


_SUBDIRECTION_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_SUBDIRECTION_REVIEW_STATUSES = frozenset({"pass", "limited", "block"})


def validate_research_subdirections(
    payload: dict[str, Any],
    professor_ids: set[str],
    publication_owners: dict[str, str | set[str]],
) -> None:
    """Validate professor-scoped research sub-directions and publication assignments."""
    def publication_is_owned_by(publication_id: str, profile_id: str) -> bool:
        owner = publication_owners.get(publication_id)
        if isinstance(owner, str):
            return owner == profile_id
        if isinstance(owner, set):
            return profile_id in owner
        return False

    if (
        not isinstance(payload, dict)
        or type(payload.get("schemaVersion")) is not int
        or payload["schemaVersion"] != 1
    ):
        raise SourceDataError("research subdirections require schema version 1")
    try:
        cutoff = date.fromisoformat(str(payload.get("cutoff")))
    except ValueError as error:
        raise SourceDataError("research subdirections require an ISO cutoff date") from error
    if (cutoff.month, cutoff.day) not in {(3, 1), (9, 1)}:
        raise SourceDataError("research subdirections cutoff must be March 1 or September 1")
    records = payload.get("professors")
    if not isinstance(records, list):
        raise SourceDataError("research subdirections professors must be a list")

    seen_professors: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            raise SourceDataError("research subdirection professor record must be an object")
        profile_id = clean(record.get("officialProfileId"))
        if not profile_id or profile_id not in professor_ids:
            raise SourceDataError("research subdirections reference an unknown professor")
        if profile_id in seen_professors:
            raise SourceDataError(f"duplicate research subdirections for professor {profile_id}")
        seen_professors.add(profile_id)

        def validate_review_status(status: Any, label: str) -> None:
            if status not in _SUBDIRECTION_REVIEW_STATUSES:
                raise SourceDataError(f"{label} has an invalid review status")
            if status == "block":
                raise SourceDataError(f"{label} is blocked")

        validate_review_status(record.get("reviewStatus"), f"professor {profile_id}")
        subdirections = record.get("subdirections")
        assignments = record.get("publicationAssignments")
        if not isinstance(subdirections, list) or not isinstance(assignments, list):
            raise SourceDataError(f"professor {profile_id} requires subdirections and publication assignments")
        if not subdirections:
            raise SourceDataError(f"professor {profile_id} requires at least one subdirection")

        direction_ids: set[str] = set()
        normalized_names: set[str] = set()
        has_limited_direction = False
        for direction in subdirections:
            if not isinstance(direction, dict):
                raise SourceDataError(f"professor {profile_id} has a malformed subdirection")
            direction_id = direction.get("id")
            if not isinstance(direction_id, str) or not _SUBDIRECTION_ID.fullmatch(direction_id):
                raise SourceDataError(f"professor {profile_id} has an invalid subdirection ID")
            if direction_id in direction_ids:
                raise SourceDataError(f"professor {profile_id} has a duplicate subdirection ID")
            direction_ids.add(direction_id)
            name = clean(direction.get("nameEn"))
            normalized_name = _normalized_title(name or "")
            if not normalized_name:
                raise SourceDataError(f"professor {profile_id} has an empty subdirection name")
            if normalized_name in normalized_names:
                raise SourceDataError(f"professor {profile_id} has a duplicate subdirection name")
            normalized_names.add(normalized_name)

            sentences = direction.get("explanationEn")
            if (
                not isinstance(sentences, list)
                or len(sentences) != 3
                or any(not isinstance(sentence, str) or not clean(sentence) for sentence in sentences)
            ):
                raise SourceDataError(f"subdirection {direction_id} requires exactly three non-empty sentences")
            if len({_normalized_title(sentence) for sentence in sentences}) != len(sentences):
                raise SourceDataError(f"subdirection {direction_id} has duplicate explanation sentences")
            evidence_publications = direction.get("evidencePublicationIds")
            if not isinstance(evidence_publications, list):
                raise SourceDataError(f"subdirection {direction_id} supporting publication IDs must be a list")
            if any(not isinstance(publication_id, str) or not clean(publication_id) for publication_id in evidence_publications):
                raise SourceDataError(f"subdirection {direction_id} supporting publication IDs must contain strings")
            if len(evidence_publications) != len(set(evidence_publications)):
                raise SourceDataError(f"subdirection {direction_id} has duplicate supporting publication IDs")
            for publication_id in evidence_publications:
                if not publication_is_owned_by(publication_id, profile_id):
                    raise SourceDataError(f"subdirection {direction_id} references a publication not owned by professor {profile_id}")

            evidence_urls = direction.get("evidenceUrls")
            if not isinstance(evidence_urls, list) or not evidence_urls:
                raise SourceDataError(f"subdirection {direction_id} requires supporting evidence URLs")
            for url in evidence_urls:
                if not isinstance(url, str):
                    raise SourceDataError(f"subdirection {direction_id} has an invalid evidence URL")
                if (urlparse(url).hostname or "").casefold() == "scholar.google.com":
                    raise SourceDataError(f"subdirection {direction_id} cannot use Google Scholar evidence")
                if not _valid_evidence(url) and not _valid_official_subdirection_evidence(url):
                    raise SourceDataError(f"subdirection {direction_id} has evidence outside approved research sources")
            if not evidence_publications and not any(
                _valid_official_subdirection_evidence(url) for url in evidence_urls
            ):
                raise SourceDataError(f"subdirection {direction_id} without publications requires official research evidence")
            direction_review_status = direction.get("reviewStatus")
            validate_review_status(direction_review_status, f"subdirection {direction_id}")
            if direction_review_status == "limited":
                has_limited_direction = True
                limitation = direction.get("limitationEn")
                if not isinstance(limitation, str) or not clean(limitation):
                    raise SourceDataError(f"limited subdirection {direction_id} requires a non-empty limitation")
                if limitation != sentences[2]:
                    raise SourceDataError(f"limited subdirection {direction_id} limitation must match the third explanation sentence")
            elif "limitationEn" in direction:
                raise SourceDataError(f"pass subdirection {direction_id} cannot include a limitation")
            if not evidence_publications and (
                record.get("reviewStatus") != "limited" or direction_review_status != "limited"
            ):
                raise SourceDataError(f"official-only subdirection {direction_id} and its professor record must be limited")
        expected_professor_status = "limited" if has_limited_direction else "pass"
        if record.get("reviewStatus") != expected_professor_status:
            raise SourceDataError(f"professor {profile_id} has an inconsistent aggregate review status")

        assigned_publications: set[str] = set()
        for assignment in assignments:
            if not isinstance(assignment, dict):
                raise SourceDataError(f"professor {profile_id} has a malformed publication assignment")
            publication_id = assignment.get("publicationId")
            if not isinstance(publication_id, str) or not clean(publication_id):
                raise SourceDataError("publication ID must be a non-empty string")
            if not publication_is_owned_by(publication_id, profile_id):
                raise SourceDataError(f"publication assignment references a publication not owned by professor {profile_id}")
            if publication_id in assigned_publications:
                raise SourceDataError(f"professor {profile_id} has duplicate publication assignments")
            assigned_publications.add(publication_id)
            assigned_directions = assignment.get("subdirectionIds")
            if not isinstance(assigned_directions, list):
                raise SourceDataError(f"publication {publication_id} subdirection IDs must be a list")
            if any(not isinstance(direction_id, str) or not clean(direction_id) for direction_id in assigned_directions):
                raise SourceDataError(f"publication {publication_id} subdirection IDs must contain strings")
            if len(assigned_directions) != len(set(assigned_directions)):
                raise SourceDataError(f"publication {publication_id} has duplicate subdirection IDs")
            unknown_directions = set(assigned_directions) - direction_ids
            if unknown_directions:
                raise SourceDataError(f"publication {publication_id} references an unknown subdirection")
            unassigned_reason = assignment.get("unassignedReasonEn")
            if assigned_directions and unassigned_reason is not None:
                raise SourceDataError(f"publication {publication_id} cannot be assigned and unassigned")
            if not assigned_directions and (not isinstance(unassigned_reason, str) or not clean(unassigned_reason)):
                raise SourceDataError(f"publication {publication_id} requires an unassigned reason")

        owned_publications = {
            publication_id
            for publication_id in publication_owners
            if publication_is_owned_by(publication_id, profile_id)
        }
        if assigned_publications != owned_publications:
            raise SourceDataError(f"publication assignments do not cover professor {profile_id}'s publications")

    missing = professor_ids - seen_professors
    if missing:
        raise SourceDataError(f"research subdirections are missing professor IDs: {', '.join(sorted(missing))}")


def validate_research_analysis(analyses: list[dict[str, Any]], professor_ids: set[str]) -> None:
    seen: set[str] = set()
    for analysis in analyses:
        profile_id = clean(analysis.get("officialProfileId"))
        if not profile_id or profile_id not in professor_ids:
            raise SourceDataError("research analysis references an unknown professor")
        if profile_id in seen:
            raise SourceDataError(f"duplicate research analysis for professor {profile_id}")
        seen.add(profile_id)
        for field in ("researchInterests", "researchAreas", "keywords", "notes", "evidenceUrls"):
            if not isinstance(analysis.get(field), list):
                raise SourceDataError(f"research analysis {profile_id} field {field} must be a list")
        invalid_evidence = [url for url in analysis["evidenceUrls"] if not isinstance(url, str) or not _valid_evidence(url)]
        if invalid_evidence:
            raise SourceDataError(f"research analysis {profile_id} contains evidence outside approved research sources")
        if analysis.get("status") == "complete" and not analysis["evidenceUrls"]:
            raise SourceDataError(f"complete research analysis {profile_id} requires approved research evidence")
        date.fromisoformat(str(analysis.get("lastVerifiedOn")))
    missing = sorted(professor_ids - seen)
    if missing:
        raise SourceDataError(f"research analysis is missing professor IDs: {', '.join(missing)}")


def validate_retrieval_statuses(analyses: list[dict[str, Any]]) -> None:
    allowed = {"complete", "incomplete", "blocked"}
    for analysis in analyses:
        status = analysis.get("status")
        if status not in allowed:
            raise SourceDataError(f"professor {analysis.get('officialProfileId')} has non-publishable retrieval status: {status}")
        if status != "complete" and not analysis.get("notes"):
            raise SourceDataError(f"professor {analysis.get('officialProfileId')} must disclose retrieval limitations")


def build_research_analysis_skeleton(
    professors: list[dict[str, Any]],
    cutoff: str,
) -> list[dict[str, Any]]:
    date.fromisoformat(cutoff)
    return [
        {
            "officialProfileId": professor["officialProfileId"],
            "researchInterests": [],
            "researchAreas": [],
            "keywords": [],
            "summaryEn": "",
            "status": "not-started",
            "notes": ["OpenAlex, paperscraper, and independent arXiv verification have not been completed for this update."],
            "evidenceUrls": [],
            "lastVerifiedOn": cutoff,
        }
        for professor in professors
    ]


def build_research_analysis_from_evidence(
    professors: list[dict[str, Any]],
    evidence_records: list[dict[str, Any]],
    cutoff: str,
) -> list[dict[str, Any]]:
    """Create concise original English analysis from disambiguated OpenAlex metadata."""
    date.fromisoformat(cutoff)
    evidence_by_id = {str(record.get("officialProfileId") or ""): record for record in evidence_records}
    analyses: list[dict[str, Any]] = []

    def ranked(counter: Counter[str], limit: int) -> list[str]:
        return [value for value, _ in sorted(counter.items(), key=lambda item: (-item[1], item[0].casefold()))[:limit]]

    for professor in professors:
        profile_id = professor["officialProfileId"]
        record = evidence_by_id.get(profile_id, {})
        openalex = record.get("openalex") or {}
        works = openalex.get("works") or [] if openalex.get("status") == "complete" else []
        topic_counts: Counter[str] = Counter()
        keyword_counts: Counter[str] = Counter()
        for work in works:
            topic_counts.update(str(value) for value in work.get("topics") or [] if value)
            keyword_counts.update(str(value) for value in work.get("keywords") or [] if value)
        areas = ranked(topic_counts, 5)
        interests = areas[:3]
        keywords = sorted(ranked(keyword_counts, 8), key=str.casefold)
        limitations = list(record.get("failedSkipped") or []) + list(record.get("blocked") or [])
        notes = []
        if limitations:
            notes.append("Some approved research sources were unavailable after bounded attempts: " + ", ".join(sorted(set(limitations))) + ".")
        if not works:
            notes.append("No disambiguated OpenAlex work was available in the update window.")
        if works and not areas:
            notes.append("The retrieved records did not expose stable topic metadata for classification.")
        if interests:
            focus = ", ".join(interests[:-1]) + (f", and {interests[-1]}" if len(interests) > 1 else interests[0])
            if len(interests) == 1:
                focus = interests[0]
            summary = f"Recent indexed publications most consistently center on {focus}."
            if keywords:
                summary += " Recurring indexed terminology includes " + ", ".join(keywords[:5]) + "."
            summary += " This classification summarizes recurring metadata patterns and does not reproduce a source abstract."
        else:
            summary = "The approved evidence available for this update was insufficient to infer a stable research classification."
        evidence_urls = []
        for url in [openalex.get("evidenceUrl"), *(work.get("url") for work in works[:4])]:
            if isinstance(url, str) and _valid_evidence(url) and url not in evidence_urls:
                evidence_urls.append(url)
        analyses.append({
            "officialProfileId": profile_id,
            "researchInterests": interests,
            "researchAreas": areas,
            "keywords": keywords,
            "summaryEn": summary,
            "status": "complete" if works and not notes else "incomplete",
            "notes": notes,
            "evidenceUrls": evidence_urls,
            "lastVerifiedOn": cutoff,
        })
    return analyses


def record_retrieval_attempt(
    item: dict[str, Any],
    *,
    source: str,
    phase: str,
    succeeded: bool,
) -> None:
    """Record bounded discovery progress without storing response or personal details."""
    if source not in RETRIEVAL_SOURCES:
        raise ValueError("retrieval source is outside the approved source plan")
    if phase not in {"initial", "targeted"}:
        raise ValueError("retrieval phase must be initial or targeted")
    item.setdefault("attempts", []).append({"source": source, "phase": phase, "succeeded": succeeded})
    if succeeded:
        item["status"] = "retrieved"
        item["targetedFailures"] = 0
        return
    if phase == "initial":
        item["status"] = "retry-pending"
        return
    failures = int(item.get("targetedFailures") or 0) + 1
    item["targetedFailures"] = failures
    item["status"] = "failed-skipped" if failures >= 3 else "retry-pending"


def build_publication_work_queue(
    professors: list[dict[str, Any]],
    start: str,
    end: str,
) -> dict[str, Any]:
    if not in_window(start, end, start) or not in_window(start, end, end):
        raise ValueError("invalid inclusive publication window")
    work = []
    for professor in professors:
        openalex_url = "https://api.openalex.org/authors?" + urlencode({
            "search": professor["nameEn"], "per_page": 5,
        })
        arxiv_query = f'au:"{professor["nameEn"]}"'
        arxiv_url = "https://export.arxiv.org/api/query?" + urlencode({
            "search_query": arxiv_query,
            "start": 0,
            "max_results": 100,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        })
        author_names = [name for name in (professor["nameEn"], professor.get("nameZh")) if name]
        work.append({
            "officialProfileId": professor["officialProfileId"],
            "nameEn": professor["nameEn"],
            "nameZh": professor.get("nameZh"),
            "openAlexAuthorSearchUrl": openalex_url,
            "paperscraperQuery": {"authorNames": list(dict.fromkeys(author_names))},
            "arxivValidationUrl": arxiv_url,
            "status": "pending",
            "targetedFailures": 0,
            "attempts": [],
            "notes": [],
        })
    return {
        "schemaVersion": 1,
        "window": {"start": start, "end": end, "inclusive": True},
        "sources": list(RETRIEVAL_SOURCES),
        "professors": work,
    }
