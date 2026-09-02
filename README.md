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
  research-subdirections.json  # independently reviewed English sub-directions and publication assignments
  translations.zh-CN.json      # reviewed exact-key Chinese translation memory
  manifest.json                 # schema v2 bilingual profile/publication allowlist, counts, and English freeze digests
  directory-index.json          # lightweight public directory records
  search-index.json             # bounded public website search data
professors/<unit>/
  <slug>.md                      # generated English professor profile
  <slug>.zh-CN.md                # reviewed Chinese professor profile
  <slug>.publications.md         # generated English publication details
  <slug>.publications.zh-CN.md   # reviewed Chinese publication details
reports/
  <cutoff>-roster-diff.json
  <cutoff>-conflicts.json
  <cutoff>-subdirection-self-review.json
scripts/
  sync_professor_information.py
  initialize_research_analysis.py
  prepare_publication_work_queue.py
  retrieve_research_sources.py
  build_publication_candidates.py
  prepare_subdirection_work_queue.py
  validate_subdirection_review.py
  generate_professor_markdown.py
  prepare_chinese_translation.py
  translate_professor_markdown.py
  validate_professor_information.py
tests/
  COVERAGE.md
  test_professor_information.py
```

The public data has three read levels:

1. `data/directory-index.json` supplies lightweight directory and filter records without publication titles or details.
2. `professors/<unit>/<slug>.md` and its `.zh-CN.md` counterpart supply the professor profile and reviewed research sub-directions.
3. `professors/<unit>/<slug>.publications.md` and its `.publications.zh-CN.md` counterpart supply publication details.

`data/manifest.json` is schema version 2. For every professor slug, `documents.<slug>.profile.{en,zhCN}` and `documents.<slug>.publications.{en,zhCN}` are the only public Markdown paths. `data/search-index.json`, `data/directory-index.json`, and `data/research-subdirections.json` remain schema version 1.

[`HKUSTGZ_microelectronics_2025_2026_publications.md`](HKUSTGZ_microelectronics_2025_2026_publications.md) is retained as historical reference evidence. It is not the generated all-Hub source of truth.

## Source and publication rules

Faculty Profiles is used only for basic fields: current roster identity, names, public work contact, website, official profile link, source-provided `permaLink`, Google Scholar ID, and current public GZ affiliations. The Scholar ID is retained only when the official profile publishes it; it is not used as a publication-collection source. The workflow does not read official-profile research interests, research descriptions, summaries, or publication lists. The synchronization script rejects empty data, duplicate IDs, changing pagination totals, repeated pages, and a candidate roster retaining fewer than 95% of baseline IDs. Every removed baseline ID remains visible in the dated roster-diff report.

OpenAlex resolves the professor identity and supplies the primary publication list. `paperscraper` is the only integration layer used by this repository for arXiv, PubMed, bioRxiv, medRxiv, ChemRxiv, and Semantic Scholar collection. Google Scholar is intentionally excluded from the research-source plan; any profile-published Scholar ID remains a basic field only and is not used to collect publications. Repository code must not directly request Semantic Scholar endpoints. `paperscraper` remains a client library rather than a proxy: its Semantic Scholar adapter still depends on the upstream service and may encounter access limits.

An independent arXiv query verifies matching records but must not create a publication candidate by itself. Every derived research interest, research area, keyword, explanatory summary, and publication entry must be matched against the professor's OpenAlex-disambiguated identity. Do not bypass rate limits, robots, CAPTCHAs, or access controls. A failed request is skipped during the complete first pass. After that pass, only failed targets are retried; three consecutive targeted failures become `failed-skipped`. A source-wide access denial or rate limit stops that source immediately. The professor analysis is published only as `blocked` or `incomplete` with a reason when evidence is limited.

Candidate publication evidence must include an approved OpenAlex, arXiv, PubMed, bioRxiv, medRxiv, ChemRxiv, or Semantic Scholar URL. Use the inclusive effective date window. Cross-source deduplication matches DOI first, then arXiv ID, OpenAlex ID, Semantic Scholar ID, PubMed ID, and finally normalized title with a compatible year. Prefer a formal conference/journal version over a matching preprint, while retaining every source ID and evidence URL. Conflicting dates and ambiguous authors are quarantined for Owner review. No result from `paperscraper` or the independent arXiv verification pass is attributed automatically unless it matches the professor's disambiguated OpenAlex record.

Do not copy source abstracts. Research summaries and classifications must be original analysis supported by listed approved research evidence. Official Faculty Profiles content is not evidence for these derived fields.

## English-first bilingual workflow

English is the generation source of truth. The Chinese files are translations, not an independently generated dataset.

1. Build the English sub-direction queue and candidates from canonical professor, analysis, and publication data.
2. Have Codex review the English evidence, then run the independent review command. A review may publish a conservative `limited` result, but never an unresolved `block`.
3. Generate the complete English overview, profile files, publication files, directory index, search index, and manifest.
4. Validate normalized data, counts, identities, links, date window, sub-direction review, and deterministic output; then freeze the English output recorded by the manifest digests.
5. Use Codex to review and populate `data/translations.zh-CN.json`, then generate matching Chinese overview, profile, and publication files through the fail-closed translation command.
6. In Chinese publication entries, retain the complete original English title and add the Chinese translation; never replace the bibliographic title.
   Venue names and per-publication source keywords remain in their published language so bibliographic names and exact search terms are not distorted.
7. Run both release validators and the full unit suite. Require one Chinese counterpart per English document and identical professor/publication identities, external links, counts, order, heading levels, and heading structure.

Translation must not mutate `professors.json`, `research-analysis.json`, `publications.json`, the search index, or English Markdown.
The translation memory uses exact English presentation strings as keys. Missing or empty translations abort before any Chinese document is written, so a partial memory cannot publish a half-translated tree.

Sub-direction review statuses are deliberately conservative. `pass` means every accepted sub-direction for that professor passed review. `limited` means at least one sub-direction has an explicit evidence limitation; the professor aggregate must then also be `limited`. `block` is unresolved and non-publishable. A direction backed only by the official profile must be `limited`, with the limitation repeated as the third explanation sentence. Publications may remain unassigned only with an explicit English reason.

## Prerequisites and artifact policy

- Run from a clean clone with Git, Python 3, and pip available.
- Install the pinned Python dependency set from `requirements.txt` before live retrieval. Unit tests and validators do not require service credentials.
- Supply optional service credentials only through supported environment variables; never store them in the repository.
- Use a March 1 or September 1 cutoff. The inclusive start date is exactly two calendar years earlier.

Canonical inputs, reviewed outputs, public indexes, bilingual Markdown, `data/manifest.json`, the dated roster/conflict reports, and `reports/<cutoff>-subdirection-self-review.json` are tracked release artifacts. Resumable retrieval evidence, publication/translation/sub-direction work queues, unreviewed sub-direction candidates and prompts, translation shards, sub-direction shards, `.sync-cache/`, `.venv/`, Python bytecode, and internal agent logs/history/evaluation artifacts are working material and must remain ignored or outside the repository. Do not promote an ignored queue, prompt, shard, or internal evaluation artifact into release documentation.

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

Prepare and independently review the English sub-directions before generating public documents:

```bash
python3 scripts/prepare_subdirection_work_queue.py \
  --root . \
  --cutoff 2026-09-01

