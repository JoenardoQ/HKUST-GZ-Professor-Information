#!/usr/bin/env python3
"""Retrieve resumable evidence from the approved research-source pipeline."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlparse

try:
    from .professor_information import SourceDataError, in_window
except ImportError:  # Direct script execution puts this directory on sys.path.
    from professor_information import SourceDataError, in_window

USER_AGENT = "HKUST-GZ-Professor-Information/1.0 public-research-sync"
ATOM = "{http://www.w3.org/2005/Atom}"
PAPERSCRAPER_SOURCES = (
    "arxiv", "pubmed", "biorxiv", "medrxiv", "chemrxiv",
    "semantic-scholar",
)
SOURCES = (
    "OpenAlex",
    *(f"paperscraper:{source}" for source in PAPERSCRAPER_SOURCES),
    "arXivValidation",
)


def select_sources(value: str | None) -> tuple[str, ...]:
    if value is None:
        return tuple(SOURCES)
    selected = tuple(part.strip() for part in value.split(",") if part.strip())
    if not selected or any(source not in SOURCES for source in selected):
        raise ValueError("retrieval source is outside the approved source plan")
    return selected


class SourceBlockedError(SourceDataError):
    """Raised for a source-wide access or rate-limit response."""


def _doi(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return re.sub(r"^https?://(?:dx\.)?doi\.org/", "", text, flags=re.I).casefold()


def _abstract_from_inverted(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    positioned = [(int(position), str(word)) for word, positions in value.items() for position in positions]
    return " ".join(word for _, word in sorted(positioned)) or None


def _strict_json_value(value: Any) -> Any:
    """Convert pandas-style non-finite missing values before normalization."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _strict_json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_strict_json_value(item) for item in value]
    return value


def parse_openalex_works(payload: dict[str, Any], start: str, end: str) -> list[dict[str, Any]]:
    works = []
    for item in payload.get("results") or []:
        publication_date = str(item.get("publication_date") or "")[:10]
        if not publication_date or not in_window(start, end, publication_date):
            continue
        ids = item.get("ids") or {}
        arxiv_url = next((str(value) for value in ids.values() if "arxiv.org/abs/" in str(value)), None)
        works.append({
            "openAlexId": str(item.get("id") or "").rstrip("/").split("/")[-1] or None,
            "doi": _doi(item.get("doi")),
            "arxivId": arxiv_url.rstrip("/").split("/")[-1] if arxiv_url else None,
            "title": " ".join(str(item.get("display_name") or "").split()),
            "summary": _abstract_from_inverted(item.get("abstract_inverted_index")),
            "effectiveDate": publication_date,
            "datePrecision": "day",
            "publicationType": item.get("type") or "unknown",
            "venue": ((item.get("primary_location") or {}).get("source") or {}).get("display_name"),
            "topics": sorted({str(topic.get("display_name")) for topic in item.get("topics") or [] if topic.get("display_name")}),
            "keywords": sorted({str(keyword.get("display_name")) for keyword in item.get("keywords") or [] if keyword.get("display_name")}),
            "url": str(item.get("id") or ""),
        })
    return works


def parse_arxiv_feed(document: str, start: str, end: str) -> list[dict[str, Any]]:
    root = ET.fromstring(document)
    works = []
    for entry in root.findall(f"{ATOM}entry"):
        published = (entry.findtext(f"{ATOM}published") or "")[:10]
        if not published or not in_window(start, end, published):
            continue
        raw_id = (entry.findtext(f"{ATOM}id") or "").rstrip("/").split("/")[-1]
        arxiv_id = re.sub(r"v\d+$", "", raw_id)
        works.append({
            "openAlexId": None,
            "doi": None,
            "arxivId": arxiv_id,
            "title": " ".join((entry.findtext(f"{ATOM}title") or "").split()),
            "summary": " ".join((entry.findtext(f"{ATOM}summary") or "").split()),
            "effectiveDate": published,
            "datePrecision": "day",
            "publicationType": "arXiv",
            "venue": "arXiv",
            "authors": [" ".join((node.findtext(f"{ATOM}name") or "").split()) for node in entry.findall(f"{ATOM}author")],
            "topics": sorted({node.attrib.get("term", "") for node in entry.findall(f"{ATOM}category") if node.attrib.get("term")}),
            "keywords": [],
            "url": f"https://arxiv.org/abs/{arxiv_id}",
        })
    return works


