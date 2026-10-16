from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class DemoModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class RepairEstimate(DemoModel):
    amount: float = Field(ge=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class Claim(DemoModel):
    claim_id: str = Field(pattern=r"^clm-[a-z0-9-]+$")
    customer_id: str | None = Field(default=None, pattern=r"^cust-[a-z0-9-]+$")
    policy_id: str | None = Field(default=None, pattern=r"^pol-[a-z0-9-]+$")
    vehicle_id: str | None = Field(default=None, pattern=r"^veh-[a-z0-9-]+$")
    loss_date: date | None = None
    reported_date: date | None = None
    reported_driver_id: str | None = Field(default=None, pattern=r"^drv-[a-z0-9-]+$")
    claimant_statement: str | None = Field(default=None, min_length=1, max_length=10000)
    artifact_ids: list[str] = Field(default_factory=list)
    repair_estimate: RepairEstimate | None = None


class Coverage(DemoModel):
    coverage_id: str
    description: str = Field(min_length=1, max_length=2000)


class Policy(DemoModel):
    policy_id: str = Field(pattern=r"^pol-[a-z0-9-]+$")
    insured_customer_id: str = Field(pattern=r"^cust-[a-z0-9-]+$")
    status: Literal["active", "inactive"]
    effective_date: date
    expiration_date: date
    covered_vehicle_ids: list[str]
    listed_driver_ids: list[str]
    coverages: list[Coverage] = Field(min_length=1)
    exclusions: list[str] = Field(default_factory=list)
    source_document_refs: list[str] = Field(default_factory=list)


class Artifact(DemoModel):
    artifact_id: str = Field(pattern=r"^art-[a-z0-9-]+$")
    claim_id: str = Field(pattern=r"^clm-[a-z0-9-]+$")
    artifact_type: Literal[
        "claimant_statement",
        "policy_document",
        "repair_estimate",
        "damage_photo",
        "police_report",
        "other",
    ]
    received_at: datetime
    content_ref: str | None = None
    duplicate_of_artifact_id: str | None = None
    description: str | None = Field(default=None, max_length=1000)


class Finding(DemoModel):
    finding_code: Literal[
        "required_information_missing",
        "required_document_missing",
        "policy_inactive_on_loss_date",
        "driver_not_listed",
        "section_4_late_reporting",
        "section_4_reporting_dates_inconsistent",
        "section_4_missing_police_report",
        "repair_estimate_above_review_threshold",
        "possible_duplicate_artifact",
        "possible_prior_damage",
        "straight_through_candidate",
        "human_escalation",
        "other",
    ]
    status: Literal["clear", "missing", "inconsistent", "needs_review", "unknown"]
    description: str = Field(min_length=1, max_length=2000)
    related_artifact_ids: list[str] = Field(default_factory=list)


class Citation(DemoModel):
    citation_id: str = Field(pattern=r"^cit-[a-z0-9-]+$")
    policy_id: str = Field(pattern=r"^pol-[a-z0-9-]+$")
    source_document: str = Field(min_length=1)
    section: str = Field(min_length=1, max_length=300)
    quoted_text: str = Field(min_length=1, max_length=4000)


class RecommendedAction(DemoModel):
    action_id: str = Field(pattern=r"^act-[a-z0-9-]+$")
    action_type: Literal[
        "request_missing_information",
        "verify_policy_status",
        "verify_driver_listing",
        "review_conflicting_information",
        "review_repair_estimate",
        "review_possible_prior_damage",
        "investigate_possible_duplicate_artifact",
        "route_to_human_adjuster",
        "prepare_customer_update",
    ]
    priority: Literal["low", "normal", "high"] = "normal"
    rationale: str = Field(min_length=1, max_length=2000)
    supporting_citation_ids: list[str] = Field(default_factory=list)
    human_review_required: Literal[True] = True


class Assessment(DemoModel):
    assessment_id: str = Field(pattern=r"^asm-[a-z0-9-]+$")
    claim_id: str = Field(pattern=r"^clm-[a-z0-9-]+$")
    generated_at: datetime
    summary: str = Field(min_length=1, max_length=4000)
    completeness_findings: list[Finding]
    conflicts: list[dict[str, object]]
    policy_basis: Literal["grounded", "unavailable", "not_assessed"]
    policy_citations: list[Citation]
    recommended_actions: list[RecommendedAction]
    triage_recommendation: Literal[
        "request_information",
        "human_review",
        "escalate",
        "straight_through_candidate",
    ]
    human_review_required: Literal[True] = True


class ResponseDraft(DemoModel):
    draft_id: str = Field(pattern=r"^drf-[a-z0-9-]+$")
    claim_id: str = Field(pattern=r"^clm-[a-z0-9-]+$")
    assessment_id: str = Field(pattern=r"^asm-[a-z0-9-]+$")
    body: str = Field(min_length=1, max_length=10000)
    human_review_required: Literal[True] = True


class WorkflowResult(DemoModel):
    assessment: Assessment
    response_draft: ResponseDraft