python3 scripts/validate_subdirection_review.py \
  --root . \
  --cutoff 2026-09-01 \
  --write-reviewed

python3 scripts/validate_subdirection_review.py \
  --root . \
  --cutoff 2026-09-01
```

The preparation command writes ignored work-queue and unreviewed-candidate files under `reports/` and prints `397/397` for this release. `--write-reviewed` refuses stale prepared inputs, recomputes the independent review, rejects any `block`, and writes the tracked `data/research-subdirections.json` and dated self-review report. The validation-only command recomputes the queue and review from canonical data and requires exact equality with both tracked artifacts.

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
python3 scripts/validate_subdirection_review.py --root . --cutoff 2026-09-01
python3 scripts/validate_professor_information.py --root . --cutoff 2026-09-01
python3 -m unittest discover -s tests -p 'test_*.py'
```

For the current release, each validator must print `397/397`, and the full discovered suite must pass. Any non-zero command, count mismatch, schema/cutoff/window drift, unsafe or missing manifest path, stale review artifact, `block` status, non-publishable retrieval status, conflict, frozen-English digest mismatch, missing translation, or bilingual structural/identity/link/order mismatch stops publication.

Run the generator a second time with the same arguments and require a zero diff in English generated outputs. Review the exact candidate diff, unresolved limitations, generated counts, and bilingual parity before any commit or push. The repository's clean-before-commit gate applies at that boundary.

## Canonical data schemas

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

`data/research-subdirections.json` is a schema version 1 object organized by professor records. Each professor has an aggregate `reviewStatus`, one or more English `subdirections`, and one assignment for every owned publication. A sub-direction contains a stable ID, English name, exactly three distinct explanation sentences, approved evidence URLs, evidence publication IDs, and `pass` or `limited`; a limited direction also carries `limitationEn`. Each publication assignment names accepted sub-direction IDs or gives an explicit `unassignedReasonEn`.

## Website contract

The website reads only the fixed public repository's `main` branch. It requires manifest schema version 2, reads `data/directory-index.json` for the first level, and fetches only the manifest-allowlisted English or Chinese profile and publication paths for the next two levels. It treats all Markdown as untrusted, rejects raw HTML execution, scripts, iframes, images, dangerous URLs, redirects, invalid UTF-8, oversized content, and arbitrary paths, and does not serve expired cache content after revalidation failure.

## Review and release

Run the workflow every March 1 and September 1 with the cutoff itself as the window end and the same calendar date two years earlier as the inclusive start. After both validators and the full suite pass, review and stage only intended tracked artifacts. The Codex task may prepare a dated branch, commit, push, and pull request only when separately authorized. The Owner reviews and merges. Read back GitHub `main` and confirm manifest schema version 2 plus `data/directory-index.json` before changing the website. Tagging and website deployment are separate post-merge actions and require separate authorization.