def normalize_paperscraper_rows(
    source: str,
    rows: list[dict[str, Any]],
    start: str,
    end: str,
) -> list[dict[str, Any]]:
    """Normalize paperscraper outputs without treating the wrapper as an identity oracle."""
    if source not in PAPERSCRAPER_SOURCES:
        raise ValueError("unsupported paperscraper source")
    works: list[dict[str, Any]] = []
    for raw_row in rows:
        row = _strict_json_value(raw_row)
        title = " ".join(str(row.get("title") or "").split())
        raw_date = row.get("publicationDate") or row.get("date") or row.get("published") or row.get("year")
        date_text = str(raw_date or "").strip()[:10]
        precision = "day"
        if re.fullmatch(r"\d{4}", date_text):
            precision = "year"
            year = int(date_text)
            if year < int(start[:4]) or year > int(end[:4]):
                continue
            date_text = f"{year:04d}-01-01"
        else:
            try:
                if not in_window(start, end, date_text):
                    continue
            except ValueError:
                continue
        if not title:
            continue
        external_ids = row.get("externalIds") or row.get("external_ids") or {}
        doi = _doi(row.get("doi") or external_ids.get("DOI"))
        arxiv_id = str(row.get("arxivId") or external_ids.get("ArXiv") or "").strip() or None
        if not arxiv_id and doi and doi.casefold().startswith("10.48550/arxiv."):
            arxiv_id = doi.split(".", 2)[-1]
        semantic_id = str(row.get("paperId") or row.get("semanticScholarId") or "").strip() or None
        pubmed_id = str(row.get("pubmed_id") or row.get("pubmedId") or row.get("pmid") or "").strip() or None
        if source == "semantic-scholar" and semantic_id:
            url = f"https://www.semanticscholar.org/paper/{quote(semantic_id, safe='')}"
        elif source == "pubmed" and pubmed_id:
            url = f"https://pubmed.ncbi.nlm.nih.gov/{quote(pubmed_id, safe='')}/"
        elif source == "arxiv" and arxiv_id:
            url = f"https://arxiv.org/abs/{quote(arxiv_id, safe='./')}"
        else:
            url = str(row.get("url") or row.get("entry_id") or "")
            if not url and doi:
                url = f"https://doi.org/{quote(doi, safe='/()')}"
        authors = row.get("authors") or []
        if isinstance(authors, str):
            authors = [part.strip() for part in re.split(r",|;", authors) if part.strip()]
        elif isinstance(authors, list):
            authors = [" ".join(str(author.get("name") if isinstance(author, dict) else author).split()) for author in authors]
        works.append({
            "source": source,
            "openAlexId": None,
            "semanticScholarId": semantic_id,
            "pubmedId": pubmed_id,
            "doi": doi,
            "arxivId": arxiv_id,
            "title": title,
            "summary": " ".join(str(row.get("abstract") or row.get("summary") or "").split()) or None,
            "effectiveDate": date_text,
            "datePrecision": precision,
            "publicationType": row.get("publicationType") or ("preprint" if source in {"arxiv", "biorxiv", "medrxiv", "chemrxiv"} else "article"),
            "venue": row.get("venue") or row.get("journal") or source,
            "authors": authors,
            "topics": sorted(set(row.get("topics") or [])),
            "keywords": sorted(set(row.get("keywords") or [])),
            "citations": row.get("citations") or row.get("citationCount"),
            "url": url,
        })
    return sorted(works, key=lambda work: (work["effectiveDate"], work["title"], work["url"]))


def collect_paperscraper_source(
    source: str,
    query: dict[str, Any],
    start: str,
    end: str,
    collectors: dict[str, Any],
) -> dict[str, Any]:
    """Run one injected paperscraper collector and normalize its public result."""
    if source not in PAPERSCRAPER_SOURCES:
        raise ValueError("unsupported paperscraper source")
    collector = collectors.get(source)
    if collector is None:
        raise SourceDataError("paperscraper source is unavailable")
    rows = collector(query)
    if hasattr(rows, "to_dict"):
        rows = rows.to_dict(orient="records")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise SourceDataError("paperscraper collector returned an invalid row set")
    return {
        "source": f"paperscraper:{source}",
        "status": "complete",
        "works": normalize_paperscraper_rows(source, rows, start, end),
    }


