# Three-level Professor Data Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a schema-version-2 professor release with a lightweight directory, English-first profile and publications documents, evidence-grounded research sub-directions, three English sentences per sub-direction, publication attribution notes, reviewed Chinese translations, and a complete Codex self-review report.

**Architecture:** Keep normalized professor and publication records as canonical inputs. Add a separate structured sub-direction artifact that connects each English explanation and publication assignment to approved evidence, then generate four manifest-selected Markdown files per professor. A deterministic validator blocks malformed, unsupported, duplicated, untranslated, or Google-Scholar-derived release content.

**Tech Stack:** Python 3.12 standard library, JSON, Markdown, `unittest`, GitHub.

**Spec:** `docs/superpowers/specs/2026-09-02-three-level-professor-information-design.md`

## Global Constraints

- English structured content and Markdown are generated and frozen before Chinese translation begins.
- Every generated research sub-direction contains exactly three separately stored English sentences.
- Every publication maps only to sub-directions owned by the same professor, or carries an explicit unassigned result.
- Google Scholar is not a publication or research-claim evidence source.
- User-by-user manual approval is not required; zero unresolved `block` review results are required.
- `data/search-index.json` remains available for offline analysis, while `data/directory-index.json` is the only website directory index.
- March 1 and September 1 remain the supported release cutoffs.

## Test Coverage Model

| Requirement or risk | Evidence | Partitions and failures | Test level and oracle | Priority |
| --- | --- | --- | --- | --- |
| Sub-direction grounding | Approved spec and source schema | supported, limited, blocked, missing evidence, Google Scholar evidence | Unit validator accepts only supported/explicitly limited records | P0 |
| Three-sentence contract | Approved spec | 0, 2, 3, 4 sentences; empty or duplicate sentences | Unit generator/validator requires exactly three non-empty sentences | P0 |
| Referential integrity | Professor and publication IDs | owned, unknown, cross-professor, duplicate, unassigned | Unit validator resolves every reference within one professor | P0 |
| English-first translation | Approved workflow | clean freeze, changed structure, changed file, missing translation, reordered Chinese | Unit and integration parity checks fail closed | P0 |
| Manifest and indexes | Website contract | missing path, unsafe path, count/cutoff mismatch, oversized artifact | Unit plus release validator rejects publication | P0 |
| Deterministic release | Existing generator contract | reordered inputs, repeated generation, March/September boundary | Metamorphic tests compare byte-identical output | P1 |
| Self-review completeness | User requirement | missing professor, duplicate result, limited, block | Release validator requires one result per professor and zero blockers | P0 |

The suite covers declared P0 data-contract behavior with deterministic synthetic fixtures plus full-release validation. Live upstream retrieval quality and the truth of external source content remain outside unit-test control; the release review report and cited-source inspection are their acceptance evidence.

---

### Task 1: Define and validate the sub-direction artifact

**Files:**
- Modify: `scripts/professor_information.py`
- Modify: `tests/test_professor_information.py`

**Interfaces:**
- Consumes: normalized `data/professors.json`, `data/publications.json`, and `data/research-analysis.json`.
- Produces: `validate_research_subdirections(payload, professor_ids, publication_owners)` and a JSON artifact containing professor-scoped `subdirections` and `publicationAssignments`.

- [ ] **Step 1: Write failing validation tests**

Add tests proving that a valid record passes and that the validator rejects two sentences, cross-professor publication IDs, unknown sub-direction IDs, duplicate normalized names, Google Scholar evidence URLs, and unresolved `block` results.

