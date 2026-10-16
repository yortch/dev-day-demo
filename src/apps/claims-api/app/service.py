from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from jsonschema import Draft202012Validator, FormatChecker

from .models import (
    Artifact,
    Assessment,
    Citation,
    Claim,
    Finding,
    Policy,
    RecommendedAction,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
DATA_ROOT = REPOSITORY_ROOT / "data"
SCHEMA_ROOT = DATA_ROOT / "schemas"
DEFAULT_REVIEW_THRESHOLD_USD = 5000

SYNONYMS = {
    "deer": "animal",
    "struck": "impact",
    "hit": "impact",
    "crash": "collision",
    "delivery": "commercial",
    "ride-share": "commercial",
}
STOP_WORDS = {"a", "an", "and", "at", "for", "i", "in", "is", "of", "on", "the", "to", "with"}


class ClaimsStore(Protocol):
    def list_claims(self) -> list[Claim]: ...
    def find_claim(self, claim_id: str) -> Claim | None: ...
    def find_policy(self, policy_id: str) -> Policy | None: ...
    def find_artifact(self, artifact_id: str) -> Artifact | None: ...


class PolicyKnowledge(Protocol):
    def lookup(self, policy_id: str, query: str) -> list[Citation]: ...


class DemoServiceError(Exception):
    """Base exception for expected demo-service failures."""


class ResourceNotFound(DemoServiceError):
    """Raised when a requested synthetic resource does not exist."""


class DemoService:
    def __init__(
        self,
        data_root: Path = DATA_ROOT,
        review_threshold_usd: float = DEFAULT_REVIEW_THRESHOLD_USD,
        *,
        store: ClaimsStore | None = None,
        knowledge: PolicyKnowledge | None = None,
    ) -> None:
        if (store is None) != (knowledge is None):
            raise ValueError("Cloud data and knowledge must be configured together.")
        self.store = store
        self.knowledge = knowledge
        self.data_root = data_root
        self.review_threshold_usd = review_threshold_usd
        if store is not None:
            return
        self.claims = {
            claim.claim_id: claim
            for claim in self._load_records("claims/demo-claims.json", "claim.schema.json", Claim)
        }
        self.policies = {
            policy.policy_id: policy
            for policy in self._load_records(
                "policies/demo-auto-policy.json", "policy.schema.json", Policy
            )
        }
        self.artifacts = {
            artifact.artifact_id: artifact
            for artifact in self._load_records(
                "artifacts/demo-artifacts.json", "claim-artifact.schema.json", Artifact
            )
        }
        self.policy_text = {
            policy_id: self._load_policy_text(policy) for policy_id, policy in self.policies.items()
        }

    def _load_records(self, relative_path: str, schema_name: str, model: type[Any]) -> list[Any]:
        path = self.data_root / relative_path
        schema = json.loads((SCHEMA_ROOT / schema_name).read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        records = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(records, dict):
            records = [records]
        for index, record in enumerate(records):
            errors = sorted(validator.iter_errors(record), key=lambda error: list(error.path))
            if errors:
                details = "; ".join(error.message for error in errors)
                raise ValueError(f"Invalid fixture {path}[{index}]: {details}")
        return [model.model_validate(record) for record in records]

    def _load_policy_text(self, policy: Policy) -> str:
        source_ref = policy.source_document_refs[0]
        path = self.data_root.parent / source_ref
        if not path.is_file():
            raise ValueError(f"Policy source document does not exist: {source_ref}")
        return path.read_text(encoding="utf-8")

    def list_claims(self) -> list[Claim]:
        if self.store is not None:
            return self.store.list_claims()
        return list(self.claims.values())

    def get_claim(self, claim_id: str) -> Claim:
        if self.store is not None:
            claim = self.store.find_claim(claim_id)
            if claim is None:
                raise ResourceNotFound(f"Claim '{claim_id}' was not found.")
            return claim
        try:
            return self.claims[claim_id]
        except KeyError as error:
            raise ResourceNotFound(f"Claim '{claim_id}' was not found.") from error

    def get_policy(self, policy_id: str) -> Policy:
        if self.store is not None:
            policy = self.store.find_policy(policy_id)
            if policy is None:
                raise ResourceNotFound(f"Policy '{policy_id}' was not found.")
            return policy
        try:
            return self.policies[policy_id]
        except KeyError as error:
            raise ResourceNotFound(f"Policy '{policy_id}' was not found.") from error

    def lookup_policy(self, policy_id: str, query: str) -> list[Citation]:
        policy = self.get_policy(policy_id)
        if not query.strip():
            raise ValueError("Policy lookup query must not be empty.")
        if self.knowledge is not None:
            return self.knowledge.lookup(policy_id, query)

        sections = re.findall(
            r"^## (.+?)\n(.*?)(?=^## |\Z)",
            self.policy_text[policy_id],
            flags=re.MULTILINE | re.DOTALL,
        )
        query_tokens = self._tokens(query)
        ranked: list[tuple[int, int, str, str]] = []
        for position, (title, body) in enumerate(sections):
            section_tokens = self._tokens(f"{title} {body}")
            score = len(query_tokens & section_tokens)
            if score:
                ranked.append((score, position, title, body.strip()))

        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [
            Citation(
                citation_id=f"cit-{policy_id.removeprefix('pol-')}-{self._section_slug(title)}",
                policy_id=policy.policy_id,
                source_document=policy.source_document_refs[0],
                section=title,
                quoted_text=body[:4000],
            )
            for _, _, title, body in ranked[:3]
        ]

    def _tokens(self, value: str) -> set[str]:
        tokens = set(re.findall(r"[a-z0-9]+", value.lower()))
        normalized = {SYNONYMS.get(token, token) for token in tokens}
        return normalized - STOP_WORDS

    def _section_slug(self, title: str) -> str:
        match = re.search(r"section\s+(\d+(?:\.\d+)*)", title, flags=re.IGNORECASE)
        if match:
            return match.group(1).replace(".", "-")
        return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40]

    def assess_claim(self, claim_id: str) -> Assessment:
        claim = self.get_claim(claim_id)
        findings: list[Finding] = []
        missing_fields = [
            name
            for name, value in (
                ("policy reference", claim.policy_id),
                ("loss date", claim.loss_date),
                ("reported date", claim.reported_date),
                ("vehicle", claim.vehicle_id),
                ("reporting driver", claim.reported_driver_id),
                ("claimant statement", claim.claimant_statement),
            )
            if value is None
        ]
        if missing_fields:
            findings.append(
                Finding(
                    finding_code="required_information_missing",
                    status="missing",
                    description="Missing required intake information: "
                    + ", ".join(missing_fields)
                    + ".",
                )
            )
        if not claim.artifact_ids:
            findings.append(
                Finding(
                    finding_code="required_document_missing",
                    status="missing",
                    description="At least one supporting artifact is required for this demo.",
                )
            )

        policy: Policy | None = None
        if claim.policy_id:
            try:
                policy = self.get_policy(claim.policy_id)
            except ResourceNotFound:
                pass
        artifacts = {
            artifact_id: (
                self.store.find_artifact(artifact_id)
                if self.store
                else self.artifacts.get(artifact_id)
            )
            for artifact_id in claim.artifact_ids
        }
        process_findings = self._claims_process_findings(claim, artifacts)
        findings.extend(process_findings)
        policy_citations: list[Citation] = []
        policy_basis = "unavailable" if claim.policy_id and policy is None else "not_assessed"
        if policy and claim.loss_date and claim.claimant_statement:
            policy_basis = "not_assessed"
            if (
                policy.status != "active"
                or not policy.effective_date <= claim.loss_date <= policy.expiration_date
            ):
                findings.append(
                    Finding(
                        finding_code="policy_inactive_on_loss_date",
                        status="needs_review",
                        description="The policy status or date range needs handler verification.",
                    )
                )
            if (
                claim.reported_driver_id
                and claim.reported_driver_id not in policy.listed_driver_ids
            ):
                findings.append(
                    Finding(
                        finding_code="driver_not_listed",
                        status="needs_review",
                        description=(
                            "The reporting driver is not listed in the synthetic policy fixture."
                        ),
                    )
                )
            if claim.vehicle_id and claim.vehicle_id not in policy.covered_vehicle_ids:
                findings.append(
                    Finding(
                        finding_code="other",
                        status="needs_review",
                        description=(
                            "The vehicle reference does not match the synthetic policy fixture."
                        ),
                    )
                )
            policy_citations = self.lookup_policy(policy.policy_id, claim.claimant_statement)
            if policy_citations:
                policy_basis = "grounded"
        if policy and (process_findings or (claim.loss_date and claim.reported_date)):
            process_citations = self.lookup_policy(
                policy.policy_id, "Section 4 claims process report 30 days theft vandalism police"
            )
            for citation in process_citations:
                if re.match(r"^Section 4\b", citation.section, flags=re.IGNORECASE) and not any(
                    existing.citation_id == citation.citation_id for existing in policy_citations
                ):
                    policy_citations.append(citation)
            if policy_citations:
                policy_basis = "grounded"
        if (
            claim.repair_estimate
            and claim.repair_estimate.currency == "USD"
            and claim.repair_estimate.amount > self.review_threshold_usd
        ):
            related = [
                artifact_id for artifact_id, artifact in artifacts.items() if artifact is not None
            ]
            findings.append(
                Finding(
                    finding_code="repair_estimate_above_review_threshold",
                    status="needs_review",
                    description=(
                        f"The estimate exceeds the synthetic USD {self.review_threshold_usd:,.0f} "
                        "handler-review threshold. This is not a coverage rule."
                    ),
                    related_artifact_ids=related,
                )
            )
        for artifact_id in claim.artifact_ids:
            artifact = artifacts.get(artifact_id)
            if artifact and artifact.duplicate_of_artifact_id:
                findings.append(
                    Finding(
                        finding_code="possible_duplicate_artifact",
                        status="needs_review",
                        description=(
                            "An artifact is marked as a possible duplicate for handler review."
                        ),
                        related_artifact_ids=[artifact_id],
                    )
                )

        actions = self._recommended_actions(findings, policy_citations)
        if not actions:
            actions.append(
                RecommendedAction(
                    action_id="act-human-review",
                    action_type="route_to_human_adjuster",
                    rationale="All generated assessments remain subject to handler review.",
                )
            )
        triage = (
            "request_information"
            if any(finding.status == "missing" for finding in findings)
            else "human_review"
        )
        finding_summary = (
            f"{len(findings)} deterministic review flag(s) require attention."
            if findings
            else "No deterministic completeness or relationship flags were raised."
        )
        return Assessment(
            assessment_id=f"asm-{claim.claim_id.removeprefix('clm-')}",
            claim_id=claim.claim_id,
            generated_at=datetime.now(UTC),
            summary=(
                f"{finding_summary} Relevant policy wording is evidence for handler review, "
                "not a coverage decision."
            ),
            completeness_findings=findings,
            conflicts=[],
            policy_basis=policy_basis,
            policy_citations=policy_citations,
            recommended_actions=actions,
            triage_recommendation=triage,
            human_review_required=True,
        )

    def _claims_process_findings(
        self, claim: Claim, artifacts: dict[str, Artifact | None]
    ) -> list[Finding]:
        findings: list[Finding] = []
        if claim.loss_date is not None and claim.reported_date is not None:
            days_after_loss = (claim.reported_date - claim.loss_date).days
            if days_after_loss > 30:
                findings.append(
                    Finding(
                        finding_code="section_4_late_reporting",
                        status="needs_review",
                        description=(
                            f"Claim reported {days_after_loss} days after loss "
                            f"({claim.loss_date} to {claim.reported_date}), exceeding the "
                            "Section 4 limit of 30 days. Handler confirmation is required."
                        ),
                    )
                )
            elif days_after_loss < 0:
                findings.append(
                    Finding(
                        finding_code="section_4_reporting_dates_inconsistent",
                        status="inconsistent",
                        description=(
                            "The reported date precedes the loss date. Confirm the dates "
                            "before checking the Section 4 reporting deadline."
                        ),
                    )
                )
        if re.search(
            r"\b(theft|stolen|vandalism|vandali[sz]ed|vandali[sz]ation)\b",
            claim.claimant_statement or "",
            flags=re.IGNORECASE,
        ) and not any(
            artifact is not None
            and artifact.claim_id == claim.claim_id
            and artifact.artifact_type == "police_report"
            for artifact in artifacts.values()
        ):
            findings.append(
                Finding(
                    finding_code="section_4_missing_police_report",
                    status="missing",
                    description=(
                        "The statement indicates possible theft or vandalism, but no police "
                        "report artifact is available for this claim. Section 4 requires a "
                        "police report and report number; follow up with the claimant."
                    ),
                )
            )
        return findings

    def _recommended_actions(
        self, findings: list[Finding], citations: list[Citation]
    ) -> list[RecommendedAction]:
        action_map = {
            "required_information_missing": (
                "request_missing_information",
                "Request the missing intake fields before handler assessment.",
            ),
            "required_document_missing": (
                "request_missing_information",
                "Request at least one supporting artifact for the synthetic intake.",
            ),
            "policy_inactive_on_loss_date": (
                "verify_policy_status",
                "Have a handler verify policy status and dates; no coverage result is inferred.",
            ),
            "driver_not_listed": (
                "verify_driver_listing",
                "Have a handler verify the driver information against the complete policy record.",
            ),
            "section_4_late_reporting": (
                "review_conflicting_information",
                "Confirm the loss and reported dates and follow up on the Section 4 "
                "late-reporting flag; do not infer a coverage decision.",
            ),
            "section_4_reporting_dates_inconsistent": (
                "review_conflicting_information",
                "Confirm or correct the loss and reported dates before reviewing the "
                "Section 4 reporting deadline.",
            ),
            "section_4_missing_police_report": (
                "request_missing_information",
                "Confirm whether theft or vandalism applies and request the missing "
                "police report and report number for handler review under Section 4.",
            ),
            "repair_estimate_above_review_threshold": (
                "review_repair_estimate",
                "Route the estimate for handler review; the configured threshold is demo-only.",
            ),
            "possible_duplicate_artifact": (
                "investigate_possible_duplicate_artifact",
                "Have a handler compare the flagged artifact with its referenced original.",
            ),
            "other": (
                "route_to_human_adjuster",
                "Have a handler resolve the fixture relationship before proceeding.",
            ),
        }
        actions: list[RecommendedAction] = []
        for index, finding in enumerate(findings, start=1):
            details = action_map.get(finding.finding_code)
            if details is None:
                continue
            action_type, rationale = details
            actions.append(
                RecommendedAction(
                    action_id=f"act-review-{index}",
                    action_type=action_type,
                    rationale=rationale,
                    supporting_citation_ids=[citation.citation_id for citation in citations],
                )
            )
        return actions

    def create_response_draft(self, assessment: Assessment) -> dict[str, object]:
        if assessment.triage_recommendation == "request_information":
            body = (
                "Thank you for providing the initial information. A claims handler will review "
                "your submission and may request additional details or documents. This draft "
                "must be checked and approved by a person before use."
            )
        else:
            body = (
                "Thank you for your submission. A claims handler will review the information "
                "and relevant policy materials and determine any next steps. This draft must "
                "be checked and approved by a person before use."
            )
        return {
            "draftId": f"drf-{assessment.claim_id.removeprefix('clm-')}",
            "claimId": assessment.claim_id,
            "assessmentId": assessment.assessment_id,
            "body": body,
            "humanReviewRequired": True,
        }
