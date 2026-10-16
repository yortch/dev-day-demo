import json
from datetime import timedelta

from app import main
from app.main import app
from app.service import SCHEMA_ROOT, DemoService
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


def test_claim_list_and_policy_lookup() -> None:
    with TestClient(app) as client:
        claims = client.get("/api/claims")
        citations = client.get(
            "/api/policies/pol-demo-auto/lookup",
            params={"query": "deer strike"},
        )

    assert claims.status_code == 200
    assert len(claims.json()) == 3
    assert citations.status_code == 200
    assert citations.json()[0]["section"].startswith("Section 2.3")


def test_workflow_returns_human_review_assessment_and_draft() -> None:
    with TestClient(app) as client:
        response = client.post("/api/claims/clm-demo-001/workflow")

    assert response.status_code == 200
    result = response.json()
    assert result["assessment"]["humanReviewRequired"] is True
    assert result["responseDraft"]["humanReviewRequired"] is True
    assert "not a coverage decision" in result["assessment"]["summary"]


def test_unknown_claim_returns_404() -> None:
    with TestClient(app) as client:
        response = client.post("/api/claims/clm-unknown/assess")

    assert response.status_code == 404


def test_empty_lookup_query_is_rejected() -> None:
    with TestClient(app) as client:
        response = client.get(
            "/api/policies/pol-demo-auto/lookup",
            params={"query": " "},
        )

    assert response.status_code == 422


def test_section_4_flags_and_follow_up_reach_the_handler(monkeypatch) -> None:
    service = DemoService()
    claim = service.get_claim("clm-demo-001")
    claim.reported_date = claim.loss_date + timedelta(days=31)
    claim.claimant_statement = "The vehicle was stolen."
    monkeypatch.setattr(main, "service", service)

    with TestClient(app) as client:
        response = client.post(f"/api/claims/{claim.claim_id}/assess")

    assert response.status_code == 200
    assessment = response.json()
    assert {f["findingCode"] for f in assessment["completenessFindings"]} == {
        "section_4_late_reporting",
        "section_4_missing_police_report",
    }
    assert assessment["triageRecommendation"] == "request_information"
    assert assessment["humanReviewRequired"] is True
    assert all(action["humanReviewRequired"] for action in assessment["recommendedActions"])
    assert any(c["section"].startswith("Section 4") for c in assessment["policyCitations"])

    schemas = [
        json.loads(path.read_text(encoding="utf-8")) for path in SCHEMA_ROOT.glob("*.schema.json")
    ]
    registry = Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema)) for schema in schemas
    )
    schema = json.loads(
        (SCHEMA_ROOT / "claim-assessment.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema, registry=registry, format_checker=FormatChecker()).validate(
        assessment
    )