```python
payload = {
    "schemaVersion": 1,
    "cutoff": "2026-09-01",
    "professors": [{
        "officialProfileId": "20",
        "reviewStatus": "pass",
        "subdirections": [{
            "id": "database-systems",
            "nameEn": "Database systems",
            "explanationEn": ["Sentence one.", "Sentence two.", "Sentence three."],
            "evidencePublicationIds": ["doi:10.1/example"],
            "evidenceUrls": ["https://openalex.org/W1"],
            "reviewStatus": "pass"
        }],
        "publicationAssignments": [{
            "publicationId": "doi:10.1/example",
            "subdirectionIds": ["database-systems"],
            "unassignedReasonEn": None
        }]
    }]
}
validate_research_subdirections(payload, {"20"}, {"doi:10.1/example": "20"})
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `python3 -m unittest tests.test_professor_information.ProfessorInformationTests.test_research_subdirection_contract`

Expected: FAIL because `validate_research_subdirections` is not defined.

- [ ] **Step 3: Implement the minimal validator**

Validate exact schema version, cutoff, unique professor IDs, stable lowercase-hyphen IDs, exactly three non-empty sentences, evidence URL hosts, publication ownership, direction references, assignment exclusivity, and review statuses. Reject any URL whose hostname is `scholar.google.com`.

- [ ] **Step 4: Run the focused and complete suites**

Run: `python3 -m unittest tests.test_professor_information.ProfessorInformationTests.test_research_subdirection_contract`

Expected: PASS.

Run: `python3 -m unittest discover -s tests -p 'test_*.py'`

Expected: all tests pass.

- [ ] **Step 5: Commit the contract**

Run the exact staged-change audit, then commit `scripts/professor_information.py` and `tests/test_professor_information.py` with message `feat: validate professor research subdirections`.

### Task 2: Prepare, generate, and self-review English sub-directions

**Files:**
- Create: `scripts/prepare_subdirection_work_queue.py`
- Create: `scripts/validate_subdirection_review.py`
- Create (ignored intermediate): `reports/2026-09-01-subdirection-work-queue.json`
- Create: `reports/2026-09-01-subdirection-self-review.json`
- Create: `data/research-subdirections.json`
- Modify: `.gitignore`
- Modify: `tests/test_professor_information.py`

**Interfaces:**
- Consumes: canonical professor, research-analysis, and deduplicated publication JSON.
- Produces: an ignored work queue, the tracked English `data/research-subdirections.json`, and a tracked release review report with one result per professor.

- [ ] **Step 1: Write failing work-queue and review tests**

Assert that the queue contains every professor exactly once, includes only publications owned by that professor, carries approved evidence URLs, excludes Google Scholar evidence, and remains deterministic when inputs are reordered. Assert that the review report must contain one `pass`, `limited`, or `block` result per professor and that any `block` fails validation.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `python3 -m unittest tests.test_professor_information.ProfessorInformationTests.test_subdirection_work_queue tests.test_professor_information.ProfessorInformationTests.test_subdirection_review_report`

Expected: FAIL because the queue and review functions are absent.

- [ ] **Step 3: Implement deterministic preparation and review validation**

The queue exposes names, official research fields, existing English analysis, owned publication titles, dates, keywords, venues, publication IDs, and approved evidence URLs. The review validator checks complete professor coverage, structural results, evidence resolution, and zero blockers. Intermediate prompt and shard files remain ignored; only final data and the compact review report are tracked.

- [ ] **Step 4: Generate English content for every professor**

For each professor, derive a small non-duplicative set of sub-directions from official research information and retrieved publications. Write exactly three English sentences per direction: scope, supporting evidence, and bounded interpretation or limitation. Assign each publication only where evidence supports the relation; otherwise store an English unassigned reason. Do not generate Chinese in this step.

- [ ] **Step 5: Perform the complete Codex self-review**

Review every professor record against its cited source data. Record `pass` for supported content, `limited` when the text explicitly states the evidence limitation, and `block` for an unsupported or conflicting record. Correct candidates and repeat validation until the report contains all professors and zero blockers; do not bypass a blocker.

- [ ] **Step 6: Verify generated English data**

Run: `python3 scripts/validate_subdirection_review.py --root . --cutoff 2026-09-01`

Expected: prints `397/397` and exits zero.

- [ ] **Step 7: Commit reviewed English structured data**

Run the exact staged-change audit, confirm ignored work files are excluded, and commit the two scripts, tests, `.gitignore`, `data/research-subdirections.json`, and the compact self-review report with message `feat: add reviewed professor subdirections`.

### Task 3: Generate the lightweight directory and four document paths

**Files:**
- Modify: `scripts/professor_information.py`
- Modify: `scripts/validate_professor_information.py`
- Modify: `tests/test_professor_information.py`
- Modify: `data/manifest.json`
- Create: `data/directory-index.json`

**Interfaces:**
- Consumes: the validated sub-direction artifact from Task 2.
- Produces: schema-version-2 manifest entries with `profile.en`, `profile.zhCN`, `publications.en`, and `publications.zhCN`; a lightweight directory index that is schema-compatible with the website.

- [ ] **Step 1: Write failing schema-version-2 generation tests**

Assert that `directory-index.json` contains identity, contact, affiliation, research fields, sub-direction names, publication count, and verification date but no publication titles, publication keywords, or venues. Assert that every manifest record exposes exactly four safe Markdown paths.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python3 -m unittest tests.test_professor_information.ProfessorInformationTests.test_generation_writes_lightweight_directory_index_without_publication_details tests.test_professor_information.ProfessorInformationTests.test_manifest_contains_four_level_documents`

Expected: FAIL because the current manifest has two paths and no released directory index.

- [ ] **Step 3: Implement schema-version-2 generation**

Build the directory index directly from normalized professor and reviewed sub-direction data rather than trimming the full index in the website. Generate deterministic four-path manifest entries and retain the full offline search index.

