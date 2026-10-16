from datetime import UTC, date, datetime, timedelta

import pytest
from app.models import Artifact
from app.service import DemoService, ResourceNotFound


def test_policy_lookup_returns_source_linked_evidence() -> None:
    service = DemoService()

    citations = service.lookup_policy("pol-demo-auto", "deer strike vehicle damage")

    assert citations
    assert citations[0].section.startswith("Section 2.3")
    assert citations[0].source_document == "data/policies/demo-auto-policy.md"
    assert "animal strikes" in citations[0].quoted_text


def test_assessment_flags_driver_and_estimate_without_deciding_coverage() -> None:
    assessment = DemoService().assess_claim("clm-demo-002")

    finding_codes = {finding.finding_code for finding in assessment.completeness_findings}
    assert finding_codes == {
        "driver_not_listed",
        "repair_estimate_above_review_threshold",
    }
    assert assessment.policy_citations
    assert assessment.human_review_required is True
    assert "not a coverage decision" in assessment.summary


def test_incomplete_claim_requests_information() -> None:
    assessment = DemoService().assess_claim("clm-demo-003")

    assert assessment.triage_recommendation == "request_information"
    assert {finding.finding_code for finding in assessment.completeness_findings} == {
        "required_information_missing",
        "required_document_missing",
    }
    assert assessment.policy_citations == []
    assert assessment.human_review_required is True


def test_unknown_claim_is_an_explicit_not_found_error() -> None:
    try:
        DemoService().get_claim("clm-does-not-exist")
    except ResourceNotFound as error:
        assert "was not found" in str(error)
    else:
        raise AssertionError("Expected a missing claim to raise ResourceNotFound.")


@pytest.mark.parametrize("days_after_loss", [0, 29, 30, 31, 60])
def test_section_4_reporting_deadline(days_after_loss: int) -> None:
    service = DemoService()
    claim = service.get_claim("clm-demo-001")
    claim.loss_date = date(2024, 12, 15)
    claim.reported_date = claim.loss_date + timedelta(days=days_after_loss)

    assessment = service.assess_claim(claim.claim_id)

    late = [
        finding
        for finding in assessment.completeness_findings
        if finding.finding_code == "section_4_late_reporting"
    ]
    assert bool(late) is (days_after_loss > 30)
    if late:
        assert late[0].status == "needs_review"
        assert f"{days_after_loss} days" in late[0].description
        action = assessment.recommended_actions[0]
        assert "Confirm" in action.rationale
        section_4 = next(
            c for c in assessment.policy_citations if c.section.startswith("Section 4")
        )
        assert section_4.citation_id in action.supporting_citation_ids
    assert assessment.human_review_required is True
    assert assessment.triage_recommendation == "human_review"


@pytest.mark.parametrize("missing_field", ["loss_date", "reported_date"])
def test_section_4_missing_dates_require_follow_up(missing_field: str) -> None:
    service = DemoService()
    claim = service.get_claim("clm-demo-001")
    setattr(claim, missing_field, None)

    assessment = service.assess_claim(claim.claim_id)

    assert assessment.triage_recommendation == "request_information"
    assert any(
        missing_field.replace("_", " ") in finding.description
        for finding in assessment.completeness_findings
        if finding.finding_code == "required_information_missing"
    )
    assert not any(
        finding.finding_code == "section_4_late_reporting"
        for finding in assessment.completeness_findings
    )


def test_section_4_report_before_loss_requires_date_confirmation() -> None:
    service = DemoService()
    claim = service.get_claim("clm-demo-001")
    claim.reported_date = claim.loss_date - timedelta(days=1)

    assessment = service.assess_claim(claim.claim_id)

    assert assessment.completeness_findings[0].finding_code == (
        "section_4_reporting_dates_inconsistent"
    )
    assert assessment.completeness_findings[0].status == "inconsistent"
    assert assessment.recommended_actions[0].action_type == "review_conflicting_information"


@pytest.mark.parametrize(
    "statement",
    [
        "The vehicle was STOLEN.",
        "Vehicle theft occurred.",
        "The damage was caused by vandalism.",
        "Someone vandalized the door.",
        "Someone vandalised the door.",
    ],
)
def test_section_4_missing_police_report_requires_follow_up(statement: str) -> None:
    service = DemoService()
    claim = service.get_claim("clm-demo-001")
    claim.claimant_statement = statement

    assessment = service.assess_claim(claim.claim_id)

    finding = assessment.completeness_findings[0]
    assert finding.finding_code == "section_4_missing_police_report"
    assert finding.status == "missing"
    assert assessment.triage_recommendation == "request_information"
    assert assessment.recommended_actions[0].action_type == "request_missing_information"
    assert "police report and report number" in assessment.recommended_actions[0].rationale
    assert assessment.recommended_actions[0].human_review_required is True
    assert any(c.section.startswith("Section 4") for c in assessment.policy_citations)


@pytest.mark.parametrize("statement", ["Vehicle theft.", "Vandalism damaged the vehicle."])
def test_section_4_police_report_present(statement: str) -> None:
    service = DemoService()
    claim = service.get_claim("clm-demo-001")
    claim.claimant_statement = statement
    artifact = service.artifacts[claim.artifact_ids[0]]
    artifact.artifact_type = "police_report"

    assessment = service.assess_claim(claim.claim_id)

    assert assessment.completeness_findings == []
    assert assessment.human_review_required is True


@pytest.mark.parametrize("evidence", ["missing_reference", "other_claim", "unlinked_report"])
def test_section_4_unavailable_or_unrelated_police_report_does_not_clear_flag(
    evidence: str,
) -> None:
    service = DemoService()
    claim = service.get_claim("clm-demo-001")
    claim.claimant_statement = "The vehicle was stolen."
    report = Artifact(
        artifact_id="art-test-police",
        claim_id=claim.claim_id if evidence != "other_claim" else "clm-demo-002",
        artifact_type="police_report",
        received_at=datetime(2024, 9, 13, tzinfo=UTC),
    )
    if evidence != "missing_reference":
        service.artifacts[report.artifact_id] = report
    if evidence != "unlinked_report":
        claim.artifact_ids.append(report.artifact_id)

    assessment = service.assess_claim(claim.claim_id)

    assert any(
        f.finding_code == "section_4_missing_police_report"
        for f in assessment.completeness_findings
    )


def test_section_4_checks_are_independent_of_other_missing_fields() -> None:
    service = DemoService()
    claim = service.get_claim("clm-demo-001")
    claim.reported_driver_id = None
    claim.claimant_statement = "The vehicle was stolen."
    claim.reported_date = claim.loss_date + timedelta(days=31)

    assessment = service.assess_claim(claim.claim_id)

    assert {f.finding_code for f in assessment.completeness_findings} == {
        "required_information_missing",
        "section_4_late_reporting",
        "section_4_missing_police_report",
    }


def test_section_4_does_not_infer_reporting_date_from_artifact_receipt() -> None:
    service = DemoService()
    claim = service.get_claim("clm-demo-001")
    service.artifacts[claim.artifact_ids[0]].received_at = datetime(2025, 1, 1, tzinfo=UTC)

    assert service.assess_claim(claim.claim_id).completeness_findings == []
