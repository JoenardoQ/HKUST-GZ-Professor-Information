#!/usr/bin/env python3
"""Run one bounded paperscraper source job without leaking record data to stdout."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from retrieve_research_sources import collect_paperscraper_source


def _collectors() -> dict[str, Any]:
    from paperscraper.arxiv import get_arxiv_papers_api
    from paperscraper.citations.utils import author_name_to_ssaid
    from paperscraper.load_dumps import QUERY_FN_DICT
    from paperscraper.pubmed import get_pubmed_papers
    from semanticscholar import SemanticScholar

    def name(query: dict[str, Any]) -> str:
        names = query.get("authorNames") or []
        if not names:
            raise ValueError("author name is required")
        return str(names[0])

    def arxiv(query: dict[str, Any]):
        return get_arxiv_papers_api(
            f'au:"{name(query)}"', max_results=100, verbose=False,
            client_options={"page_size": 100, "delay_seconds": 3, "num_retries": 2},
        )

    def pubmed(query: dict[str, Any]):
        return get_pubmed_papers(
            f'"{name(query)}"[Author]',
            fields=["title", "authors", "date", "abstract", "journal", "doi", "pubmed_id"],
            max_results=200,
        )

    def xrxiv(source: str):
        def collect(query: dict[str, Any]):
            collector = QUERY_FN_DICT.get(source)
            if collector is None:
                raise RuntimeError(f"missing local {source} dump")
            return collector([re.escape(name(query))], fields=["authors"])
        return collect

    def semantic_scholar(query: dict[str, Any]):
        author_id, _ = author_name_to_ssaid(name(query))
        if not author_id or author_id == "-1":
            return []
        client = SemanticScholar(timeout=20, api_key=os.environ.get("SS_API_KEY"), retry=True)
        papers = client.get_author_papers(
            author_id,
            fields=[
                "paperId", "title", "authors", "publicationDate", "year",
                "externalIds", "venue", "abstract", "citationCount",
            ],
            limit=1000,
        )
        return [dict(paper.raw_data) for paper in papers]

    return {
        "arxiv": arxiv,
        "pubmed": pubmed,
        "biorxiv": xrxiv("biorxiv"),
        "medrxiv": xrxiv("medrxiv"),
        "chemrxiv": xrxiv("chemrxiv"),
        "semantic-scholar": semantic_scholar,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = collect_paperscraper_source(
                payload["source"], payload["query"], payload["start"], payload["end"], _collectors(),
            )
    except Exception as error:
        message = str(error).casefold()
        return 77 if any(token in message for token in ("captcha", "forbidden", "rate limit", "429", "403")) else 1
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