def _name_key(value: Any) -> tuple[str, ...]:
    ascii_text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().casefold()
    return tuple(sorted(re.findall(r"[a-z0-9]+", ascii_text)))


def _name_compact(value: Any) -> str:
    ascii_text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().casefold()
    return "".join(re.findall(r"[a-z0-9]+", ascii_text))


def _choose_author(candidates: list[dict[str, Any]], name: str, source: str) -> dict[str, Any] | None:
    exact = [
        candidate for candidate in candidates
        if _name_key(candidate.get("display_name") or candidate.get("name")) == _name_key(name)
        or _name_compact(candidate.get("display_name") or candidate.get("name")) == _name_compact(name)
    ]
    if not exact:
        return None

    def affiliations(candidate: dict[str, Any]) -> str:
        if source == "OpenAlex":
            institutions = list(candidate.get("last_known_institutions") or [])
            institutions.extend(item.get("institution") or {} for item in candidate.get("affiliations") or [])
            return " ".join(str(item.get("display_name") or "") for item in institutions).casefold()
        return " ".join(str(item) for item in candidate.get("affiliations") or []).casefold()

    affiliated = [candidate for candidate in exact if "hong kong university of science and technology" in affiliations(candidate) or "hkust" in affiliations(candidate)]
    return affiliated[0] if len(affiliated) == 1 else None


def openalex_work_cursor_url(
    author_id: str,
    start: str,
    end: str,
    cursor: str,
    api_key: str | None,
) -> str:
    parameters = {
        "filter": f"author.id:{author_id},from_publication_date:{start},to_publication_date:{end}",
        "per_page": 100,
        "cursor": cursor,
    }
    if api_key:
        parameters["api_key"] = api_key
    return "https://api.openalex.org/works?" + urlencode(parameters)


def curl_request_spec(url: str, timeout: int, headers: list[str] | None = None) -> tuple[list[str], bytes]:
    if "\n" in url or "\r" in url:
        raise SourceDataError("source URL contains control characters")
    escaped_url = url.replace("\\", "\\\\").replace('"', '\\"')
    command = [
        "curl", "-sS", "-L", "--proto", "=https", "--max-redirs", "3",
        "--max-time", str(timeout), "--connect-timeout", str(min(timeout, 10)),
        "-A", USER_AGENT,
    ]
    for header in headers or []:
        command.extend(["-H", header])
    command.extend(["-w", "\n%{http_code}", "--config", "-"])
    return command, f'url = "{escaped_url}"\n'.encode("utf-8")


def _request(url: str, timeout: int, headers: list[str] | None = None) -> bytes:
    if not shutil.which("curl"):
        raise SourceDataError("curl is required for bounded source retrieval")
    command, stdin = curl_request_spec(url, timeout, headers)
    response = subprocess.run(command, input=stdin, check=True, capture_output=True, timeout=timeout + 5)
    body, separator, status = response.stdout.rpartition(b"\n")
    if not separator or not status.isdigit():
        raise SourceDataError("source response omitted an HTTP status")
    code = int(status)
    if code in {401, 403, 429}:
        raise SourceBlockedError("source access is blocked or rate limited")
    if code < 200 or code >= 300:
        raise SourceDataError("source returned a non-success response")
    if len(body) > 3_000_000:
        raise SourceDataError("source response exceeds the three-megabyte limit")
    return body


def _json(url: str, timeout: int, headers: list[str] | None = None) -> dict[str, Any]:
    value = json.loads(_request(url, timeout, headers))
    if not isinstance(value, dict):
        raise SourceDataError("source JSON must be an object")
    return value


