import contextlib
from copy import deepcopy
import io
import json
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.professor_information import (
    SourceDataError,
    build_publication_work_queue,
    build_research_analysis_skeleton,
    deduplicate_publications,
    generate_documents,
    in_window,
    normalize_faculty_rows,
    publication_candidates_from_evidence,
    record_retrieval_attempt,
    validate_research_analysis,
    validate_research_subdirections,
    validate_retrieval_statuses,
    validate_bilingual_parity,
    validate_artifact_window,
    validate_release_window,
)
from scripts.retrieve_research_sources import (
    parse_arxiv_feed,
    parse_openalex_works,
)
from scripts import retrieve_research_sources as retrieval
from scripts import professor_information as professor_logic


def faculty_row(profile_id="20", name="陈雷", en_name="Lei CHEN"):
    return {
        "id": profile_id,
        "name": name,
        "enName": en_name,
        "email": "leichen@hkust-gz.edu.cn",
        "phone": "(020) 8833 3866",
        "website": "https://example.edu/lab",
        "permaLink": "https://repository.hkust.edu.hk/ir/AuthorProfile/chen-lei",
        "rsidentifier": [{"label": "GoogleScholarID", "id": "gtglwgYAAAAJ", "url": "https://scholar.google.com/citations?user=gtglwgYAAAAJ"}],
        "jobs": [
            {"id": "1", "campus": "gz", "jobEnName": "Chair Professor", "departmentEnName": "Thrust of Artificial Intelligence", "parentEnName": "Information Hub", "sort": "1400"},
            {"id": "2", "campus": "hk", "jobEnName": "Professor", "departmentEnName": "CSE", "parentEnName": "Engineering", "sort": "100"},
        ],
    }


def research_analysis(profile_id="20", status="complete"):
    return {
        "officialProfileId": profile_id,
        "researchInterests": ["Data management"],
        "researchAreas": ["Database systems"],
        "keywords": ["databases", "knowledge graphs"],
        "summaryEn": "Recent arXiv and OpenAlex records indicate work on data-intensive systems.",
        "status": status,
        "notes": [] if status == "complete" else ["One research index was unavailable after bounded attempts."],
        "evidenceUrls": ["https://arxiv.org/abs/2501.00001"],
        "lastVerifiedOn": "2026-09-01",
    }


def reviewed_subdirections(profile_id="20", publication_ids=()):
    publication_ids = list(publication_ids)
    limited = not publication_ids
    direction = {
        "id": "evidence-limited-research-profile",
        "nameEn": "Evidence-limited research profile",
        "explanationEn": [
            "The approved release data does not support a specific research sub-direction for this professor.",
            "The official profile identifies the professor, but no approved thematic analysis or canonical publication is available for this release.",
            "This record is limited to documenting insufficient approved evidence and should not be read as a description of the professor's broader research agenda.",
        ],
        "evidencePublicationIds": publication_ids,
        "evidenceUrls": [
            f"https://facultyprofiles.hkust-gz.edu.cn/faculty-personal-page?id={profile_id}"
        ] if limited else ["https://openalex.org/W1"],
        "reviewStatus": "limited" if limited else "pass",
    }
    if limited:
        direction["limitationEn"] = direction["explanationEn"][2]
    return {
        "schemaVersion": 1,
        "cutoff": "2026-09-01",
        "professors": [{
            "officialProfileId": profile_id,
            "reviewStatus": "limited" if limited else "pass",
            "subdirections": [direction],
            "publicationAssignments": [
                {
                    "publicationId": publication_id,
                    "subdirectionIds": [direction["id"]],
                    "unassignedReasonEn": None,
                }
                for publication_id in publication_ids
            ],
        }],
    }


def synthetic_retrieval_queue():
    return {
        "schemaVersion": 1,
        "window": {"start": "2024-09-01", "end": "2026-09-01", "inclusive": True},
        "professors": [{
            "officialProfileId": "P1",
            "nameEn": "Synthetic Researcher",
            "openAlexAuthorSearchUrl": "https://api.openalex.org/authors?search=synthetic",
            "paperscraperQuery": {"authorNames": ["Synthetic Researcher"]},
            "arxivValidationUrl": "https://export.arxiv.org/api/query?search_query=synthetic",
            "status": "pending",
            "targetedFailures": 0,
            "attempts": [],
            "notes": [],
        }],
    }


