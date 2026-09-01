# HKUST(GZ) Microelectronics Faculty Research and Publication Update Guide

## Purpose

This repository tracks the research areas and recent publications of HKUST(GZ) Microelectronics faculty whose work is relevant to EDA, computer architecture, digital integrated circuits, hardware security, or quantum-computing architecture. The current report is [`HKUSTGZ_microelectronics_2025_2026_publications.md`](./HKUSTGZ_microelectronics_2025_2026_publications.md).

## Original Requirements

1. Search all Microelectronics faculty in HKUST(GZ) Faculty Profiles.
2. Identify faculty relevant to EDA, computer architecture, and integrated-circuit design. Robotics and quantum architecture that does not focus on devices may also be included.
3. Identify the supervisor, Yangdi Lyu, first; then search personal websites, Google Scholar, arXiv, and AIHub. List Yangdi Lyu’s results separately.
4. Exclude faculty whose primary area is general AI, but do not incorrectly exclude hardware-centered work such as AI accelerators, hardware generation, or intelligent EDA—for example, Xinyu Chen.
5. Remove candidates and work centered on analog circuits or device design.
6. For every final candidate, list all 2025/2026 publications, classify them by research direction, and provide keywords, a summary of no more than three sentences, and complete publication titles in a large table.

## Candidate Boundaries

### Include

- EDA: logic synthesis, formal verification, RTL generation and repair, PPA modeling, physical design, routing, and design-space exploration.
- Computer architecture: CPUs, GPUs, DPUs, FPGAs, NoCs, storage systems, and architecture simulation.
- Digital ICs and accelerators: AI/LLM accelerators, CIM, cryptographic computing, sparse and mixed-precision computing, and hardware/software co-design.
- Hardware security: microarchitectural side channels, sensor or electromagnetic injection, and processor or cryptographic implementation defenses.
- Quantum computing: architecture, compilation, mapping, scheduling, fault tolerance, and quantum system software; exclude work primarily about device fabrication.

### Exclude

- Research centered on analog, RF, power-management, or sensor-front-end circuit design.
- Research centered on semiconductor materials, processes, device physics, fabrication, or characterization.
- General AI work without a substantive hardware, EDA, or architecture contribution.

Classify by the paper’s primary technical contribution, not merely by the presence of words such as “AI” or “device.”

## Update Workflow

1. Record the search cutoff date and time zone at the top of the report.
2. Re-enumerate Microelectronics faculty from [HKUST(GZ) Faculty Profiles](https://facultyprofiles.hkust-gz.edu.cn/), recording names, official research descriptions, and personal websites.
3. Verify Yangdi Lyu first, then process the other final candidates.
4. For each person, check the official profile, personal or lab publication page, date-sorted Google Scholar, and arXiv author search. Use DBLP, conference proceedings, coauthor pages, and AIHub when needed.
5. Search name variants and initials, and disambiguate authors using affiliation, coauthors, and research topics.
6. Collect journal papers, conference papers, accepted papers, and arXiv-only preprints in the target years, then deduplicate and classify them.
7. Update the main tables and the count table, verifying totals per person, per year, and for the complete report.

## Year and Deduplication Rules

- Prefer the formal publication or acceptance year for conference and journal versions.
- When only an arXiv version exists, use its first-submission year and label it `arXiv`.
- Count arXiv, online-first, and formal versions of the same work once. Keep the formal title and venue, and note cross-year relationships.
- Count an extended version separately only when its title, method, or experiments form a distinguishable publication; state its relationship to the earlier work.
- Merge duplicate Scholar records, capitalization differences, and revised-title variants.
- Talks, courses, news items, project descriptions, and patents are not publications by default. If a dissertation is retained, label it explicitly.
- When personal pages, Scholar, and formal issue metadata disagree on the year, record the conflict instead of silently choosing one.

## Table Format

Use a separate table for Yangdi Lyu and one main table for the remaining faculty. Each row represents “faculty member × year × primary research direction” with these columns:

| Faculty | Year | Research direction | Keywords | Summary in at most three sentences | Deduplicated publication titles |
|---|---|---|---|---|---|

Assign every publication to exactly one primary category to avoid double counting. Use complete titles and identify the conference, journal, or `arXiv` whenever possible.

## Completeness Checks

- Compare the sets from official pages, Scholar, arXiv, and supplemental sources for every faculty member.
- Verify the 2025 total, 2026 total, per-faculty totals, and complete-report total.
- Search the report for placeholders, ellipses, truncated titles, and unresolved items.
- Check that preprints and formal versions are not counted twice and that cross-year records appear once.
- For a year with no retrieved results, state the search scope and cutoff date; do not equate a stale webpage with proof of no publications.

## Reusable Update Prompt

> As of [date], re-search all Microelectronics faculty in HKUST(GZ) Faculty Profiles. Apply the inclusion, exclusion, year, and deduplication rules in this README. Verify Yangdi Lyu first, then check each final candidate’s official profile, personal publication page, date-sorted Google Scholar, and arXiv; use DBLP, conference proceedings, coauthor pages, and AIHub when needed. Update every journal paper, conference paper, accepted paper, and arXiv-only preprint in [target years]. Merge preprint and formal versions of the same work and explicitly mark year or title conflicts. Put Yangdi Lyu in a separate table and organize the others by faculty member × year × research direction, including keywords, a summary of no more than three sentences, complete publication titles, and venues. Finally verify per-person, per-year, and grand totals without using placeholder entries.