def _retrieve(source: str, item: dict[str, Any], start: str, end: str, timeout: int) -> dict[str, Any]:
    if source == "arXivValidation":
        document = _request(item["arxivValidationUrl"], timeout).decode("utf-8", errors="strict")
        return {"source": source, "status": "complete", "evidenceUrl": item["arxivValidationUrl"], "works": parse_arxiv_feed(document, start, end)}

    if source == "OpenAlex":
        key = os.environ.get("OPENALEX_API_KEY")
        search_url = item["openAlexAuthorSearchUrl"] + (("&" if "?" in item["openAlexAuthorSearchUrl"] else "?") + urlencode({"api_key": key}) if key else "")
        search = _json(search_url, timeout)
        author = _choose_author(search.get("results") or [], item["nameEn"], source)
        if author is None:
            return {"source": source, "status": "ambiguous", "evidenceUrl": item["openAlexAuthorSearchUrl"], "works": []}
        author_id = str(author["id"]).rstrip("/").split("/")[-1]
        cursor: str | None = "*"
        declared_count: int | None = None
        works: list[dict[str, Any]] = []
        seen_cursors: set[str] = set()
        seen_work_ids: set[str] = set()
        while cursor is not None:
            if cursor in seen_cursors:
                raise SourceDataError("OpenAlex pagination repeated a cursor")
            seen_cursors.add(cursor)
            page = _json(openalex_work_cursor_url(author_id, start, end, cursor, key), timeout)
            meta = page.get("meta")
            page_works = page.get("results")
            if not isinstance(meta, dict) or not isinstance(page_works, list) or any(not isinstance(work, dict) for work in page_works):
                raise SourceDataError("OpenAlex work page has an invalid shape")
            page_count = int(meta.get("count") or 0)
            if page_count < 0:
                raise SourceDataError("OpenAlex work count must not be negative")
            if declared_count is None:
                declared_count = page_count
            elif page_count != declared_count:
                raise SourceDataError("OpenAlex work count changed during pagination")
            for work in page_works:
                work_id = str(work.get("id") or "")
                if not work_id or work_id in seen_work_ids:
                    raise SourceDataError("OpenAlex pagination returned a missing or repeated work ID")
                seen_work_ids.add(work_id)
                works.append(work)
            next_cursor = meta.get("next_cursor")
            if next_cursor in (None, ""):
                cursor = None
            else:
                if not page_works:
                    raise SourceDataError("OpenAlex pagination returned an empty non-terminal page")
                cursor = str(next_cursor)
        if len(works) != (declared_count or 0):
            raise SourceDataError("OpenAlex pagination is incomplete")
        payload = {"results": works}
        return {
            "source": source, "status": "complete", "evidenceUrl": f"https://openalex.org/{author_id}",
            "authorId": author_id, "truncated": False,
            "works": parse_openalex_works(payload, start, end),
        }

    if source.startswith("paperscraper:"):
        paperscraper_source = source.split(":", 1)[1]
        payload = {
            "source": paperscraper_source,
            "query": item["paperscraperQuery"],
            "start": start,
            "end": end,
        }
        worker = Path(__file__).with_name("paperscraper_worker.py")
        with tempfile.TemporaryDirectory(prefix="prof-info-paperscraper-") as temporary:
            input_path = Path(temporary) / "input.json"
            output_path = Path(temporary) / "output.json"
            input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            response = subprocess.run(
                [sys.executable, str(worker), "--input", str(input_path), "--output", str(output_path)],
                capture_output=True,
                timeout=max(timeout, 5),
                check=False,
            )
            if response.returncode == 77:
                raise SourceBlockedError("paperscraper upstream is blocked or rate limited")
            if response.returncode != 0 or not output_path.is_file():
                raise SourceDataError("paperscraper worker failed")
            result = json.loads(output_path.read_text(encoding="utf-8"))
        if not isinstance(result, dict) or result.get("source") != source:
            raise SourceDataError("paperscraper worker returned an invalid result")
        return result

    raise ValueError("unsupported research source")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _load_attempt_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"initialAttempted": False, "targetedFailures": 0, "status": "pending"}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SourceDataError("retrieval attempt state must be a JSON object")
    failures = value.get("targetedFailures")
    status = value.get("status")
    if not isinstance(value.get("initialAttempted"), bool) or not isinstance(failures, int) or failures < 0 or status not in {"pending", "retry-pending", "failed-skipped", "retrieved", "blocked"}:
        raise SourceDataError("retrieval attempt state is invalid")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True)
    parser.add_argument("--cutoff", required=True)
    parser.add_argument("--queue", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--cache-dir", type=Path, default=Path(".sync-cache/research-evidence"))
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--delay", type=float, default=1.1)
    parser.add_argument("--sources", default=None, help="Comma-separated approved sources to resume")
    parser.add_argument("--blocked-sources", default=None, help="Comma-separated sources already failed by bounded probes")
    args = parser.parse_args()
    queue_path = args.queue or Path(f"reports/{args.cutoff}-publication-work-queue.json")
    output = args.output or Path(f"reports/{args.cutoff}-retrieval-evidence.json")
    queue = json.loads(queue_path.read_text(encoding="utf-8"))
    items = queue.get("professors")
    if not isinstance(items, list) or not items:
        raise SourceDataError("retrieval queue must contain professors")
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, dict[str, Any]] = {}
    prior_failed: set[tuple[str, str]] = set()
    prior_blocked: set[tuple[str, str]] = set()
    if output.is_file():
        existing = _strict_json_value(json.loads(output.read_text(encoding="utf-8")))
        expected_window = {"start": args.start, "end": args.cutoff, "inclusive": True}
        records = existing.get("records") if isinstance(existing, dict) else None
        if not isinstance(existing, dict) or existing.get("schemaVersion") != 2 or existing.get("window") != expected_window or not isinstance(records, list):
            raise SourceDataError("existing retrieval evidence has an incompatible schema or window")
        queue_ids = {str(item.get("officialProfileId") or "") for item in items}
        for record in records:
            if not isinstance(record, dict):
                raise SourceDataError("existing retrieval evidence contains an invalid record")
            profile_id = str(record.get("officialProfileId") or "")
            if not profile_id or profile_id not in queue_ids or profile_id in results:
                raise SourceDataError("existing retrieval evidence does not match the queue")
            results[profile_id] = {
                key: value for key, value in record.items()
                if key not in {"officialProfileId", "failedSkipped", "blocked"}
            }
            openalex = results[profile_id].get("openalex")
            if isinstance(openalex, dict) and openalex.get("status") == "complete" and openalex.get("truncated") is not False:
                del results[profile_id]["openalex"]
                prior_failed.add((profile_id, "OpenAlex"))
            prior_failed.update((profile_id, source) for source in record.get("failedSkipped") or [] if source in SOURCES)
            prior_blocked.update((profile_id, source) for source in record.get("blocked") or [] if source in SOURCES)
    pending: list[tuple[dict[str, Any], str]] = []
    blocked: set[tuple[str, str]] = set(prior_blocked)
    failed_current: set[tuple[str, str]] = set()
    blocked_sources: set[str] = set(select_sources(args.blocked_sources)) if args.blocked_sources else set()
    active_sources = select_sources(args.sources)
    jobs = [(item, source) for item in items for source in active_sources]
    source_keys = {
        "OpenAlex": "openalex",
        "arXivValidation": "arxivValidation",
        **{f"paperscraper:{source}": source for source in PAPERSCRAPER_SOURCES},
    }

    def save_result(profile_id: str, source: str, source_key: str, result: dict[str, Any]) -> None:
        if source.startswith("paperscraper:"):
            results.setdefault(profile_id, {}).setdefault("paperscraper", {})[source_key] = result
        else:
            results.setdefault(profile_id, {})[source_key] = result

    for index, (item, source) in enumerate(jobs):
        profile_id, source_key = str(item["officialProfileId"]), source_keys[source]
        checkpoint_key = "arxiv" if source == "arXivValidation" else (f"paperscraper-{source_key}" if source.startswith("paperscraper:") else source_key)
        checkpoint = args.cache_dir / f"{profile_id}-{checkpoint_key}.json"
        attempt_path = args.cache_dir / f"{profile_id}-{checkpoint_key}.attempts.json"
        attempt_state = _load_attempt_state(attempt_path)
        requested = False
        result = None
        if checkpoint.is_file():
            cached_result = _strict_json_value(json.loads(checkpoint.read_text(encoding="utf-8")))
            if source != "OpenAlex" or cached_result.get("status") == "ambiguous" or (
                cached_result.get("status") == "complete" and cached_result.get("truncated") is False
            ):
                result = cached_result
        if result is not None:
            pass
        elif source in blocked_sources:
            blocked.add((profile_id, source))
            attempt_state["initialAttempted"] = True
            attempt_state["status"] = "blocked"
            _write_json(attempt_path, attempt_state)
        elif attempt_state["status"] == "blocked":
            blocked.add((profile_id, source))
        elif attempt_state["status"] == "failed-skipped" or attempt_state["targetedFailures"] >= 3:
            failed_current.add((profile_id, source))
        elif attempt_state["initialAttempted"]:
            pending.append((item, source))
        else:
            requested = True
            try:
                result = _retrieve(source, item, args.start, args.cutoff, args.timeout)
            except SourceBlockedError:
                blocked_sources.add(source)
                blocked.add((profile_id, source))
                attempt_state["initialAttempted"] = True
                attempt_state["status"] = "blocked"
                _write_json(attempt_path, attempt_state)
            except Exception:
                attempt_state["initialAttempted"] = True
                attempt_state["status"] = "retry-pending"
                _write_json(attempt_path, attempt_state)
                pending.append((item, source))
            else:
                _write_json(checkpoint, result)
                attempt_state["initialAttempted"] = True
                attempt_state["status"] = "retrieved"
                _write_json(attempt_path, attempt_state)
        if result is not None:
            save_result(profile_id, source, source_key, result)
            prior_failed.discard((profile_id, source))
            failed_current.discard((profile_id, source))
            blocked.discard((profile_id, source))
        print(f"{index + 1}/{len(jobs)}", flush=True)
        if requested and args.delay and index + 1 < len(jobs):
            time.sleep(args.delay)

    for _retry_round in range(3):
        retry_jobs, pending = pending, []
        if not retry_jobs:
            break
        for index, (item, source) in enumerate(retry_jobs):
            profile_id, source_key = str(item["officialProfileId"]), source_keys[source]
            checkpoint_key = "arxiv" if source == "arXivValidation" else (f"paperscraper-{source_key}" if source.startswith("paperscraper:") else source_key)
            attempt_path = args.cache_dir / f"{profile_id}-{checkpoint_key}.attempts.json"
            attempt_state = _load_attempt_state(attempt_path)
            if source in blocked_sources:
                blocked.add((profile_id, source))
                attempt_state["status"] = "blocked"
                _write_json(attempt_path, attempt_state)
            elif attempt_state["targetedFailures"] >= 3:
                attempt_state["status"] = "failed-skipped"
                failed_current.add((profile_id, source))
                _write_json(attempt_path, attempt_state)
            else:
                try:
                    result = _retrieve(source, item, args.start, args.cutoff, args.timeout)
                except SourceBlockedError:
                    blocked_sources.add(source)
                    blocked.add((profile_id, source))
                    attempt_state["status"] = "blocked"
                    _write_json(attempt_path, attempt_state)
                except Exception:
                    attempt_state["targetedFailures"] += 1
                    if attempt_state["targetedFailures"] >= 3:
                        attempt_state["status"] = "failed-skipped"
                        failed_current.add((profile_id, source))
                    else:
                        attempt_state["status"] = "retry-pending"
                        pending.append((item, source))
                    _write_json(attempt_path, attempt_state)
                else:
                    _write_json(args.cache_dir / f"{profile_id}-{checkpoint_key}.json", result)
                    attempt_state["status"] = "retrieved"
                    _write_json(attempt_path, attempt_state)
                    save_result(profile_id, source, source_key, result)
                    prior_failed.discard((profile_id, source))
                    failed_current.discard((profile_id, source))
                    blocked.discard((profile_id, source))
            print(f"{index + 1}/{len(retry_jobs)}", flush=True)
            if args.delay and index + 1 < len(retry_jobs):
                time.sleep(args.delay)

    failed = prior_failed | failed_current | {(str(item["officialProfileId"]), source) for item, source in pending}
    report = []
    for item in items:
        profile_id = str(item["officialProfileId"])
        record = {"officialProfileId": profile_id, **results.get(profile_id, {})}
        record["failedSkipped"] = [source for source in SOURCES if (profile_id, source) in failed]
        record["blocked"] = [source for source in SOURCES if (profile_id, source) in blocked or source in blocked_sources]
        report.append(record)
    _write_json(output, {"schemaVersion": 2, "window": {"start": args.start, "end": args.cutoff, "inclusive": True}, "records": report})


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
