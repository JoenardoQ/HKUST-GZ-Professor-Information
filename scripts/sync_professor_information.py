#!/usr/bin/env python3
"""Synchronize public identity, contact, link, and affiliation basics."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    from .professor_information import SourceDataError, normalize_faculty_rows
except ImportError:  # Direct script execution puts this directory on sys.path.
    from professor_information import SourceDataError, normalize_faculty_rows

PAGE_API = "https://facultyprofiles.hkust-gz.edu.cn/api/itdcms-rpc/profile/page"
USER_AGENT = "HKUST-GZ-Professor-Information/1.0 public-data-sync"


def request_json(url: str, *, retries: int = 3, timeout: int = 30) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            if shutil.which("curl"):
                response = subprocess.run(
                    [
                        "curl", "-fsS", "--proto", "=https", "--max-time", str(timeout),
                        "--connect-timeout", str(min(timeout, 10)), "-A", USER_AGENT, url,
                    ],
                    check=True,
                    capture_output=True,
                    timeout=timeout + 5,
                )
                payload = json.loads(response.stdout)
            else:
                request = Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
                with urlopen(request, timeout=timeout) as response:
                    if response.geturl() != url:
                        raise SourceDataError(f"unexpected redirect from {url}")
                    payload = json.load(response)
            if not isinstance(payload, dict) or payload.get("code") not in (None, 0, 200, "0", "200"):
                raise SourceDataError(f"invalid API response from {url}")
            return payload
        except Exception as error:
            last_error = error
            if attempt + 1 < retries:
                time.sleep(2 ** attempt)
    raise SourceDataError(f"unable to read {url} after {retries} attempts") from last_error


def fetch_roster(page_size: int = 600) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    expected_total: int | None = None
    signatures: set[str] = set()
    page = 1
    while True:
        query = urlencode({
            "languageType": "en",
            "size": page_size,
            "pages": page,
            "dataSourcesCodes": "OUTSIDE_NC",
            "facultyType": "GZ",
            "excludeJobTypes": "Visiting",
        })
        payload = request_json(f"{PAGE_API}?{query}")
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("list"), list):
            raise SourceDataError(f"faculty page {page} has no data.list")
        total = data.get("total")
        if not isinstance(total, int) or total < 1:
            raise SourceDataError(f"faculty page {page} has no valid declared total")
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            raise SourceDataError(f"declared faculty total changed from {expected_total} to {total}")
        page_rows = data["list"]
        signature = json.dumps(page_rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if page_rows and signature in signatures:
            raise SourceDataError(f"faculty page {page} repeats an earlier page")
        signatures.add(signature)
        rows.extend(page_rows)
        if len(rows) > expected_total:
            raise SourceDataError("faculty API returned more rows than its declared total")
        if len(rows) == expected_total:
            return rows
        if not page_rows:
            raise SourceDataError("faculty API stopped before its declared total")
        page += 1


def load_baseline(path: Path) -> set[str]:
    if not path.exists():
        return set()
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise SourceDataError("existing professors.json must contain a list")
    return {str(item["officialProfileId"]) for item in value}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cutoff", required=True, help="Verification date in YYYY-MM-DD")
    parser.add_argument("--output", type=Path, default=Path("data/professors.json"))
    parser.add_argument("--removed-report", type=Path, default=None)
    parser.add_argument("--roster-cache", type=Path, default=None)
    args = parser.parse_args()
    baseline_ids = load_baseline(args.output)
    roster_cache = args.roster_cache or Path(f".sync-cache/faculty-roster-{args.cutoff}.json")
    if roster_cache.is_file():
        cached_roster = json.loads(roster_cache.read_text(encoding="utf-8"))
        if isinstance(cached_roster, dict):
            cached_data = cached_roster.get("data")
            rows = cached_data.get("list") if isinstance(cached_data, dict) else None
            if not isinstance(rows, list) or cached_data.get("total") != len(rows):
                raise SourceDataError("cached faculty API response is incomplete")
        else:
            rows = cached_roster
    else:
        rows = fetch_roster()
        roster_cache.parent.mkdir(parents=True, exist_ok=True)
        roster_cache.write_text(json.dumps(rows, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    if not isinstance(rows, list) or not rows:
        raise SourceDataError("cached faculty roster is empty or invalid")
    basic_rows = []
    for index, row in enumerate(rows):
        basic_rows.append({**row, "lastVerifiedOn": args.cutoff})
        print(f"{index + 1}/{len(rows)}", flush=True)
    professors, removed = normalize_faculty_rows(basic_rows, baseline_ids=baseline_ids)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(professors, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report = args.removed_report or Path(f"reports/{args.cutoff}-roster-diff.json")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({"cutoff": args.cutoff, "declaredTotal": len(rows), "normalizedTotal": len(professors), "removedBaselineIds": removed}, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