class ProfessorInformationTests(unittest.TestCase):
    def test_inclusive_two_year_window(self):
        self.assertTrue(in_window("2024-09-01", "2026-09-01", "2024-09-01"))
        self.assertTrue(in_window("2024-09-01", "2026-09-01", "2026-09-01"))
        self.assertFalse(in_window("2024-09-01", "2026-09-01", "2024-08-31"))
        self.assertFalse(in_window("2024-09-01", "2026-09-01", "2026-09-02"))

    def test_release_window_must_be_exactly_two_calendar_years(self):
        validate_release_window("2024-09-01", "2026-09-01")
        validate_release_window("2024-03-01", "2026-03-01")
        for start, end in (("2025-09-01", "2026-09-01"), ("2023-09-01", "2026-09-01"), ("2026-09-01", "2024-09-01")):
            with self.assertRaisesRegex(ValueError, "exactly two calendar years"):
                validate_release_window(start, end)

    def test_artifact_window_must_match_release_window(self):
        payload = {"window": {"start": "2024-09-01", "end": "2026-09-01", "inclusive": True}}
        validate_artifact_window(payload, "2024-09-01", "2026-09-01", "evidence")
        with self.assertRaisesRegex(SourceDataError, "evidence window does not match"):
            validate_artifact_window(payload, "2024-03-01", "2026-03-01", "evidence")

    def test_official_faculty_normalization(self):
        row = faculty_row()
        row["researchDirections"] = ["MUST NOT ENTER OFFICIAL BASICS"]
        row["officialOverview"] = "MUST NOT ENTER OFFICIAL BASICS"
        professors, removed = normalize_faculty_rows([row], baseline_ids={"20"})
        self.assertEqual(removed, [])
        professor = professors[0]
        self.assertEqual(professor["officialProfileUrl"], "https://facultyprofiles.hkust-gz.edu.cn/faculty-personal-page?id=20")
        self.assertEqual(professor["permaLink"], "https://repository.hkust.edu.hk/ir/AuthorProfile/chen-lei")
        self.assertEqual(professor["email"], "leichen@hkust-gz.edu.cn")
        self.assertEqual(professor["phone"], "(020) 8833 3866")
        self.assertEqual(len(professor["affiliations"]), 1)
        self.assertEqual(professor["scholarId"], "gtglwgYAAAAJ")
        self.assertNotIn("researchDirections", professor)
        self.assertNotIn("officialOverview", professor)
        self.assertNotIn("retrievalStatus", professor)

    def test_roster_guards(self):
        with self.assertRaisesRegex(SourceDataError, "duplicate"):
            normalize_faculty_rows([faculty_row(), faculty_row()], baseline_ids={"20"})
        with self.assertRaisesRegex(SourceDataError, "95%"):
            normalize_faculty_rows([faculty_row("1")], baseline_ids={str(value) for value in range(1, 21)})

    def test_research_analysis_requires_only_approved_research_evidence(self):
        analyses = [research_analysis()]
        validate_research_analysis(analyses, {"20"})
        analyses[0]["evidenceUrls"] = ["https://facultyprofiles.hkust-gz.edu.cn/faculty-personal-page?id=20"]
        with self.assertRaisesRegex(SourceDataError, "approved research sources"):
            validate_research_analysis(analyses, {"20"})

    def test_research_subdirection_contract(self):
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
                    "reviewStatus": "pass",
                }],
                "publicationAssignments": [{
                    "publicationId": "doi:10.1/example",
                    "subdirectionIds": ["database-systems"],
                    "unassignedReasonEn": None,
                }],
            }],
        }
        professor_ids = {"20"}
        publication_owners = {"doi:10.1/example": "20"}
        validate_research_subdirections(payload, professor_ids, publication_owners)

        for sentences in (
            [],
            ["Sentence one.", "Sentence two."],
            ["Sentence one.", "Sentence two.", "Sentence three.", "Sentence four."],
            ["", "Sentence two.", "Sentence three."],
            ["Sentence one.", " SENTENCE ONE! ", "Sentence three."],
        ):
            invalid_sentences = deepcopy(payload)
            invalid_sentences["professors"][0]["subdirections"][0]["explanationEn"] = sentences
            with self.assertRaisesRegex(SourceDataError, "exactly three|duplicate"):
                validate_research_subdirections(invalid_sentences, professor_ids, publication_owners)

        official_profile_only = deepcopy(payload)
        official_profile_only["professors"][0]["subdirections"][0]["evidencePublicationIds"] = []
        official_profile_only["professors"][0]["subdirections"][0]["evidenceUrls"] = [
            "https://facultyprofiles.hkust-gz.edu.cn/faculty-personal-page?id=20"
        ]
        official_profile_only["professors"][0]["publicationAssignments"] = []
        with self.assertRaisesRegex(SourceDataError, "must be limited"):
            validate_research_subdirections(official_profile_only, professor_ids, {})

        limited = deepcopy(official_profile_only)
        limited["professors"][0]["reviewStatus"] = "limited"
        limited["professors"][0]["subdirections"][0]["reviewStatus"] = "limited"
        with self.assertRaisesRegex(SourceDataError, "limitation"):
            validate_research_subdirections(limited, professor_ids, {})

        mismatched_limitation = deepcopy(limited)
        mismatched_limitation["professors"][0]["subdirections"][0]["limitationEn"] = "Different limitation."
        with self.assertRaisesRegex(SourceDataError, "match"):
            validate_research_subdirections(mismatched_limitation, professor_ids, {})

        valid_limited = deepcopy(limited)
        valid_limited["professors"][0]["subdirections"][0]["limitationEn"] = "Sentence three."
        validate_research_subdirections(valid_limited, professor_ids, {})

        professor_limited_with_pass_direction = deepcopy(payload)
        professor_limited_with_pass_direction["professors"][0]["reviewStatus"] = "limited"
        with self.assertRaisesRegex(SourceDataError, "aggregate review status"):
            validate_research_subdirections(professor_limited_with_pass_direction, professor_ids, publication_owners)

        publication_backed_limited = deepcopy(payload)
        publication_backed_limited["professors"][0]["reviewStatus"] = "limited"
        publication_backed_limited["professors"][0]["subdirections"][0].update({
            "reviewStatus": "limited",
            "limitationEn": "Sentence three.",
        })
        validate_research_subdirections(publication_backed_limited, professor_ids, publication_owners)

        professor_pass_with_limited_direction = deepcopy(publication_backed_limited)
        professor_pass_with_limited_direction["professors"][0]["reviewStatus"] = "pass"
        with self.assertRaisesRegex(SourceDataError, "aggregate review status"):
            validate_research_subdirections(professor_pass_with_limited_direction, professor_ids, publication_owners)

        pass_with_limitation = deepcopy(payload)
        pass_with_limitation["professors"][0]["subdirections"][0]["limitationEn"] = "Sentence three."
        with self.assertRaisesRegex(SourceDataError, "pass.*limitation"):
            validate_research_subdirections(pass_with_limitation, professor_ids, publication_owners)

        missing_evidence = deepcopy(official_profile_only)
        missing_evidence["professors"][0]["subdirections"][0]["evidenceUrls"] = []
        with self.assertRaisesRegex(SourceDataError, "supporting evidence URLs"):
            validate_research_subdirections(missing_evidence, professor_ids, {})

        valid_unassigned = deepcopy(payload)
        valid_unassigned["professors"][0]["publicationAssignments"][0].update({
            "subdirectionIds": [],
            "unassignedReasonEn": "No reliable generated sub-direction assignment was made.",
        })
        validate_research_subdirections(valid_unassigned, professor_ids, publication_owners)

        unknown_publication = deepcopy(payload)
        unknown_publication["professors"][0]["publicationAssignments"][0]["publicationId"] = "doi:10.1/unknown"
        with self.assertRaisesRegex(SourceDataError, "owned"):
            validate_research_subdirections(unknown_publication, professor_ids, publication_owners)

        duplicate_assignment = deepcopy(payload)
        duplicate_assignment["professors"][0]["publicationAssignments"].append(
            deepcopy(duplicate_assignment["professors"][0]["publicationAssignments"][0])
        )
        with self.assertRaisesRegex(SourceDataError, "duplicate publication assignments"):
            validate_research_subdirections(duplicate_assignment, professor_ids, publication_owners)

        for malformed_publication_id in ({}, []):
            malformed_assignment = deepcopy(payload)
            malformed_assignment["professors"][0]["publicationAssignments"][0]["publicationId"] = malformed_publication_id
            with self.assertRaisesRegex(SourceDataError, "publication ID must be a non-empty string"):
                validate_research_subdirections(malformed_assignment, professor_ids, publication_owners)

        for schema_version in (True, False, "1"):
            malformed_schema = deepcopy(payload)
            malformed_schema["schemaVersion"] = schema_version
            with self.assertRaisesRegex(SourceDataError, "schema version"):
                validate_research_subdirections(malformed_schema, professor_ids, publication_owners)

        for field, value in (
            ("evidencePublicationIds", 7),
            ("evidencePublicationIds", {}),
            ("subdirectionIds", 7),
            ("subdirectionIds", {}),
        ):
            malformed_id = deepcopy(payload)
            if field == "evidencePublicationIds":
                malformed_id["professors"][0]["subdirections"][0][field] = [value]
            else:
                malformed_id["professors"][0]["publicationAssignments"][0][field] = [value]
            with self.assertRaisesRegex(SourceDataError, "IDs must contain strings"):
                validate_research_subdirections(malformed_id, professor_ids, publication_owners)

        cross_professor = deepcopy(payload)
        with self.assertRaisesRegex(SourceDataError, "owned"):
            validate_research_subdirections(cross_professor, professor_ids, {"doi:10.1/example": "99"})

        unknown_subdirection = deepcopy(payload)
        unknown_subdirection["professors"][0]["publicationAssignments"][0]["subdirectionIds"] = ["unknown-direction"]
        with self.assertRaisesRegex(SourceDataError, "unknown subdirection"):
            validate_research_subdirections(unknown_subdirection, professor_ids, publication_owners)

        duplicate_normalized_name = deepcopy(payload)
        duplicate_normalized_name["professors"][0]["subdirections"].append({
            **duplicate_normalized_name["professors"][0]["subdirections"][0],
            "id": "database-systems-duplicate",
            "nameEn": " DATABASE SYSTEMS ",
        })
        with self.assertRaisesRegex(SourceDataError, "duplicate"):
            validate_research_subdirections(duplicate_normalized_name, professor_ids, publication_owners)

        scholar_evidence = deepcopy(payload)
        scholar_evidence["professors"][0]["subdirections"][0]["evidenceUrls"] = ["https://scholar.google.com/citations?user=example"]
        with self.assertRaisesRegex(SourceDataError, "Google Scholar"):
            validate_research_subdirections(scholar_evidence, professor_ids, publication_owners)

        unresolved_block = deepcopy(payload)
        unresolved_block["professors"][0]["reviewStatus"] = "block"
        with self.assertRaisesRegex(SourceDataError, "blocked"):
            validate_research_subdirections(unresolved_block, professor_ids, publication_owners)

        zero_directions = deepcopy(payload)
        zero_directions["professors"][0]["subdirections"] = []
        zero_directions["professors"][0]["publicationAssignments"][0].update({
            "subdirectionIds": [],
            "unassignedReasonEn": "No reliable generated sub-direction assignment was made.",
        })
        with self.assertRaisesRegex(SourceDataError, "at least one subdirection"):
            validate_research_subdirections(
                zero_directions, professor_ids, publication_owners
            )

    def test_subdirection_work_queue(self):
        from scripts import validate_subdirection_review as subdirection_review
        from scripts.prepare_subdirection_work_queue import (
            build_subdirection_candidates,
            build_subdirection_work_queue,
        )
        from scripts.validate_subdirection_review import review_subdirection_candidates

        second_row = faculty_row("21", "王明", "Ming WANG")
        professors, _ = normalize_faculty_rows(
            [faculty_row(), second_row], baseline_ids={"20", "21"}
        )
        analyses = [
            {
                **research_analysis(),
                "researchAreas": [
                    "Database systems",
                    "Advanced Database Systems Research",
                ],
            },
            {
                **research_analysis("21"),
                "researchInterests": ["Privacy"],
                "researchAreas": ["Privacy"],
                "keywords": ["security"],
                "summaryEn": "Indexed records contain security metadata.",
                "evidenceUrls": ["https://openalex.org/A21"],
            },
        ]
        publications = [
            {
                "officialProfileId": "20",
                "publicationId": "doi:10.1/database",
                "title": "A Database Systems Study",
                "effectiveDate": "2025-08-01",
                "publicationType": "article",
                "venue": "Example Journal",
                "keywords": [],
                "evidenceUrls": ["https://openalex.org/W20"],
            },
            {
                "officialProfileId": "21",
                "publicationId": "doi:10.1/security",
                "title": "A Security Study",
                "effectiveDate": "2025-09-01",
                "publicationType": "article",
                "venue": None,
                "keywords": ["security"],
                "evidenceUrls": ["https://openalex.org/W21"],
            },
            {
                "officialProfileId": "21",
                "publicationId": "doi:10.1/database",
                "title": "A Database Systems Study",
                "effectiveDate": "2025-08-01",
                "publicationType": "article",
                "venue": "Example Journal",
                "keywords": [],
                "evidenceUrls": ["https://openalex.org/W20"],
            },
        ]
        queue = build_subdirection_work_queue(
            professors, analyses, publications, "2026-09-01"
        )
        self.assertEqual(
            [item["officialProfileId"] for item in queue["professors"]],
            [item["officialProfileId"] for item in professors],
        )
        self.assertEqual(
            [publication["publicationId"] for publication in queue["professors"][0]["publications"]],
            ["doi:10.1/database"],
        )
        self.assertEqual(
            queue["professors"][0]["researchAnalysis"]["researchInterests"],
            ["Data management"],
        )
        self.assertEqual(
            queue,
            build_subdirection_work_queue(
                list(reversed(professors)),
                list(reversed(analyses)),
                list(reversed(publications)),
                "2026-09-01",
            ),
        )
        self.assertNotIn("scholar.google.com", json.dumps(queue))

        candidates = build_subdirection_candidates(queue)
        self.assertNotIn("reviewStatus", json.dumps(candidates))
        candidate_by_id = {
            item["officialProfileId"]: item for item in candidates["professors"]
        }
        self.assertEqual(
            [item["nameEn"] for item in candidate_by_id["20"]["subdirections"]],
            [
                "Data management",
                "Database systems",
                "Advanced Database Systems Research",
            ],
        )
        self.assertNotIn("evidencePublicationIds", json.dumps(candidates))
        self.assertNotIn("publicationAssignments", json.dumps(candidates))
        artifact, report = review_subdirection_candidates(queue, candidates)
        publication_owners = {}
        for publication in publications:
            publication_owners.setdefault(publication["publicationId"], set()).add(
                publication["officialProfileId"]
            )
        validate_research_subdirections(
            artifact, {"20", "21"}, publication_owners
        )
        by_id = {
            item["officialProfileId"]: item for item in artifact["professors"]
        }
        self.assertEqual(
            [item["publicationId"] for item in by_id["20"]["publicationAssignments"]],
            ["doi:10.1/database"],
        )
        self.assertEqual(
            {item["publicationId"] for item in by_id["21"]["publicationAssignments"]},
            {"doi:10.1/database", "doi:10.1/security"},
        )
        non_owner = deepcopy(publication_owners)
        non_owner["doi:10.1/database"] = {"21"}
        with self.assertRaisesRegex(SourceDataError, "not owned"):
            validate_research_subdirections(
                artifact, {"20", "21"}, non_owner
            )
        self.assertEqual(by_id["20"]["reviewStatus"], "pass")
        self.assertEqual(
            by_id["20"]["subdirections"][0]["nameEn"], "Database systems"
        )
        self.assertNotIn(
            "Data management",
            {item["nameEn"] for item in by_id["20"]["subdirections"]},
            "the independent reviewer must reject a broad unsupported prepared candidate",
        )
        self.assertEqual(by_id["21"]["reviewStatus"], "limited")
        limited_direction = by_id["21"]["subdirections"][0]
        self.assertEqual(
            limited_direction["limitationEn"],
            limited_direction["explanationEn"][2],
        )
        self.assertEqual(
            by_id["21"]["publicationAssignments"][0]["subdirectionIds"], []
        )
        self.assertTrue(
            by_id["21"]["publicationAssignments"][0]["unassignedReasonEn"]
        )
        result_by_id = {
            item["officialProfileId"]: item for item in report["results"]
        }
        direction_review = result_by_id["20"]["directionReviews"][0]
        self.assertEqual(
            direction_review["evidenceBasis"],
            "title-or-indexed-subject support",
        )
        self.assertEqual(direction_review["evidencePublicationIds"], ["doi:10.1/database"])
        self.assertEqual(direction_review["evidenceUrls"], ["https://openalex.org/W20"])
        self.assertEqual(
            direction_review["sourceNamesEn"],
            ["Database systems", "Advanced Database Systems Research"],
        )
        self.assertTrue(direction_review["reasonEn"])

        frozen_candidates = deepcopy(candidates)
        with patch.object(
            subdirection_review, "_review_text_support", return_value=False
        ):
            rejected_artifact, _ = review_subdirection_candidates(queue, candidates)
        rejected_by_id = {
            item["officialProfileId"]: item
            for item in rejected_artifact["professors"]
        }
        self.assertEqual(candidates, frozen_candidates)
        self.assertEqual(
            [item["nameEn"] for item in rejected_by_id["20"]["subdirections"]],
            ["Conflicting indexed research evidence"],
        )

        with self.assertRaisesRegex(SourceDataError, "March 1 or September 1"):
            build_subdirection_work_queue(
                professors, analyses, publications, "2026-08-31"
            )
        march_queue = build_subdirection_work_queue(
            professors, analyses, publications, "2026-03-01"
        )
        self.assertEqual(
            march_queue["window"],
            {"start": "2024-03-01", "end": "2026-03-01", "inclusive": True},
        )
        unknown_owner = deepcopy(publications)
        unknown_owner[0]["officialProfileId"] = "99"
        with self.assertRaisesRegex(SourceDataError, "unknown professor"):
            build_subdirection_work_queue(
                professors, analyses, unknown_owner, "2026-09-01"
            )

    def test_subdirection_review_report(self):
        from scripts.prepare_subdirection_work_queue import (
            build_subdirection_candidates,
            build_subdirection_work_queue,
        )
        from scripts.validate_subdirection_review import (
            review_subdirection_candidates,
            validate_subdirection_review_report,
        )

        professors, _ = normalize_faculty_rows(
            [faculty_row(), faculty_row("21", "王明", "Ming WANG")],
            baseline_ids={"20", "21"},
        )
        analyses = [
            {**research_analysis(), "researchAreas": ["Database systems"]},
            {
                **research_analysis("21"),
                "researchInterests": ["Smart Grid Energy Management"],
                "researchAreas": ["Smart Grid Energy Management"],
                "evidenceUrls": ["https://openalex.org/W21"],
            },
        ]
        publications = [{
            "officialProfileId": "20",
            "publicationId": "doi:10.1/database",
            "title": "A Database Systems Study",
            "effectiveDate": "2025-08-01",
            "publicationType": "article",
            "venue": "Example Journal",
            "keywords": ["Database systems"],
            "evidenceUrls": ["https://openalex.org/W20"],
        }]
        queue = build_subdirection_work_queue(
            professors, analyses, publications, "2026-09-01"
        )
        candidates = build_subdirection_candidates(queue)
        artifact, report = review_subdirection_candidates(queue, candidates)
        self.assertEqual(
            report["summary"],
            {"block": 0, "limited": 1, "pass": 1, "total": 2},
        )
        validate_subdirection_review_report(
            report, artifact, queue, candidates, {"20", "21"}, publications
        )

        blocked = deepcopy(report)
        blocked["results"][0]["disposition"] = "block"
        blocked["summary"] = {"block": 1, "limited": 1, "pass": 0, "total": 2}
        with self.assertRaisesRegex(SourceDataError, "blocked"):
            validate_subdirection_review_report(
                blocked, artifact, queue, candidates, {"20", "21"}, publications
            )

        missing = deepcopy(report)
        missing["results"].pop()
        with self.assertRaisesRegex(SourceDataError, "missing professor IDs"):
            validate_subdirection_review_report(
                missing, artifact, queue, candidates, {"20", "21"}, publications
            )

        duplicate = deepcopy(report)
        duplicate["results"].append(deepcopy(duplicate["results"][0]))
        with self.assertRaisesRegex(SourceDataError, "duplicate"):
            validate_subdirection_review_report(
                duplicate, artifact, queue, candidates, {"20", "21"}, publications
            )

        circular = deepcopy(report)
        circular["results"][0]["directionReviews"][0]["reasonEn"] = "Candidate says so."
        with self.assertRaisesRegex(SourceDataError, "independent review"):
            validate_subdirection_review_report(
                circular, artifact, queue, candidates, {"20", "21"}, publications
            )

        unknown_owner = deepcopy(publications)
        unknown_owner[0]["officialProfileId"] = "99"
        with self.assertRaisesRegex(SourceDataError, "unknown professor"):
            validate_subdirection_review_report(
                report, artifact, queue, candidates, {"20", "21"}, unknown_owner
            )

        changed_source = deepcopy(publications)
        changed_source[0]["title"] = "A different canonical title"
        with self.assertRaisesRegex(SourceDataError, "canonical publication evidence"):
            validate_subdirection_review_report(
                report, artifact, queue, candidates, {"20", "21"}, changed_source
            )

        non_english = deepcopy(artifact)
        non_english["professors"][0]["subdirections"][0]["explanationEn"][0] = "数据库系统。"
        with self.assertRaisesRegex(SourceDataError, "English-only"):
            validate_subdirection_review_report(
                report, non_english, queue, candidates, {"20", "21"}, publications
            )

    def test_subdirection_matcher_requires_every_discriminative_core_concept(self):
        from scripts.validate_subdirection_review import _review_text_support

        rejected_pairs = [
            ("Spondyloarthritis Studies and Treatments", "Pancreatitis pathology and treatment"),
            ("Advanced Bandit Algorithms Research", "A generic optimization algorithm"),
            ("CO2 Reduction Techniques and Catalysts", "A carbon catalyst for oxygen reduction"),
            ("Multimodal Machine Learning Applications", "A machine learning model"),
            ("Risk and Portfolio Optimization", "Portfolio optimization methods"),
            ("Artificial Intelligence in Games", "AI forecasting for electricity demand"),
            ("3D Printing in Biomedical Research", "3D printing of concrete structures"),
        ]
        for topic, evidence in rejected_pairs:
            with self.subTest(topic=topic, evidence=evidence):
                self.assertFalse(_review_text_support(topic, evidence))

        accepted_pairs = [
            ("Spondyloarthritis Studies and Treatments", "Therapies for axial spondyloarthritis"),
            ("Advanced Bandit Algorithms Research", "Contextual bandits with delayed feedback"),
            ("CO2 Reduction Techniques and Catalysts", "Catalytic carbon dioxide electroreduction"),
            ("Multimodal Machine Learning Applications", "Multimodal learning for perception"),
            ("Risk and Portfolio Optimization", "Risk-aware portfolio optimisation"),
            ("Magnetic Properties of Alloys", "Magnetism in compositionally complex alloy systems"),
            ("BIM and Construction Integration", "BIM-based model visualization"),
            ("Advanced Bandit Algorithms Research", "Advanced bandit algorithm research"),
        ]
        for topic, evidence in accepted_pairs:
            with self.subTest(topic=topic, evidence=evidence):
                self.assertTrue(_review_text_support(topic, evidence))

    def test_subdirection_matcher_keeps_domain_defining_concepts_distinct(self):
        from scripts.validate_subdirection_review import _near_duplicate, _review_text_support

        optical_device = (
            {"nameEn": "Photonic and Optical Devices"},
            {},
            {"p-shared"},
        )
        optical_network = (
            {"nameEn": "Optical Network Technologies"},
            {},
            {"p-shared"},
        )

        self.assertFalse(_near_duplicate(optical_device, optical_network))
        self.assertFalse(_review_text_support(
            "Optical Network Technologies",
            "Transfer Learning Enhanced Blood Pressure Monitoring Based on Flexible Optical Pulse Sensing Patch",
        ))
        self.assertTrue(_review_text_support(
            "Optical Network Technologies",
            "Programmable optical networks for datacenter communications",
        ))
        self.assertTrue(_review_text_support(
            "Optical Network Technologies",
            "High-speed fiber optical communication systems",
        ))
        self.assertTrue(_review_text_support(
            "Advanced Neural Network Applications",
            "A deep neural architecture for time-series forecasting",
        ))

    def test_subdirection_duplicate_merge_revalidates_unioned_publications(self):
        from scripts.validate_subdirection_review import _merge_reviewed_direction

        publications = [
            {
                "publicationId": "p-shared",
                "title": "Perovskite materials for infrared sensing",
                "keywords": [],
                "evidenceUrls": ["https://openalex.org/W-shared"],
            },
            {
                "publicationId": "p-narrow-only",
                "title": "Magnetic transport in an alloy",
                "keywords": [],
                "evidenceUrls": ["https://openalex.org/W-narrow"],
            },
        ]
        narrow = (
            {
                "id": "magnetic-transport-perovskites",
                "nameEn": "Magnetic transport in perovskites",
                "evidencePublicationIds": ["p-narrow-only", "p-shared"],
                "evidenceUrls": ["https://openalex.org/W-narrow", "https://openalex.org/W-shared"],
            },
            {
                "subdirectionId": "magnetic-transport-perovskites",
                "sourceNamesEn": ["Magnetic transport in perovskites"],
                "evidenceBasis": "title-or-indexed-subject support",
                "evidencePublicationIds": ["p-narrow-only", "p-shared"],
                "evidenceUrls": ["https://openalex.org/W-narrow", "https://openalex.org/W-shared"],
                "sourceTitles": [item["title"] for item in publications],
                "evidenceJudgments": [],
            },
            {"p-narrow-only", "p-shared"},
        )
        broad = (
            {
                "id": "perovskite-materials",
                "nameEn": "Perovskite materials",
                "evidencePublicationIds": ["p-shared"],
                "evidenceUrls": ["https://openalex.org/W-shared"],
            },
            {
                "subdirectionId": "perovskite-materials",
                "sourceNamesEn": ["Perovskite materials"],
                "evidenceBasis": "title-or-indexed-subject support",
                "evidencePublicationIds": ["p-shared"],
                "evidenceUrls": ["https://openalex.org/W-shared"],
                "sourceTitles": [publications[0]["title"]],
                "evidenceJudgments": [],
            },
            {"p-shared"},
        )

        direction, review, evidence_ids = _merge_reviewed_direction(
            narrow, broad, publications
        )
        self.assertEqual(direction["nameEn"], "Perovskite materials")
        self.assertEqual(direction["id"], "perovskite-materials")
        self.assertEqual(evidence_ids, {"p-shared"})
        self.assertEqual(review["evidencePublicationIds"], ["p-shared"])
        self.assertEqual(
            review["sourceNamesEn"],
            ["Magnetic transport in perovskites", "Perovskite materials"],
        )

    def test_subdirection_release_regressions(self):
        from scripts.prepare_subdirection_work_queue import (
            build_subdirection_candidates,
            build_subdirection_work_queue,
        )
        from scripts.validate_subdirection_review import review_subdirection_candidates

        root = Path(__file__).parents[1]
        professors = json.loads((root / "data/professors.json").read_text(encoding="utf-8"))
        analyses = json.loads((root / "data/research-analysis.json").read_text(encoding="utf-8"))
        publications = json.loads((root / "data/publications.json").read_text(encoding="utf-8"))
        queue = build_subdirection_work_queue(
            professors, analyses, publications, "2026-09-01"
        )
        candidates = build_subdirection_candidates(queue)
        artifact, report = review_subdirection_candidates(queue, candidates)
        by_id = {item["officialProfileId"]: item for item in artifact["professors"]}
        reviews = {item["officialProfileId"]: item for item in report["results"]}

        names_626 = {item["nameEn"] for item in by_id["626"]["subdirections"]}
        self.assertIn("Multimodal Machine Learning Applications", names_626)
        self.assertIn("3D Shape Modeling and Analysis", names_626)
        self.assertNotIn("Evidence-limited research profile", names_626)
        review_3d = next(
            item for item in reviews["626"]["directionReviews"]
            if item["subdirectionId"] == "3d-shape-modeling-and-analysis"
        )
        self.assertTrue(all("3D" in title for title in review_3d["sourceTitles"]))

        names_671 = {item["nameEn"] for item in by_id["671"]["subdirections"]}
        self.assertIn("Smart Grid Energy Management", names_671)
        self.assertNotIn("Evidence-limited research profile", names_671)
        self.assertTrue(all(item["reviewStatus"] == "limited" for item in by_id["671"]["subdirections"]))
        self.assertEqual(reviews["671"]["professorBasis"], "analysis-only")
        self.assertTrue(all(
            "Electricity" in item["explanationEn"][1]
            for item in by_id["671"]["subdirections"]
        ))

        self.assertEqual(reviews["22"]["professorBasis"], "evidence-conflict")
        self.assertEqual(
            {item["nameEn"] for item in by_id["22"]["subdirections"]},
            {"Conflicting indexed research evidence"},
        )
        self.assertNotIn("matched publication", by_id["22"]["subdirections"][0]["explanationEn"][1])
        self.assertTrue(all(not item["subdirectionIds"] for item in by_id["22"]["publicationAssignments"]))
        self.assertNotIn(
            "AI in cancer detection",
            {item["nameEn"] for item in by_id["384"]["subdirections"]},
        )
        self.assertEqual(reviews["475"]["professorBasis"], "evidence-conflict")
        names_475 = {item["nameEn"] for item in by_id["475"]["subdirections"]}
        self.assertIn("Anaerobic Digestion and Biogas Production", names_475)
        self.assertIn("Nanoplatforms for cancer theranostics", names_475)
        self.assertNotIn("Distributed Sensor Networks and Detection Algorithms", names_475)
        cancer_review = next(
            item for item in reviews["475"]["directionReviews"]
            if item["subdirectionId"] == "nanoplatforms-for-cancer-theranostics"
        )
        self.assertTrue(any(
            term in " ".join(cancer_review["matchedSubjectLabels"]).casefold()
            for term in ("cancer", "tumor", "antitumor")
        ))
        self.assertTrue(all(not item["subdirectionIds"] for item in by_id["475"]["publicationAssignments"]))
        self.assertEqual(reviews["115"]["professorBasis"], "publication-record")
        self.assertGreater(reviews["115"]["assignedPublicationCount"], 0)
        self.assertTrue({
            "Sensorless Control of Electric Motors",
            "Electric Motor Design and Analysis",
            "Multilevel Inverters and Converters",
        }.issubset({item["nameEn"] for item in by_id["115"]["subdirections"]}))
        self.assertNotIn("identity remains uncertain", json.dumps(report).casefold())
        self.assertEqual(
            {item["nameEn"] for item in by_id["393"]["subdirections"]},
            {"BIM and Construction Integration"},
        )
        for profile_id in ("340", "364"):
            battery_directions = [
                item for item in by_id[profile_id]["subdirections"]
                if "battery" in item["nameEn"].casefold()
            ]
            self.assertEqual(len(battery_directions), 1)
            battery_review = next(
                item for item in reviews[profile_id]["directionReviews"]
                if item["subdirectionId"] == battery_directions[0]["id"]
            )
            self.assertTrue(all(
                any(term in title.casefold() for term in (
                    "anode", "batter", "cathode", "lithium", "sodium", "zinc", "zn"
                ))
                for title in battery_review["sourceTitles"]
            ))

        antenna_design = [
            item for item in by_id["607"]["subdirections"]
            if "antenna design" in item["nameEn"].casefold()
        ]
        self.assertEqual(len(antenna_design), 1)
        antenna_review = next(
            item for item in reviews["607"]["directionReviews"]
            if item["subdirectionId"] == antenna_design[0]["id"]
        )
        self.assertIn("Antenna Design and Analysis", antenna_review["sourceNamesEn"])
        self.assertIn("Antenna Design and Optimization", antenna_review["sourceNamesEn"])

        names_122 = {item["nameEn"] for item in by_id["122"]["subdirections"]}
        self.assertIn("Photonic and Optical Devices", names_122)
        self.assertIn("Optical Network Technologies", names_122)
        blood_pressure = next(
            item for item in by_id["122"]["publicationAssignments"]
            if item["publicationId"] == "doi:10.1021/acssensors.4c03404"
        )
        self.assertNotIn("optical-network-technologies", blood_pressure["subdirectionIds"])

        self.assertNotIn(
            "Artificial Intelligence in Games",
            {item["nameEn"] for item in by_id["55"]["subdirections"]},
        )

        healthcare = next(
            item for item in reviews["34"]["directionReviews"]
            if item["subdirectionId"] == "artificial-intelligence-in-healthcare-and-education"
        )
        for source_title in healthcare["sourceTitles"]:
            evidence_text = source_title.casefold()
            self.assertTrue(any(term in evidence_text for term in (
                "health", "clinical", "medical", "medicine", "education", "patient"
            )))
            self.assertNotIn("it terminal", evidence_text)

        biomedical_3d = next(
            item for item in reviews["372"]["directionReviews"]
            if item["subdirectionId"] == "3d-printing-in-biomedical-research"
        )
        self.assertEqual(
            biomedical_3d["evidenceBasis"],
            "title-or-indexed-subject support",
        )
        self.assertTrue(any(
            publication_id not in biomedical_3d["titleSupportedPublicationIds"]
            for publication_id in biomedical_3d["indexedSubjectSupportedPublicationIds"]
        ))

        self.assertNotIn(
            "Spondyloarthritis Studies and Treatments",
            {item["nameEn"] for item in by_id["570"]["subdirections"]},
        )
        self.assertNotIn(
            "Advanced Bandit Algorithms Research",
            {item["nameEn"] for item in by_id["375"]["subdirections"]},
        )
        self.assertNotIn(
            "CO2 Reduction Techniques and Catalysts",
            {item["nameEn"] for item in by_id["558"]["subdirections"]},
        )

        perovskite_review = next(
            item for item in reviews["11"]["directionReviews"]
            if item["subdirectionId"] == "perovskite-materials-and-applications"
        )
        self.assertEqual(
            perovskite_review["subdirectionId"],
            "perovskite-materials-and-applications",
        )
        infrared_publication_id = "doi:10.1016/j.device.2024.100661"
        self.assertIn(infrared_publication_id, perovskite_review["evidencePublicationIds"])
        self.assertFalse(any(
            item["subdirectionId"]
            == "magnetic-and-transport-properties-of-perovskites-and-related-materials"
            and infrared_publication_id in item["evidencePublicationIds"]
            for item in reviews["11"]["directionReviews"]
        ))

    def test_retrieval_attempts_skip_then_fail_after_three_targeted_failures(self):
        item = build_publication_work_queue(
            normalize_faculty_rows([faculty_row()], baseline_ids={"20"})[0],
            "2024-09-01", "2026-09-01",
        )["professors"][0]
        record_retrieval_attempt(item, source="OpenAlex", phase="initial", succeeded=False)
        self.assertEqual(item["status"], "retry-pending")
        for retry_round in range(1, 4):
            record_retrieval_attempt(item, source="OpenAlex", phase="targeted", succeeded=False)
            self.assertEqual(item["targetedFailures"], retry_round)
        self.assertEqual(item["status"], "failed-skipped")

    def test_formal_version_wins(self):
        arxiv = {"officialProfileId": "20", "title": "A Robust System", "effectiveDate": "2025-01-03", "publicationType": "arXiv", "arxivId": "2501.00001", "evidenceUrls": ["https://arxiv.org/abs/2501.00001"]}
        formal = {"officialProfileId": "20", "title": "A Robust System", "effectiveDate": "2025-08-01", "publicationType": "conference", "venue": "SIGMOD", "doi": "10.1/example", "arxivId": "2501.00001", "evidenceUrls": ["https://openalex.org/W1"]}
        merged, conflicts = deduplicate_publications([arxiv, formal])
        self.assertEqual(conflicts, [])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["publicationType"], "conference")
        self.assertEqual(merged[0]["arxivId"], "2501.00001")
        self.assertEqual(len(merged[0]["evidenceUrls"]), 2)

    def test_all_preprint_types_lose_to_formal_version(self):
        for publication_type in ("arxiv", "preprint", "biorxiv", "medrxiv", "chemrxiv"):
            preprint = {"officialProfileId": "20", "title": "A Robust System", "effectiveDate": "2026-01-03", "publicationType": publication_type, "arxivId": "2501.00001", "evidenceUrls": ["https://arxiv.org/abs/2501.00001"]}
            formal = {**preprint, "effectiveDate": "2025-08-01", "publicationType": "journal", "venue": "Journal", "doi": "10.1/example", "evidenceUrls": ["https://openalex.org/W1"]}
            merged, conflicts = deduplicate_publications([preprint, formal])
            self.assertEqual(conflicts, [])
            self.assertEqual(merged[0]["publicationType"], "journal")

    def test_conflicting_strong_identifiers_are_quarantined_before_title_fallback(self):
        first = {"officialProfileId": "20", "title": "Same Work", "effectiveDate": "2025-01-01", "publicationType": "journal", "doi": "10.1/first", "evidenceUrls": ["https://openalex.org/W1"]}
        second = {**first, "doi": "10.1/second", "evidenceUrls": ["https://openalex.org/W2"]}
        merged, conflicts = deduplicate_publications([first, second])
        self.assertEqual(merged, [])
        self.assertEqual(conflicts[0]["reason"], "conflicting-strong-identifiers")

    def test_conflicting_years_are_quarantined(self):
        first = {"officialProfileId": "20", "title": "Same Work", "effectiveDate": "2024-09-01", "publicationType": "arXiv", "arxivId": "2409.00001", "evidenceUrls": ["https://arxiv.org/abs/2409.00001"]}
        second = {**first, "effectiveDate": "2026-09-01", "publicationType": "conference", "venue": "TestConf"}
        merged, conflicts = deduplicate_publications([first, second])
        self.assertEqual(merged, [])
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["reason"], "conflicting-effective-year")

    def test_generation_is_deterministic_and_english_first(self):
        professors, _ = normalize_faculty_rows([faculty_row()], baseline_ids={"20"})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = generate_documents(root, professors, [research_analysis()], [], "2024-09-01", "2026-09-01", "2026-09-02T00:00:00Z", research_subdirections=reviewed_subdirections())
            first_bytes = (root / "All_Prof_Info.md").read_bytes()
            second = generate_documents(root, list(reversed(professors)), [research_analysis()], [], "2024-09-01", "2026-09-01", "2026-09-02T00:00:00Z", research_subdirections=reviewed_subdirections())
            self.assertEqual(first, second)
            self.assertEqual(first_bytes, (root / "All_Prof_Info.md").read_bytes())
            self.assertFalse((root / "All_Prof_Info.zh-CN.md").exists(), "English generation must finish before translation")

    def test_chinese_generation_requires_frozen_english_and_preserves_bibliographic_identity(self):
        professors, _ = normalize_faculty_rows([faculty_row()], baseline_ids={"20"})
        publications = [{
            "officialProfileId": "20",
            "publicationId": "doi:10.1/example",
            "title": "A Robust Data System",
            "effectiveDate": "2025-08-01",
            "publicationType": "conference",
            "venue": "SIGMOD",
            "doi": "10.1/example",
            "keywords": ["database"],
            "evidenceUrls": ["https://openalex.org/W1"],
        }]

        def translator(text):
            return {
                "A Robust Data System": "一个稳健的数据系统",
                "Recent arXiv and OpenAlex records indicate work on data-intensive systems.": "近期索引记录表明其研究涉及数据密集型系统。",
                "Data management": "数据管理",
                "Database systems": "数据库系统",
                "databases": "数据库",
                "knowledge graphs": "知识图谱",
            }.get(text, f"中译：{text}")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(SourceDataError, "English documents must be generated first"):
                professor_logic.generate_chinese_documents(
                    root, professors, [research_analysis()], publications,
                    "2024-09-01", "2026-09-01", translator,
                )
            generate_documents(root, professors, [research_analysis()], publications, "2024-09-01", "2026-09-01", "2026-09-02T00:00:00Z", research_subdirections=reviewed_subdirections(publication_ids=["doi:10.1/example"]))
            professor_logic.generate_chinese_documents(
                root, professors, [research_analysis()], publications,
                "2024-09-01", "2026-09-01", translator,
            )
            slug = professors[0]["slug"]
            english = (root / f"professors/information-hub/{slug}.md").read_text(encoding="utf-8")
            chinese = (root / f"professors/information-hub/{slug}.zh-CN.md").read_text(encoding="utf-8")
            self.assertIn("### A Robust Data System｜一个稳健的数据系统", chinese)
            self.assertIn("Publication identity: `doi:10.1/example`", chinese)
            self.assertIn("https://openalex.org/W1", chinese)
            self.assertIn("- 发表场所/类型：SIGMOD", chinese)
            self.assertIn("- 关键词：database", chinese)
            self.assertEqual(validate_bilingual_parity(english, chinese), [])
            self.assertTrue((root / "All_Prof_Info.zh-CN.md").is_file())

    def test_chinese_generation_rejects_structured_or_english_drift_after_freeze(self):
        professors, _ = normalize_faculty_rows([faculty_row()], baseline_ids={"20"})
        analysis = research_analysis()
        translator = lambda text: f"中译：{text}"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            generate_documents(root, professors, [analysis], [], "2024-09-01", "2026-09-01", "2026-09-02T00:00:00Z", research_subdirections=reviewed_subdirections())
            mutated = json.loads(json.dumps(analysis))
            mutated["summaryEn"] = "Changed after the English freeze."
            with self.assertRaisesRegex(SourceDataError, "frozen English inputs changed"):
                professor_logic.generate_chinese_documents(root, professors, [mutated], [], "2024-09-01", "2026-09-01", translator)
            overview = root / "All_Prof_Info.md"
            overview.write_text(overview.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")
            with self.assertRaisesRegex(SourceDataError, "frozen English file changed"):
                professor_logic.generate_chinese_documents(root, professors, [analysis], [], "2024-09-01", "2026-09-01", translator)

    def test_translation_memory_fails_closed_for_missing_or_empty_text(self):
        translator = professor_logic.build_translation_memory_translator({"Database systems": "数据库系统"})
        self.assertEqual(translator("Database systems"), "数据库系统")
        with self.assertRaisesRegex(SourceDataError, "missing reviewed Chinese translation"):
            translator("Unknown topic")
        with self.assertRaisesRegex(SourceDataError, "missing reviewed Chinese translation"):
            professor_logic.build_translation_memory_translator({"Database systems": ""})("Database systems")

    def test_translation_cli_uses_reviewed_memory_and_prints_progress_only(self):
        professors, _ = normalize_faculty_rows([faculty_row()], baseline_ids={"20"})
        memory = {
            "Chair Professor": "讲席教授",
            "Thrust of Artificial Intelligence": "人工智能学域",
            "Information Hub": "信息枢纽",
            "Recent arXiv and OpenAlex records indicate work on data-intensive systems.": "近期索引记录表明其研究涉及数据密集型系统。",
            "Data management": "数据管理",
            "Database systems": "数据库系统",
            "databases": "数据库",
            "knowledge graphs": "知识图谱",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            generate_documents(root, professors, [research_analysis()], [], "2024-09-01", "2026-09-01", "2026-09-02T00:00:00Z", research_subdirections=reviewed_subdirections())
            (root / "data/professors.json").write_text(json.dumps(professors, ensure_ascii=False), encoding="utf-8")
            (root / "data/research-analysis.json").write_text(json.dumps([research_analysis()], ensure_ascii=False), encoding="utf-8")
            (root / "data/publications.json").write_text("[]", encoding="utf-8")
            (root / "data/translations.zh-CN.json").write_text(json.dumps(memory, ensure_ascii=False), encoding="utf-8")
            result = subprocess.run([
                sys.executable,
                str(Path(__file__).parents[1] / "scripts/translate_professor_markdown.py"),
                "--root", str(root),
                "--start", "2024-09-01",
                "--cutoff", "2026-09-01",
            ], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "1/1\n")
            self.assertTrue((root / "All_Prof_Info.zh-CN.md").is_file())

    def test_translation_inputs_cover_dynamic_text_without_identifiers(self):
        professors, _ = normalize_faculty_rows([faculty_row()], baseline_ids={"20"})
        analysis = research_analysis(status="incomplete")
        publications = [{
            "officialProfileId": "20", "publicationId": "doi:10.1/example",
            "title": "A Robust Data System", "effectiveDate": "2025-08-01",
            "publicationType": "conference", "venue": "SIGMOD",
            "keywords": ["database"], "evidenceUrls": ["https://openalex.org/W1"],
        }]
        values = professor_logic.collect_translation_inputs(
            professors, [analysis], publications, "2024-09-01", "2026-09-01",
        )
        for expected in (
            "Chair Professor", "Thrust of Artificial Intelligence", "Information Hub",
            analysis["summaryEn"], analysis["notes"][0], "A Robust Data System",
        ):
            self.assertIn(expected, values)
        self.assertNotIn("SIGMOD", values)
        self.assertNotIn("database", values)
        self.assertNotIn("20", values)
        self.assertNotIn("https://openalex.org/W1", values)
        self.assertEqual(values, sorted(set(values), key=str.casefold))

    def test_translation_preparation_cli_emits_only_missing_frozen_inputs(self):
        professors, _ = normalize_faculty_rows([faculty_row()], baseline_ids={"20"})
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            analysis = research_analysis()
            generate_documents(root, professors, [analysis], [], "2024-09-01", "2026-09-01", "2026-09-02T00:00:00Z", research_subdirections=reviewed_subdirections())
            (root / "data/professors.json").write_text(json.dumps(professors, ensure_ascii=False), encoding="utf-8")
            (root / "data/research-analysis.json").write_text(json.dumps([analysis], ensure_ascii=False), encoding="utf-8")
            (root / "data/publications.json").write_text("[]", encoding="utf-8")
            (root / "data/translations.zh-CN.json").write_text(json.dumps({"Database systems": "数据库系统"}), encoding="utf-8")
            result = subprocess.run([
                sys.executable,
                str(Path(__file__).parents[1] / "scripts/prepare_chinese_translation.py"),
                "--root", str(root), "--start", "2024-09-01", "--cutoff", "2026-09-01",
            ], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads((root / "reports/2026-09-01-translation-inputs.json").read_text(encoding="utf-8"))
            self.assertNotIn("Database systems", payload["inputs"])
            self.assertIn(analysis["summaryEn"], payload["inputs"])
            self.assertEqual(result.stdout, f"{len(payload['inputs'])}/{payload['totalInputs']}\n")

    def test_bilingual_parity_rejects_missing_or_changed_identity(self):
        english = "# Lei CHEN\n\nProfessor identity: `20`\n\nPublication identity: `doi:10.1/example`\n\n[Official profile](https://facultyprofiles.hkust-gz.edu.cn/faculty-personal-page?id=20)\n"
        chinese = "# 陈雷 · Lei CHEN\n\nProfessor identity: `20`\n\nPublication identity: `doi:10.1/example`\n\n[官方主页](https://facultyprofiles.hkust-gz.edu.cn/faculty-personal-page?id=20)\n"
        self.assertEqual(validate_bilingual_parity(english, chinese), [])
        self.assertIn("publication identities differ", validate_bilingual_parity(english, chinese.replace("Publication identity: `doi:10.1/example`\n\n", "")))

    def test_bilingual_parity_rejects_order_level_and_duplicate_link_drift(self):
        english = "# Name\n\nProfessor identity: `20`\n\n## Official profile and contact\n\n[x](https://openalex.org/W1)\n[x](https://openalex.org/W1)\n\n## Research analysis\n"
        chinese = "# 姓名\n\nProfessor identity: `20`\n\n## 官方资料与联系方式\n\n[x](https://openalex.org/W1)\n[x](https://openalex.org/W1)\n\n## 研究分析\n"
        self.assertEqual(validate_bilingual_parity(english, chinese), [])
        self.assertIn("heading structure differs", validate_bilingual_parity(english, chinese.replace("## 官方资料与联系方式", "### 官方资料与联系方式")))
        reordered = chinese.replace("## 官方资料与联系方式", "## __TEMP__", 1).replace("## 研究分析", "## 官方资料与联系方式", 1).replace("## __TEMP__", "## 研究分析", 1)
        self.assertIn("heading structure differs", validate_bilingual_parity(english, reordered))
        self.assertIn("external links differ", validate_bilingual_parity(english, chinese.replace("[x](https://openalex.org/W1)\n", "", 1)))

    def test_bilingual_parity_accepts_localized_overview_directory(self):
        english = "# All HKUST(GZ) Professor Information\n\n## Professor directory\n\n### [Lei CHEN](professors/information-hub/lei-chen-20.md)\n"
        chinese = "# 香港科技大学（广州）教授信息总览\n\n## 教授目录\n\n### [陈雷 · Lei CHEN](professors/information-hub/lei-chen-20.zh-CN.md)\n"
        self.assertEqual(validate_bilingual_parity(english, chinese), [])

    def test_generation_writes_lightweight_directory_index_without_publication_details(self):
        professors, _ = normalize_faculty_rows([faculty_row()], baseline_ids={"20"})
        publications = [{
            "officialProfileId": "20",
            "publicationId": "doi:10.1/example",
            "title": "A Publication Title That Must Stay Offline",
            "effectiveDate": "2025-08-01",
            "publicationType": "conference",
            "venue": "Private Venue",
            "keywords": ["publication-only keyword"],
            "evidenceUrls": ["https://openalex.org/W1"],
        }]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = generate_documents(
                root, professors, [research_analysis()], publications,
                "2024-09-01", "2026-09-01", "2026-09-02T00:00:00Z",
                research_subdirections=reviewed_subdirections(publication_ids=["doi:10.1/example"]),
            )
            payload = json.loads((root / "data/directory-index.json").read_text(encoding="utf-8"))
        self.assertEqual(payload, result["directoryIndex"])
        self.assertEqual(payload["schemaVersion"], 1)
        self.assertEqual(payload["cutoff"], "2026-09-01")
        record = payload["records"][0]
        self.assertEqual(record["officialProfileId"], "20")
        self.assertEqual(record["email"], "leichen@hkust-gz.edu.cn")
        self.assertEqual(record["hubs"], ["Information Hub"])
        self.assertEqual(record["researchFields"], ["Data management", "Database systems"])
        self.assertEqual(record["keywords"], ["databases", "knowledge graphs"])
        self.assertEqual(record["subdirectionNames"], ["Evidence-limited research profile"])
        self.assertEqual(record["publicationCount"], 1)
        self.assertEqual(record["lastVerifiedOn"], professors[0]["lastVerifiedOn"])
        self.assertEqual(
            set(record),
            {
                "officialProfileId", "slug", "nameZh", "nameEn", "email", "phone",
                "titles", "hubs", "units", "researchFields", "keywords",
                "subdirectionNames", "publicationCount", "lastVerifiedOn",
            },
        )
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("A Publication Title That Must Stay Offline", serialized)
        self.assertNotIn("publication-only keyword", serialized)
        self.assertNotIn("Private Venue", serialized)

    def test_manifest_contains_four_level_documents(self):
        professors, _ = normalize_faculty_rows([faculty_row()], baseline_ids={"20"})
        with tempfile.TemporaryDirectory() as temporary:
            manifest = generate_documents(
                Path(temporary), professors, [research_analysis()], [],
                "2024-09-01", "2026-09-01", "2026-09-02T00:00:00Z",
                research_subdirections=reviewed_subdirections(),
            )["manifest"]
        paths = manifest["documents"][professors[0]["slug"]]
        self.assertEqual(set(paths), {"profile", "publications"})
        self.assertEqual(set(paths["profile"]), {"en", "zhCN"})
        self.assertEqual(set(paths["publications"]), {"en", "zhCN"})
        self.assertTrue(paths["profile"]["en"].endswith(".md"))
        self.assertTrue(paths["profile"]["zhCN"].endswith(".zh-CN.md"))
        self.assertTrue(paths["publications"]["en"].endswith(".publications.md"))
        self.assertTrue(paths["publications"]["zhCN"].endswith(".publications.zh-CN.md"))
        for group in paths.values():
            for relative_path in group.values():
                self.assertFalse(Path(relative_path).is_absolute())
                self.assertNotIn("..", Path(relative_path).parts)

    def test_stage_validator_accepts_task_three_artifacts_without_final_documents(self):
        professors, _ = normalize_faculty_rows([faculty_row()], baseline_ids={"20"})
        analysis = research_analysis()
        subdirections = reviewed_subdirections()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            generate_documents(
                root, professors, [analysis], [],
                "2024-09-01", "2026-09-01", "2026-09-02T00:00:00Z",
                research_subdirections=subdirections,
            )
            for filename, value in (
                ("professors.json", professors),
                ("research-analysis.json", [analysis]),
                ("publications.json", []),
                ("research-subdirections.json", subdirections),
            ):
                (root / "data" / filename).write_text(json.dumps(value), encoding="utf-8")
            command = [
                sys.executable,
                str(Path(__file__).parents[1] / "scripts/validate_professor_information.py"),
                "--root", str(root), "--cutoff", "2026-09-01",
            ]
            valid = subprocess.run(command, capture_output=True, text=True, check=False)
            self.assertEqual(valid.returncode, 0, valid.stderr)
            manifest_path = root / "data/manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["documents"][professors[0]["slug"]]["profile"]["en"] = "../unsafe.md"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            unsafe = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertNotEqual(unsafe.returncode, 0)
        self.assertIn("safe", unsafe.stderr)

    def test_not_started_retrieval_is_not_publishable(self):
        analyses = [research_analysis(status="not-started")]
        with self.assertRaisesRegex(SourceDataError, "not-started"):
            validate_retrieval_statuses(analyses)
        analyses[0]["status"] = "incomplete"
        analyses[0]["notes"] = ["One research index was blocked after bounded attempts; arXiv was checked."]
        validate_retrieval_statuses(analyses)

    def test_publication_work_queue_uses_approved_research_sources(self):
        professors, _ = normalize_faculty_rows([faculty_row()], baseline_ids={"20"})
        queue = build_publication_work_queue(professors, "2024-09-01", "2026-09-01")
        item = queue["professors"][0]
        self.assertEqual(queue["window"], {"start": "2024-09-01", "end": "2026-09-01", "inclusive": True})
        self.assertIn("api.openalex.org/authors", item["openAlexAuthorSearchUrl"])
        self.assertEqual(item.get("paperscraperQuery", {}).get("authorNames"), ["Lei CHEN", "陈雷"])
        self.assertIn("export.arxiv.org", item.get("arxivValidationUrl", ""))
        self.assertEqual(queue["sources"], [
            "OpenAlex",
            "paperscraper:arxiv",
            "paperscraper:pubmed",
            "paperscraper:biorxiv",
            "paperscraper:medrxiv",
            "paperscraper:chemrxiv",
            "paperscraper:semantic-scholar",
            "arXivValidation",
        ])
        self.assertEqual(item["status"], "pending")
        self.assertEqual(item["attempts"], [])

    def test_paperscraper_rows_are_normalized_without_losing_source_identity(self):
        try:
            normalize = retrieval.normalize_paperscraper_rows
        except AttributeError:
            self.fail("paperscraper normalization behavior is missing")
        works = normalize("semantic-scholar", [{
            "paperId": "S2-1",
            "title": "A Study",
            "authors": [{"name": "Lei Chen"}],
            "publicationDate": "2025-03-04",
            "externalIds": {"DOI": "10.1/Example", "ArXiv": "2501.00001"},
            "venue": "Example Journal",
            "abstract": "Evidence text.",
        }], "2024-09-01", "2026-09-01")
        self.assertEqual(len(works), 1)
        self.assertEqual(works[0]["semanticScholarId"], "S2-1")
        self.assertEqual(works[0]["doi"], "10.1/example")
        self.assertEqual(works[0]["arxivId"], "2501.00001")
        self.assertEqual(works[0]["effectiveDate"], "2025-03-04")
        self.assertEqual(works[0]["datePrecision"], "day")
        self.assertEqual(works[0]["url"], "https://www.semanticscholar.org/paper/S2-1")

    def test_paperscraper_year_only_records_use_explicit_precision(self):
        try:
            normalize = retrieval.normalize_paperscraper_rows
        except AttributeError:
            self.fail("paperscraper normalization behavior is missing")
        works = normalize("semantic-scholar", [{
            "title": "A Year-only Study",
            "authors": ["Lei Chen"],
            "year": 2025,
            "journal": "Example Venue",
            "citations": 7,
            "paperId": "S-year-only",
        }], "2024-09-01", "2026-09-01")
        self.assertEqual(works[0]["effectiveDate"], "2025-01-01")
        self.assertEqual(works[0]["datePrecision"], "year")
        self.assertEqual(works[0]["url"], "https://www.semanticscholar.org/paper/S-year-only")

    def test_paperscraper_nonfinite_missing_values_produce_strict_json(self):
        works = retrieval.normalize_paperscraper_rows("pubmed", [{
            "title": "A Study With Missing Optional Metadata",
            "authors": ["Lei Chen"],
            "date": "2025-03-04",
            "venue": float("nan"),
            "abstract": float("nan"),
            "citations": float("nan"),
        }], "2024-09-01", "2026-09-01")
        self.assertEqual(works[0]["venue"], "pubmed")
        self.assertIsNone(works[0]["summary"])
        self.assertIsNone(works[0]["citations"])
        json.dumps(works, allow_nan=False)

    def test_paperscraper_collection_dispatch_normalizes_the_selected_source(self):
        try:
            collect = retrieval.collect_paperscraper_source
        except AttributeError:
            self.fail("paperscraper collection dispatch is missing")

        def pubmed_collector(request):
            return [{
                "pubmed_id": "123",
                "title": "A Biomedical Study",
                "authors": ["Lei Chen"],
                "date": "2025-06-01",
                "doi": "10.1/Bio",
                "journal": "Biomedical Journal",
            }]

        result = collect(
            "pubmed",
            {"authorNames": ["Lei CHEN", "陈雷"]},
            "2024-09-01",
            "2026-09-01",
            {"pubmed": pubmed_collector},
        )
        self.assertEqual(result["source"], "paperscraper:pubmed")
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["works"][0]["pubmedId"], "123")

    def test_paperscraper_collection_rejects_an_unapproved_source(self):
        try:
            collect = retrieval.collect_paperscraper_source
        except AttributeError:
            self.fail("paperscraper collection dispatch is missing")
        with self.assertRaisesRegex(ValueError, "unsupported paperscraper source"):
            collect("crossref", {"authorNames": ["Lei CHEN"]}, "2024-09-01", "2026-09-01", {})

    def test_paperscraper_worker_timeout_honors_a_short_bounded_timeout(self):
        item = {"paperscraperQuery": {"authorNames": ["Synthetic Researcher"]}}
        calls = []

        def fake_run(command, **kwargs):
            calls.append(kwargs["timeout"])
            output_path = Path(command[command.index("--output") + 1])
            output_path.write_text(json.dumps({
                "source": "paperscraper:semantic-scholar", "status": "complete", "works": [],
            }), encoding="utf-8")
            return types.SimpleNamespace(returncode=0)

        with patch.object(retrieval.subprocess, "run", side_effect=fake_run):
            result = retrieval._retrieve("paperscraper:semantic-scholar", item, "2024-09-01", "2026-09-01", 1)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(calls, [5])

    def test_semantic_scholar_worker_uses_high_level_client_without_repository_endpoint(self):
        scripts_path = str(Path(__file__).parents[1] / "scripts")
        sys.path.insert(0, scripts_path)
        try:
            import paperscraper_worker as worker
        finally:
            sys.path.remove(scripts_path)

        class SyntheticPaper:
            raw_data = {
                "paperId": "S1",
                "title": "Synthetic Work",
                "authors": [{"name": "Synthetic Researcher"}],
                "publicationDate": "2025-01-01",
                "year": 2025,
                "externalIds": {},
                "venue": "Synthetic Venue",
                "abstract": None,
                "citationCount": 0,
            }

        class SyntheticClient:
            def __init__(self, **_options):
                pass

            def get_author_papers(self, _author_id, fields, limit):
                if "paperId" not in fields or limit != 1000:
                    raise AssertionError("high-level client received an incomplete request")
                return [SyntheticPaper()]

        paperscraper = types.ModuleType("paperscraper")
        paperscraper.__path__ = []
        arxiv = types.ModuleType("paperscraper.arxiv")
        arxiv.get_arxiv_papers_api = lambda *_args, **_kwargs: []
        citations = types.ModuleType("paperscraper.citations")
        citations.__path__ = []
        citation_utils = types.ModuleType("paperscraper.citations.utils")
        citation_utils.author_name_to_ssaid = lambda _name: ("A1", "Synthetic Researcher")
        citation_utils.semantic_scholar_requests_get = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("repository-owned endpoint request"))
        load_dumps = types.ModuleType("paperscraper.load_dumps")
        load_dumps.QUERY_FN_DICT = {}
        pubmed = types.ModuleType("paperscraper.pubmed")
        pubmed.get_pubmed_papers = lambda *_args, **_kwargs: []
        semanticscholar = types.ModuleType("semanticscholar")
        semanticscholar.SemanticScholar = SyntheticClient
        modules = {
            "paperscraper": paperscraper,
            "paperscraper.arxiv": arxiv,
            "paperscraper.citations": citations,
            "paperscraper.citations.utils": citation_utils,
            "paperscraper.load_dumps": load_dumps,
            "paperscraper.pubmed": pubmed,
            "semanticscholar": semanticscholar,
        }
        with patch.dict(sys.modules, modules):
            try:
                rows = worker._collectors()["semantic-scholar"]({"authorNames": ["Synthetic Researcher"]})
            except AssertionError as error:
                self.fail(str(error))
        self.assertEqual(rows, [SyntheticPaper.raw_data])

    def test_retrieval_source_selection_rejects_sources_outside_the_plan(self):
        try:
            select = retrieval.select_sources
        except AttributeError:
            self.fail("retrieval source selection is missing")
        self.assertEqual(select("OpenAlex,arXivValidation"), ("OpenAlex", "arXivValidation"))
        with self.assertRaisesRegex(ValueError, "outside the approved source plan"):
            select("Crossref")
        with self.assertRaisesRegex(ValueError, "outside the approved source plan"):
            select("paperscraper:google-scholar")
        self.assertNotIn("paperscraper:google-scholar", select(None))

    def test_excluded_google_scholar_evidence_is_not_consumed(self):
        evidence = [{
            "officialProfileId": "20",
            "openalex": {"status": "complete", "works": []},
            "paperscraper": {
                "google-scholar": {
                    "status": "complete",
                    "works": [{
                        "title": "Excluded Work",
                        "effectiveDate": "2025-01-01",
                        "url": "https://scholar.google.com/scholar?q=Excluded+Work",
                    }],
                },
            },
            "failedSkipped": [],
            "blocked": [],
        }]
        candidates, quarantined = publication_candidates_from_evidence(evidence)
        self.assertEqual(candidates, [])
        self.assertEqual(quarantined, [])

    def test_selected_source_resume_preserves_existing_primary_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            queue_path = root / "queue.json"
            output_path = root / "evidence.json"
            cache_dir = root / "cache"
            queue_path.write_text(json.dumps(synthetic_retrieval_queue()), encoding="utf-8")
            output_path.write_text(json.dumps({
                "schemaVersion": 2,
                "window": {"start": "2024-09-01", "end": "2026-09-01", "inclusive": True},
                "records": [{
                    "officialProfileId": "P1",
                    "openalex": {"source": "OpenAlex", "status": "complete", "truncated": False, "works": []},
                    "failedSkipped": [],
                    "blocked": [],
                }],
            }), encoding="utf-8")
            resumed = {"source": "arXivValidation", "status": "complete", "works": []}
            stdout = io.StringIO()
            argv = [
                "retrieve_research_sources.py",
                "--start", "2024-09-01", "--cutoff", "2026-09-01",
                "--queue", str(queue_path), "--output", str(output_path),
                "--cache-dir", str(cache_dir), "--sources", "arXivValidation", "--delay", "0",
            ]
            with (
                patch.object(sys, "argv", argv),
                patch.object(retrieval, "_retrieve", return_value=resumed),
                contextlib.redirect_stdout(stdout),
            ):
                retrieval.main()
            record = json.loads(output_path.read_text(encoding="utf-8"))["records"][0]
        self.assertIn("openalex", record)
        self.assertIn("arxivValidation", record)
        self.assertEqual(stdout.getvalue(), "1/1\n")

    def test_legacy_nonfinite_checkpoint_is_sanitized_on_resume(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            queue_path = root / "queue.json"
            output_path = root / "evidence.json"
            cache_dir = root / "cache"
            cache_dir.mkdir()
            queue_path.write_text(json.dumps(synthetic_retrieval_queue()), encoding="utf-8")
            (cache_dir / "P1-paperscraper-pubmed.json").write_text(json.dumps({
                "source": "paperscraper:pubmed",
                "status": "complete",
                "works": [{"title": "Synthetic", "venue": float("nan")}],
            }), encoding="utf-8")
            argv = [
                "retrieve_research_sources.py",
                "--start", "2024-09-01", "--cutoff", "2026-09-01",
                "--queue", str(queue_path), "--output", str(output_path),
                "--cache-dir", str(cache_dir), "--sources", "paperscraper:pubmed", "--delay", "0",
            ]
            with (
                patch.object(sys, "argv", argv),
                patch.object(retrieval, "_retrieve", side_effect=AssertionError("cache should be reused")),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                retrieval.main()
            strict = json.loads(
                output_path.read_text(encoding="utf-8"),
                parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
            )
        self.assertIsNone(strict["records"][0]["paperscraper"]["pubmed"]["works"][0]["venue"])

    def test_selected_source_resume_rejects_non_object_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            queue_path = root / "queue.json"
            output_path = root / "evidence.json"
            queue_path.write_text(json.dumps(synthetic_retrieval_queue()), encoding="utf-8")
            output_path.write_text("[]", encoding="utf-8")
            argv = [
                "retrieve_research_sources.py",
                "--start", "2024-09-01", "--cutoff", "2026-09-01",
                "--queue", str(queue_path), "--output", str(output_path),
                "--cache-dir", str(root / "cache"), "--sources", "OpenAlex", "--delay", "0",
            ]
            with patch.object(sys, "argv", argv):
                try:
                    with self.assertRaisesRegex(SourceDataError, "incompatible schema or window"):
                        retrieval.main()
                except AttributeError as error:
                    self.fail(f"malformed evidence escaped source-data validation: {error}")

    def test_targeted_retry_limit_persists_across_restarts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            queue_path = root / "queue.json"
            output_path = root / "evidence.json"
            cache_dir = root / "cache"
            queue_path.write_text(json.dumps(synthetic_retrieval_queue()), encoding="utf-8")
            attempts = 0

            def fail_retrieval(*_args, **_kwargs):
                nonlocal attempts
                attempts += 1
                raise SourceDataError("synthetic retrieval failure")

            argv = [
                "retrieve_research_sources.py",
                "--start", "2024-09-01", "--cutoff", "2026-09-01",
                "--queue", str(queue_path), "--output", str(output_path),
                "--cache-dir", str(cache_dir), "--sources", "OpenAlex", "--delay", "0",
            ]
            stdout = io.StringIO()
            with (
                patch.object(sys, "argv", argv),
                patch.object(retrieval, "_retrieve", side_effect=fail_retrieval),
                contextlib.redirect_stdout(stdout),
            ):
                retrieval.main()
                retrieval.main()
            record = json.loads(output_path.read_text(encoding="utf-8"))["records"][0]
        self.assertEqual(attempts, 4, "one initial attempt plus at most three targeted retries")
        self.assertIn("OpenAlex", record["failedSkipped"])
        self.assertEqual(stdout.getvalue(), "1/1\n" * 5)

    def test_truncated_openalex_checkpoint_is_not_reused_as_complete(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            queue_path = root / "queue.json"
            output_path = root / "evidence.json"
            cache_dir = root / "cache"
            cache_dir.mkdir()
            queue_path.write_text(json.dumps(synthetic_retrieval_queue()), encoding="utf-8")
            (cache_dir / "P1-openalex.json").write_text(json.dumps({
                "source": "OpenAlex", "status": "complete", "truncated": True, "works": [],
            }), encoding="utf-8")
            refreshed = {"source": "OpenAlex", "status": "complete", "truncated": False, "works": []}
            argv = [
                "retrieve_research_sources.py",
                "--start", "2024-09-01", "--cutoff", "2026-09-01",
                "--queue", str(queue_path), "--output", str(output_path),
                "--cache-dir", str(cache_dir), "--sources", "OpenAlex", "--delay", "0",
            ]
            with (
                patch.object(sys, "argv", argv),
                patch.object(retrieval, "_retrieve", return_value=refreshed) as retrieve_source,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                retrieval.main()
            record = json.loads(output_path.read_text(encoding="utf-8"))["records"][0]
        self.assertEqual(retrieve_source.call_count, 1)
        self.assertFalse(record["openalex"]["truncated"])

    def test_resume_does_not_preserve_truncated_openalex_evidence_as_complete(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            queue_path = root / "queue.json"
            output_path = root / "evidence.json"
            cache_dir = root / "cache"
            queue_path.write_text(json.dumps(synthetic_retrieval_queue()), encoding="utf-8")
            output_path.write_text(json.dumps({
                "schemaVersion": 2,
                "window": {"start": "2024-09-01", "end": "2026-09-01", "inclusive": True},
                "records": [{
                    "officialProfileId": "P1",
                    "openalex": {"source": "OpenAlex", "status": "complete", "truncated": True, "works": []},
                    "failedSkipped": [], "blocked": [],
                }],
            }), encoding="utf-8")
            argv = [
                "retrieve_research_sources.py",
                "--start", "2024-09-01", "--cutoff", "2026-09-01",
                "--queue", str(queue_path), "--output", str(output_path),
                "--cache-dir", str(cache_dir), "--sources", "arXivValidation", "--delay", "0",
            ]
            with (
                patch.object(sys, "argv", argv),
                patch.object(retrieval, "_retrieve", return_value={"source": "arXivValidation", "status": "complete", "works": []}),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                retrieval.main()
            record = json.loads(output_path.read_text(encoding="utf-8"))["records"][0]
        self.assertNotIn("openalex", record)
        self.assertIn("OpenAlex", record["failedSkipped"])

    def test_research_analysis_skeleton_is_explicitly_not_publishable(self):
        professors, _ = normalize_faculty_rows([faculty_row()], baseline_ids={"20"})
        analyses = build_research_analysis_skeleton(professors, "2026-09-01")
        self.assertEqual(analyses[0]["officialProfileId"], "20")
        self.assertEqual(analyses[0]["status"], "not-started")
        self.assertEqual(analyses[0]["evidenceUrls"], [])
        with self.assertRaisesRegex(SourceDataError, "not-started"):
            validate_retrieval_statuses(analyses)

    def test_research_analysis_is_derived_from_disambiguated_openalex_topics(self):
        try:
            build_analysis = professor_logic.build_research_analysis_from_evidence
        except AttributeError:
            self.fail("evidence-derived research analysis is missing")
        professors, _ = normalize_faculty_rows([faculty_row()], baseline_ids={"20"})
        evidence = [{
            "officialProfileId": "20",
            "openalex": {
                "status": "complete",
                "evidenceUrl": "https://openalex.org/A1",
                "works": [
                    {"topics": ["Database systems", "Knowledge graphs"], "keywords": ["query optimization"], "url": "https://openalex.org/W1"},
                    {"topics": ["Database systems"], "keywords": ["data management"], "url": "https://openalex.org/W2"},
                ],
            },
            "paperscraper": {},
            "arxivValidation": {"status": "complete", "works": []},
            "failedSkipped": [],
            "blocked": [],
        }]
        analyses = build_analysis(professors, evidence, "2026-09-01")
        self.assertEqual(analyses[0]["researchInterests"], ["Database systems", "Knowledge graphs"])
        self.assertEqual(analyses[0]["keywords"], ["data management", "query optimization"])
        self.assertEqual(analyses[0]["status"], "complete")
        self.assertIn("Database systems", analyses[0]["summaryEn"])

    def test_openalex_parser_normalizes_work_and_reconstructs_abstract(self):
        payload = {"results": [{
            "id": "https://openalex.org/W1", "doi": "https://doi.org/10.1/example",
            "display_name": "A Study", "publication_date": "2026-01-02", "type": "article",
            "abstract_inverted_index": {"An": [0], "abstract": [1]},
            "topics": [{"display_name": "Database systems"}],
            "keywords": [{"display_name": "knowledge graph"}],
            "ids": {"openalex": "https://openalex.org/W1"},
        }]}
        works = parse_openalex_works(payload, "2024-09-01", "2026-09-01")
        self.assertEqual(works[0]["doi"], "10.1/example")
        self.assertEqual(works[0]["summary"], "An abstract")
        self.assertEqual(works[0]["topics"], ["Database systems"])

    def test_openalex_author_disambiguation_handles_name_formatting_without_guessing(self):
        candidates = [
            {
                "id": "https://openalex.org/A1",
                "display_name": "Jian-Ping Gong",
                "last_known_institutions": [{"display_name": "The Hong Kong University of Science and Technology (Guangzhou)"}],
            },
            {
                "id": "https://openalex.org/A2",
                "display_name": "Jian Ping Gong",
                "last_known_institutions": [{"display_name": "Unrelated University"}],
            },
        ]
        chosen = retrieval._choose_author(candidates, "Jianping GONG", "OpenAlex")
        self.assertEqual(chosen["id"], "https://openalex.org/A1")
        candidates[1]["last_known_institutions"] = [{"display_name": "HKUST"}]
        self.assertIsNone(retrieval._choose_author(candidates, "Jianping GONG", "OpenAlex"))

    def test_openalex_author_disambiguation_rejects_unique_unaffiliated_homonym(self):
        candidates = [{
            "id": "https://openalex.org/A1",
            "display_name": "Synthetic Researcher",
            "last_known_institutions": [{"display_name": "Unrelated University"}],
        }]
        self.assertIsNone(retrieval._choose_author(candidates, "Synthetic Researcher", "OpenAlex"))

    def test_openalex_retrieval_follows_cursor_to_terminal_page(self):
        item = {
            "nameEn": "Synthetic Researcher",
            "openAlexAuthorSearchUrl": "https://api.openalex.org/authors?search=synthetic",
        }
        author_search = {"results": [{
            "id": "https://openalex.org/A1",
            "display_name": "Synthetic Researcher",
            "last_known_institutions": [{"display_name": "HKUST"}],
        }]}
        first_page = {
            "meta": {"count": 2, "next_cursor": "next-token"},
            "results": [{
                "id": "https://openalex.org/W1", "display_name": "Synthetic Work One",
                "publication_date": "2025-01-01", "ids": {},
            }],
        }
        final_page = {
            "meta": {"count": 2, "next_cursor": None},
            "results": [{
                "id": "https://openalex.org/W2", "display_name": "Synthetic Work Two",
                "publication_date": "2025-02-01", "ids": {},
            }],
        }
        with patch.object(retrieval, "_json", side_effect=[author_search, first_page, final_page]) as request_json:
            result = retrieval._retrieve("OpenAlex", item, "2024-09-01", "2026-09-01", 20)
        requested_urls = [call.args[0] for call in request_json.call_args_list]
        self.assertEqual(len(requested_urls), 3)
        self.assertIn("cursor=%2A", requested_urls[1])
        self.assertIn("cursor=next-token", requested_urls[2])
        self.assertEqual(result["status"], "complete")
        self.assertFalse(result["truncated"])
        self.assertEqual(len(result["works"]), 2)

    def test_openalex_retrieval_rejects_premature_terminal_cursor(self):
        item = {
            "nameEn": "Synthetic Researcher",
            "openAlexAuthorSearchUrl": "https://api.openalex.org/authors?search=synthetic",
        }
        author_search = {"results": [{
            "id": "https://openalex.org/A1",
            "display_name": "Synthetic Researcher",
            "last_known_institutions": [{"display_name": "HKUST"}],
        }]}
        incomplete_page = {
            "meta": {"count": 2, "next_cursor": None},
            "results": [{
                "id": "https://openalex.org/W1", "display_name": "Synthetic Work",
                "publication_date": "2025-01-01", "ids": {},
            }],
        }
        with patch.object(retrieval, "_json", side_effect=[author_search, incomplete_page]):
            with self.assertRaisesRegex(SourceDataError, "pagination is incomplete"):
                retrieval._retrieve("OpenAlex", item, "2024-09-01", "2026-09-01", 20)

    def test_curl_request_spec_keeps_url_secrets_out_of_process_arguments(self):
        command, stdin = retrieval.curl_request_spec("https://api.openalex.org/works?api_key=secret", 20)
        self.assertNotIn("secret", " ".join(command))
        self.assertIn("api_key=secret", stdin.decode("utf-8"))
        with self.assertRaisesRegex(SourceDataError, "control characters"):
            retrieval.curl_request_spec("https://example.test/\nheader", 20)

    def test_arxiv_parser_uses_first_submission_and_window(self):
        xml = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry>
        <id>https://arxiv.org/abs/2501.00001v2</id><updated>2026-01-02T00:00:00Z</updated><published>2025-01-01T00:00:00Z</published>
        <title>A Preprint</title><summary>An original abstract.</summary><author><name>Example Author</name></author><category term="cs.DB" />
        </entry></feed>"""
        works = parse_arxiv_feed(xml, "2024-09-01", "2026-09-01")
        self.assertEqual(len(works), 1)
        self.assertEqual(works[0]["effectiveDate"], "2025-01-01")
        self.assertEqual(works[0]["arxivId"], "2501.00001")

    def test_openalex_and_arxiv_records_deduplicate_by_shared_doi(self):
        base = {
            "officialProfileId": "20", "title": "A Study", "effectiveDate": "2025-01-01",
            "doi": "10.1/example", "arxivId": "2501.00001", "keywords": [],
        }
        records = [
            {**base, "publicationType": "arXiv", "evidenceUrls": ["https://arxiv.org/abs/2501.00001"]},
            {**base, "publicationType": "journal", "venue": "Example", "openAlexId": "W1", "evidenceUrls": ["https://openalex.org/W1"]},
        ]
        merged, conflicts = deduplicate_publications(records)
        self.assertEqual(conflicts, [])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["publicationId"], "doi:10.1/example")
        self.assertEqual(len(merged[0]["evidenceUrls"]), 2)
        self.assertEqual(merged[0]["openAlexId"], "W1")

    def test_arxiv_name_result_requires_cross_source_confirmation(self):
        evidence = [{
            "officialProfileId": "20",
            "openalex": {"status": "complete", "evidenceUrl": "https://openalex.org/A1", "works": [{
                "openAlexId": "W1", "doi": None, "arxivId": "2501.00001",
                "title": "Confirmed Work", "effectiveDate": "2025-01-01", "publicationType": "article",
                "venue": "Example", "keywords": ["database"], "url": "https://openalex.org/W1",
            }]},
            "arxiv": {"status": "complete", "evidenceUrl": "https://export.arxiv.org/api/query?q=x", "works": [
                {"arxivId": "2501.00001", "title": "Confirmed Work", "effectiveDate": "2025-01-01", "publicationType": "arXiv", "venue": "arXiv", "keywords": [], "url": "https://arxiv.org/abs/2501.00001"},
                {"arxivId": "2502.00002", "title": "Unrelated Same-name Work", "effectiveDate": "2025-02-01", "publicationType": "arXiv", "venue": "arXiv", "keywords": [], "url": "https://arxiv.org/abs/2502.00002"},
            ]},
        }]
        candidates, quarantined = publication_candidates_from_evidence(evidence)
        self.assertEqual(len(candidates), 2)
        self.assertEqual(len(quarantined), 1)
        self.assertEqual(quarantined[0]["reason"], "unconfirmed-arxiv-author")

    def test_paperscraper_results_require_openalex_confirmation(self):
        evidence = [{
            "officialProfileId": "20",
            "openalex": {"status": "complete", "works": [{
                "openAlexId": "W1", "doi": "10.1/example", "arxivId": None,
                "title": "Confirmed Work", "effectiveDate": "2025-01-01",
                "publicationType": "article", "venue": "Example", "keywords": [],
                "url": "https://openalex.org/W1",
            }]},
            "paperscraper": {
                "pubmed": {"status": "complete", "works": [
                    {"pubmedId": "P1", "doi": "10.1/example", "title": "Confirmed Work", "effectiveDate": "2025-01-01", "publicationType": "journal", "venue": "Example", "keywords": [], "url": "https://pubmed.ncbi.nlm.nih.gov/P1/"},
                    {"pubmedId": "P2", "doi": "10.1/other", "title": "Same-name False Positive", "effectiveDate": "2025-02-01", "publicationType": "journal", "venue": "Other", "keywords": [], "url": "https://pubmed.ncbi.nlm.nih.gov/P2/"},
                ]},
            },
            "arxivValidation": {"status": "complete", "works": []},
        }]
        candidates, quarantined = publication_candidates_from_evidence(evidence)
        self.assertEqual(len(candidates), 2)
        self.assertEqual(len(quarantined), 1)
        self.assertEqual(quarantined[0]["reason"], "unconfirmed-paperscraper-author")

    def test_arxiv_validation_cannot_create_a_candidate(self):
        evidence = [{
            "officialProfileId": "20",
            "openalex": {"status": "complete", "works": []},
            "paperscraper": {},
            "arxivValidation": {"status": "complete", "works": [{
                "arxivId": "2501.00001", "title": "Validation-only Work",
                "effectiveDate": "2025-01-01", "publicationType": "arXiv",
                "venue": "arXiv", "keywords": [],
                "url": "https://arxiv.org/abs/2501.00001",
            }]},
        }]
        candidates, quarantined = publication_candidates_from_evidence(evidence)
        self.assertEqual(candidates, [])
        self.assertEqual(len(quarantined), 1)
        self.assertEqual(quarantined[0]["reason"], "unmatched-arxiv-validation")

    def test_source_specific_ids_survive_deduplication(self):
        records = [
            {"officialProfileId": "20", "title": "A Study", "effectiveDate": "2025-01-01", "publicationType": "article", "doi": "10.1/example", "openAlexId": "W1", "evidenceUrls": ["https://openalex.org/W1"]},
            {"officialProfileId": "20", "title": "A Study", "effectiveDate": "2025-01-01", "publicationType": "article", "doi": "10.1/example", "semanticScholarId": "S1", "pubmedId": "P1", "evidenceUrls": ["https://www.semanticscholar.org/paper/S1", "https://pubmed.ncbi.nlm.nih.gov/P1/"]},
        ]
        try:
            merged, conflicts = deduplicate_publications(records)
        except SourceDataError as error:
            self.fail(f"approved source IDs and URLs were rejected: {error}")
        self.assertEqual(conflicts, [])
        self.assertEqual(merged[0]["semanticScholarId"], "S1")
        self.assertEqual(merged[0]["pubmedId"], "P1")

    def test_candidate_builder_writes_the_release_conflict_report(self):
        payload = {"records": [{
            "officialProfileId": "20",
            "openalex": {"status": "complete", "works": []},
            "paperscraper": {},
            "arxivValidation": {"status": "complete", "works": []},
            "failedSkipped": [],
            "blocked": [],
        }]}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "reports").mkdir()
            (root / "data").mkdir()
            (root / "reports/2026-09-01-retrieval-evidence.json").write_text(json.dumps(payload), encoding="utf-8")
            result = subprocess.run([
                sys.executable,
                str(Path(__file__).parents[1] / "scripts/build_publication_candidates.py"),
                "--cutoff", "2026-09-01",
            ], cwd=root, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((root / "reports/2026-09-01-conflicts.json").is_file())


if __name__ == "__main__":
    unittest.main()
