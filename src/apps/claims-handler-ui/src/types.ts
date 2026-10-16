export interface Claim {
  claimId: string;
  policyId?: string;
  lossDate?: string;
  reportedDate?: string;
  claimantStatement?: string;
  repairEstimate?: { amount: number; currency: string };
}

export interface Finding {
  findingCode: string;
  status: "clear" | "missing" | "inconsistent" | "needs_review" | "unknown";
  description: string;
}

export interface Citation {
  citationId: string;
  sourceDocument: string;
  section: string;
  quotedText: string;
}

export interface Assessment {
  assessmentId: string;
  claimId: string;
  summary: string;
  completenessFindings: Finding[];
  recommendedActions: {
    actionId: string;
    rationale: string;
    humanReviewRequired: true;
  }[];
  policyCitations: Citation[];
  policyBasis: "grounded" | "unavailable" | "not_assessed";
  triageRecommendation: string;
  humanReviewRequired: true;
}

export interface ResponseDraft {
  body: string;
  humanReviewRequired: true;
}

export interface WorkflowResult {
  assessment: Assessment;
  responseDraft: ResponseDraft;
}
