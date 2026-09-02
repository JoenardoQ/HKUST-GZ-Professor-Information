# Three-level professor information data design

Date: 2026-09-02

## Goal and scope

This repository is the reviewed source of truth for the public three-level professor experience. It must publish a lightweight directory, one profile document per professor, and one publications document per professor. Research sub-directions and their explanations are generated only after publication retrieval and deduplication. English is generated and frozen first; Chinese is translated from that frozen English structure.

The publication workflow must not use Google Scholar as a publication source. An official profile's Scholar identifier may remain an identity field, but it cannot contribute publication evidence or sub-direction claims.

## Public contract

The release uses manifest schema version 2 and publishes:

| Level | Artifact | Contents |
| --- | --- | --- |
| 1 | `data/directory-index.json` | Lightweight searchable identity, affiliation, contact, research fields, counts, and verification date |
| 2 | `professors/<group>/<slug>.md` and `.zh-CN.md` | Basic information, research fields, keywords, sub-directions, and exactly three explanatory sentences per sub-direction |
| 3 | `professors/<group>/<slug>.publications.md` and `.publications.zh-CN.md` | All publications retrieved in the release window, with a small sub-direction attribution note after every publication |

`data/search-index.json` remains a complete release artifact for offline analysis. The website must not consume it at request time.

Each manifest professor entry contains four allowlisted document paths:

```json
{
  "profile": { "en": "...md", "zhCN": "...zh-CN.md" },
  "publications": { "en": "...publications.md", "zhCN": "...publications.zh-CN.md" }
}
```

## Research analysis contract

After publication retrieval and deduplication, each professor receives ordered `researchSubdirections`. Every item contains:

- a stable ID scoped to the professor;
- an English name;
- exactly three English sentences stored separately, not as an unchecked paragraph;
- the publication IDs and approved evidence URLs supporting the interpretation; and
- a review status of `pass`, `limited`, or `block`.

Every publication receives zero or more `generatedSubdirectionIds`. The IDs must resolve within the same professor. If evidence cannot support a reliable assignment, the publication remains unassigned and its rendered note says that no reliable generated sub-direction assignment was made. The system must not force a false relationship.

For professors with insufficient retrieved publications, official public research information may support a sub-direction. The three sentences must distinguish the stated field, the available evidence, and the evidence limitation. Unsupported specificity is prohibited.

## English-first generation

The required order is:

1. fetch the official professor roster;
2. retrieve publications from approved sources;
3. deduplicate and quarantine conflicts;
4. associate publications with professor identities;
5. generate English fields, keywords, sub-directions, three-sentence explanations, and publication-to-direction assignments;
6. review every generated English record;
7. generate the English profile and publications Markdown plus both indexes;
8. freeze the English structured-data and file digests;
9. translate only translatable English presentation text into Chinese;
10. generate Chinese Markdown from the reviewed translation memory;
11. validate bilingual parity and publish only a release with zero blockers.

Chinese generation may not add, remove, reorder, merge, or split sub-directions, sentences, publications, identifiers, or evidence links.

## Codex self-review gate

User-by-user manual approval is not a release dependency. Codex performs a complete review and writes a machine-readable release report. Deterministic checks cover every record:

- exactly three non-empty sentences per sub-direction;
- all cited publication and sub-direction IDs resolve to the same professor;
- every claim has an approved evidence URL or an explicit limitation;
- duplicate or near-duplicate sub-directions are rejected;
- publication notes contain only known sub-directions or the unassigned notice;
- English and Chinese structure, order, IDs, URLs, and publication identity match;
- no Google Scholar publication evidence URL is present;
- all four manifest-selected documents exist and stay within published size limits.

The semantic review classifies every professor as `pass`, `limited`, or `block`. A `limited` record may publish only with its explicit evidence limitation. Any `block`, unresolved conflict, malformed translation, or missing artifact stops the release.

## Scheduled update workflow

Codex runs the workflow for the March 1 and September 1 cutoffs. Generated candidates and review reports are committed to a feature branch. After all gates pass, the reviewed artifacts are merged into GitHub `main`. The website reads only `main`, so no website redeployment is required for ordinary data updates.

## Repository documentation and tests

`README.md`, `README.zh-CN.md`, workflow documentation, schemas, generator tests, validator tests, and operator commands must describe the same schema version 2 contract. Tests cover deterministic generation, invalid evidence relationships, sentence counts, unassigned publications, bilingual parity, path allowlisting, and reproducible output.