- [ ] **Step 4: Extend release validation**

Require matching cutoff, professor count, identity set, and research fields across the manifest, directory index, full index, normalized records, and sub-direction artifact. Validate safe four-path manifest structure and the artifacts produced at this stage; Task 4 adds the final four-document existence and bilingual checks.

- [ ] **Step 5: Run the complete data suite**

Run: `python3 -m unittest discover -s tests -p 'test_*.py'`

Expected: all tests pass.

- [ ] **Step 6: Commit the schema and generator**

Run the exact staged-change audit and commit generator, validator, tests, manifest, and directory index with message `feat: publish lightweight professor directory`.

### Task 4: Split English Markdown and translate from the frozen source

**Files:**
- Modify: `scripts/professor_information.py`
- Modify: `scripts/prepare_chinese_translation.py`
- Modify: `scripts/translate_professor_markdown.py`
- Modify: `tests/test_professor_information.py`
- Modify: `data/translations.zh-CN.json`
- Modify/Create: `professors/**/*.md`
- Modify: `All_Prof_Info.md`
- Modify: `All_Prof_Info.zh-CN.md`

**Interfaces:**
- Consumes: reviewed English sub-directions, publication assignments, and frozen English digests.
- Produces: separate profile and publications Markdown in English, followed by structurally identical Chinese documents.

- [ ] **Step 1: Write failing Markdown split and parity tests**

Assert that profile Markdown contains basic information, fields, keywords, every sub-direction, and exactly three sentences but no publication list. Assert that publications Markdown contains every owned publication and a generated sub-direction note or explicit unassigned note immediately after each publication. Assert four-way manifest existence and bilingual structural parity.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python3 -m unittest tests.test_professor_information.ProfessorInformationTests.test_profile_and_publication_markdown_are_split tests.test_professor_information.ProfessorInformationTests.test_publication_notes_reference_reviewed_subdirections`

Expected: FAIL because the current generator combines profile and publications.

- [ ] **Step 3: Implement English document generation**

Generate the profile document first. Generate a separate `.publications.md` document with the complete release-window publication list. After each publication block, render an emphasized Markdown note such as `*Generated research sub-direction: …*` or `*No reliable generated sub-direction assignment.*` using only validated assignment data. The website maps this safe Markdown form to subdued small text; raw HTML remains disabled.

- [ ] **Step 4: Freeze English structure and extend translation inputs**

Include sub-direction names, all three sentences, and unassigned reasons in the translation inventory. Extend the English file digest map to cover both English documents per professor. Refuse translation when structured inputs or English files change after freezing.

- [ ] **Step 5: Translate Chinese from English and generate Chinese documents**

Translate only presentation text. Preserve IDs, publication titles, bibliographic venue names, source keywords, and URLs as required by the existing identity contract. Generate `.zh-CN.md` and `.publications.zh-CN.md` in the same order as English.

- [ ] **Step 6: Run parity and full validation**

Run: `python3 scripts/validate_professor_information.py --root . --cutoff 2026-09-01`

Expected: prints `397/397` and exits zero.

Run: `python3 -m unittest discover -s tests -p 'test_*.py'`

Expected: all tests pass.

- [ ] **Step 7: Commit bilingual Markdown**

Run the exact staged-change audit, classify generated release artifacts by their documented consumers, and commit reviewed bilingual data and documents with message `feat: publish three-level professor documents`.

### Task 5: Document and publish the data workflow

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `tests/COVERAGE.md`

**Interfaces:**
- Consumes: the final commands, paths, schema, and failure behavior from Tasks 1–4.
- Produces: aligned maintainer documentation for twice-yearly English-first generation, Codex review, translation, validation, and publication.

- [ ] **Step 1: Update English maintainer documentation**

Document exact prerequisites, command order, tracked and ignored artifacts, schema version 2, the three public levels, review statuses, failure conditions, and the March/September release process.

- [ ] **Step 2: Translate and align the Chinese documentation**

Create a complete Chinese counterpart with the same headings, commands, constraints, and behavior. Do not reduce it to a summary.

- [ ] **Step 3: Verify documentation and final release**

Run: `python3 scripts/validate_subdirection_review.py --root . --cutoff 2026-09-01`

Run: `python3 scripts/validate_professor_information.py --root . --cutoff 2026-09-01`

Run: `python3 -m unittest discover -s tests -p 'test_*.py'`

Expected: both validators print `397/397`; all tests pass.

- [ ] **Step 4: Commit, push, and read back GitHub main**

Run the exact staged-change audit, commit aligned documentation with message `docs: document three-level professor workflow`, push the reviewed branch, merge it into `main`, and verify that GitHub `main` serves manifest schema version 2 and `data/directory-index.json` before changing the website.
