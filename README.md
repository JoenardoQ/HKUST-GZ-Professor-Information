# HKUST(GZ) Professor Information

[English](README.md) | [简体中文](README.zh-CN.md)

This repository maintains a reviewed, machine-validated directory of public professional information for the current HKUST(GZ) Faculty Profiles roster. It also records publications retrieved during a defined update window through OpenAlex and `paperscraper`, with an independent arXiv verification pass.

The publication section is non-exhaustive. “No publication retrieved” is not evidence that a professor published nothing.

## Current update contract

- Cutoff: `2026-09-01`
- Inclusive publication window: `2024-09-01` through `2026-09-01`
- Future runs: March 1 and September 1, each covering exactly the preceding two years, inclusive
- Branch: `codex/prof-info-YYYY-MM-DD[-NN]`; retain the branch after merge
- Integration: Owner-reviewed pull request into protected `main`; no automatic merge or deployment
- Primary author-disambiguation and publication-list source: OpenAlex
- Unified collection layer: `paperscraper` for arXiv, PubMed, bioRxiv, medRxiv, ChemRxiv, and Semantic Scholar
- Independent verification source: arXiv; it validates matching records and does not create publication candidates by itself
- Basic identity/contact source: [HKUST(GZ) Faculty Profiles](https://facultyprofiles.hkust-gz.edu.cn/)

## Public fields

Each professor record may expose:

- official Faculty Profiles ID and Faculty Profiles page URL;
- Chinese and English names;
- public work email and public office telephone;
- personal website and source-provided `permaLink` when published;
- current public GZ titles, Hubs, Thrusts, and units;
- Google Scholar identifier when the official profile publishes one (retained as a basic field only, not used for publication retrieval);
- research-source-derived interests, areas, keywords, and explanatory summary;
- retrieval status, limitations, evidence links, and last-verification date; and
- publications retrieved in the update window, with stable identity and evidence links.

`officialProfileUrl` and `permaLink` are deliberately separate. The Faculty Profiles UI opens a person through its official profile ID. The API's `permaLink` can instead point to an Institutional Repository author profile or another official scholarly page; it must be preserved as returned, not mislabeled as the Faculty Profiles page.

Missing contacts remain `null`. Do not infer, synthesize, or copy private/personal contact information.

## Repository layout

```text
All_Prof_Info.md                 # generated English index
All_Prof_Info.zh-CN.md           # Codex translation of the frozen English index
data/
  professors.json               # normalized official roster
  research-analysis.json        # reviewed multi-source research analysis
  publications.json             # reviewed and deduplicated candidates
  translations.zh-CN.json      # reviewed exact-key Chinese translation memory
  manifest.json                 # bilingual path allowlist and counts
  search-index.json             # bounded public website search data
professors/<unit>/
  <slug>.md                      # generated English professor document
  <slug>.zh-CN.md                # Codex Chinese translation
reports/
  <cutoff>-roster-diff.json
  <cutoff>-conflicts.json
scripts/
  sync_professor_information.py
  initialize_research_analysis.py
  prepare_publication_work_queue.py
  retrieve_research_sources.py
  build_publication_candidates.py
  generate_professor_markdown.py
  prepare_chinese_translation.py
  translate_professor_markdown.py
  validate_professor_information.py
tests/
  COVERAGE.md
  test_professor_information.py
```

[`HKUSTGZ_microelectronics_2025_2026_publications.md`](HKUSTGZ_microelectronics_2025_2026_publications.md) is retained as historical reference evidence. It is not the generated all-Hub source of truth.

## Source and publication rules

Faculty Profiles is used only for basic fields: current roster identity, names, public work contact, website, official profile link, source-provided `permaLink`, Google Scholar ID, and current public GZ affiliations. The Scholar ID is retained only when the official profile publishes it; it is not used as a publication-collection source. The workflow does not read official-profile research interests, research descriptions, summaries, or publication lists. The synchronization script rejects empty data, duplicate IDs, changing pagination totals, repeated pages, and a candidate roster retaining fewer than 95% of baseline IDs. Every removed baseline ID remains visible in the dated roster-diff report.

OpenAlex resolves the professor identity and supplies the primary publication list. `paperscraper` is the only integration layer used by this repository for arXiv, PubMed, bioRxiv, medRxiv, ChemRxiv, and Semantic Scholar collection. Google Scholar is intentionally excluded from the research-source plan; any profile-published Scholar ID remains a basic field only and is not used to collect publications. Repository code must not directly request Semantic Scholar endpoints. `paperscraper` remains a client library rather than a proxy: its Semantic Scholar adapter still depends on the upstream service and may encounter access limits.

An independent arXiv query verifies matching records but must not create a publication candidate by itself. Every derived research interest, research area, keyword, explanatory summary, and publication entry must be matched against the professor's OpenAlex-disambiguated identity. Do not bypass rate limits, robots, CAPTCHAs, or access controls. A failed request is skipped during the complete first pass. After that pass, only failed targets are retried; three consecutive targeted failures become `failed-skipped`. A source-wide access denial or rate limit stops that source immediately. The professor analysis is published only as `blocked` or `incomplete` with a reason when evidence is limited.

Candidate publication evidence must include an approved OpenAlex, arXiv, PubMed, bioRxiv, medRxiv, ChemRxiv, or Semantic Scholar URL. Use the inclusive effective date window. Cross-source deduplication matches DOI first, then arXiv ID, OpenAlex ID, Semantic Scholar ID, PubMed ID, and finally normalized title with a compatible year. Prefer a formal conference/journal version over a matching preprint, while retaining every source ID and evidence URL. Conflicting dates and ambiguous authors are quarantined for Owner review. No result from `paperscraper` or the independent arXiv verification pass is attributed automatically unless it matches the professor's disambiguated OpenAlex record.

Do not copy source abstracts. Research summaries and classifications must be original analysis supported by listed approved research evidence. Official Faculty Profiles content is not evidence for these derived fields.

## English-first bilingual workflow

English is the generation source of truth. The Chinese files are translations, not an independently generated dataset.

1. Generate the complete English index and every English professor file.
2. Validate normalized data, counts, identities, links, date window, and deterministic output.
3. Freeze the English output for the run.
4. Use Codex to review and populate `data/translations.zh-CN.json`, then generate matching `.zh-CN.md` files through the fail-closed translation command.
5. In Chinese publication entries, retain the complete original English title and add the Chinese translation; never replace the bibliographic title.
   Venue names and per-publication source keywords remain in their published language so bibliographic names and exact search terms are not distorted.
6. Validate one Chinese counterpart per English document and identical professor/publication identities, external links, counts, and heading structure.

Translation must not mutate `professors.json`, `research-analysis.json`, `publications.json`, the search index, or English Markdown.
The translation memory uses exact English presentation strings as keys. Missing or empty translations abort before any Chinese document is written, so a partial memory cannot publish a half-translated tree.

## Update commands

Use a clean clone and a dated branch:

```bash
git switch main
git pull --ff-only
git switch -c codex/prof-info-2026-09-02
```

Synchronize the official roster:

```bash
python3 scripts/sync_professor_information.py \
  --cutoff 2026-09-01
```

Create the deterministic Codex retrieval queue:

```bash
python3 scripts/initialize_research_analysis.py \
  --cutoff 2026-09-01

python3 scripts/prepare_publication_work_queue.py \
  --start 2024-09-01 \
  --cutoff 2026-09-01
```

The initializer refuses to overwrite an existing analysis file, so resuming a run cannot silently erase completed work.

Install the pinned collection dependency, then let Codex work through the queue. The OpenAlex API key is read only from the protected `OPENALEX_API_KEY` environment variable. Semantic Scholar credentials, when available, are supplied only through `paperscraper`'s supported environment variable. Credentials are optional for tests and must never be written to the repository. Retrieval writes resumable evidence; candidate construction matches `paperscraper` results to OpenAlex, applies the independent arXiv verification pass, and deduplicates all approved sources:

```bash
python3 -m pip install -r requirements.txt

python3 scripts/retrieve_research_sources.py \
  --start 2024-09-01 \
  --cutoff 2026-09-01

python3 scripts/build_publication_candidates.py \
  --cutoff 2026-09-01
```

The retriever uses ignored raw checkpoints, bounded request timeouts, and progress-only standard output. A request failure is skipped for the full first pass and then receives at most three targeted retries. A source-wide access or rate-limit response blocks that source without further requests. Codex reviews the evidence and conflict reports before writing research analysis or generating documents.

Use `--sources` to resume selected approved sources. After three recorded bounded probes establish that an entire source is unavailable for the run, pass it through `--blocked-sources`; this records the limitation for every professor without repeating hundreds of known-failing requests. The adapter remains enabled for later scheduled updates.

```bash
python3 scripts/generate_professor_markdown.py \
  --start 2024-09-01 \
  --cutoff 2026-09-01 \
  --generated-at 2026-09-02T00:00:00+08:00

python3 scripts/prepare_chinese_translation.py \
  --start 2024-09-01 \
  --cutoff 2026-09-01

python3 scripts/translate_professor_markdown.py \
  --start 2024-09-01 \
  --cutoff 2026-09-01
```

The preparation command writes only missing exact-key strings to the dated translation-input report. After Codex has reviewed and merged those translations into the memory, generate the Chinese Markdown and validate:

```bash
python3 -m unittest tests/test_professor_information.py
python3 scripts/validate_professor_information.py --cutoff 2026-09-01
```

Run the generator a second time with the same arguments and require a zero diff in English generated outputs. Review the exact candidate diff, unresolved limitations, generated counts, and bilingual parity before any commit or push. The repository's clean-before-commit gate applies at that boundary.

## Publication input shape

`data/research-analysis.json` is a JSON list kept separate from official basics. A reviewed record has this minimum shape:

```json
{
  "officialProfileId": "20",
  "researchInterests": ["Data management"],
  "researchAreas": ["Database systems"],
  "keywords": ["databases", "knowledge graphs"],
  "summaryEn": "Original evidence-based explanation.",
  "status": "complete",
  "notes": [],
  "evidenceUrls": ["https://openalex.org/A...", "https://arxiv.org/abs/..."],
  "lastVerifiedOn": "2026-09-01"
}
```

Every evidence URL in this file must use an approved research-source domain. A `complete` record requires evidence; an `incomplete` or `blocked` record requires a limitation note.

`data/publications.json` is a JSON list. A reviewed record has this minimum shape:

```json
{
  "officialProfileId": "20",
  "title": "Complete original English title",
  "effectiveDate": "2025-08-01",
  "publicationType": "conference",
  "venue": "Example Conference",
  "doi": "10.x/example-or-null",
  "arxivId": "2501.00001-or-null",
  "keywords": ["keyword"],
  "evidenceUrls": [
    "https://openalex.org/W...",
    "https://arxiv.org/abs/2501.00001"
  ]
}
```

Do not add placeholders to inflate counts. An empty list is valid only when every professor is explicitly marked `blocked`, `incomplete`, or `complete` with dated evidence notes; `not-started` is not publishable.

## Website contract

The website reads only the fixed public repository's `main` branch. It validates `data/manifest.json` and `data/search-index.json`, then fetches only allowlisted English or Chinese Markdown paths. It treats all Markdown as untrusted, rejects raw HTML execution, scripts, iframes, images, dangerous URLs, redirects, invalid UTF-8, oversized content, and arbitrary paths, and does not serve expired cache content after revalidation failure.

## Review and release

The Codex task may prepare a dated branch, commit, push, and pull request only when separately authorized. The Owner reviews and merges. Branch protection, required review, and protected deployment environments remain in force. Tagging and website deployment are separate post-merge actions and require separate authorization.
