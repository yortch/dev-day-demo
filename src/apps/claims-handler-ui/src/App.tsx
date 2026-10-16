import { useEffect, useState } from "react";
import type { Assessment, Claim, ResponseDraft, WorkflowResult } from "./types";
import type { Session } from "./auth";

function describeCode(code: string): string {
  return code.replaceAll("_", " ");
}

export default function App({ session }: { session: Session }) {
  const [claims, setClaims] = useState<Claim[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [result, setResult] = useState<{
    assessment: Assessment;
    responseDraft?: ResponseDraft;
  } | null>(null);
  const [loadingClaims, setLoadingClaims] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    session.request("/api/claims", { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(`Claims request failed (${response.status}).`);
        return (await response.json()) as Claim[];
      })
      .then((data) => {
        setClaims(data);
        setSelectedId(data[0]?.claimId ?? "");
      })
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        setError(reason instanceof Error ? reason.message : "Unable to load demo claims.");
      })
      .finally(() => setLoadingClaims(false));
    return () => controller.abort();
  }, [session]);

  async function runAssessment() {
    if (!selectedId) return;
    setRunning(true);
    setError("");
    setResult(null);
    try {
      const endpoint = session.mode === "local" ? "workflow" : "assess";
      const response = await session.request(`/api/claims/${selectedId}/${endpoint}`, {
        method: "POST",
      });
      if (!response.ok) {
        const detail = (await response.json()) as { detail?: string };
        throw new Error(detail.detail ?? `Assessment request failed (${response.status}).`);
      }
      if (session.mode === "local") {
        setResult((await response.json()) as WorkflowResult);
      } else {
        setResult({ assessment: (await response.json()) as Assessment });
      }
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Unable to run the assessment.");
    } finally {
      setRunning(false);
    }
  }

  const selectedClaim = claims.find((claim) => claim.claimId === selectedId);
  const assessment = result?.assessment;

  return (
    <main className="layout">
      <header className="hero">
        <div>
          <p className="eyebrow">Synthetic FNOL • Handler workspace</p>
          <h1>Claims review</h1>
          <p className="lede">
            Deterministic intake checks and policy references to support a person reviewing a
            synthetic claim.
          </p>
        </div>
        <span className="environment-badge">DEMO DATA ONLY</span>
      </header>

      <section className="notice" role="note">
        <strong>Human review is required.</strong> This demo does not determine coverage, approve
        or deny claims, or send customer communications.
      </section>

      <section className="panel intake" aria-labelledby="intake-heading">
        <div className="section-heading">
          <div>
            <p className="eyebrow">01 / Intake</p>
            <h2 id="intake-heading">Select a synthetic claim</h2>
          </div>
          <span className="count">{claims.length} fixtures</span>
        </div>

        {loadingClaims ? (
          <p className="muted">Loading synthetic claims…</p>
        ) : (
          <div className="form-row">
            <label htmlFor="claim-select">Claim fixture</label>
            <select
              id="claim-select"
              value={selectedId}
              onChange={(event) => {
                setSelectedId(event.target.value);
                setResult(null);
              }}
              disabled={!claims.length}
            >
              {claims.map((claim) => (
                <option key={claim.claimId} value={claim.claimId}>
                  {claim.claimId}
                </option>
              ))}
            </select>
            <button type="button" onClick={runAssessment} disabled={!selectedId || running}>
              {running ? "Reviewing…" : "Run handler checks"}
            </button>
          </div>
        )}

        {selectedClaim && (
          <dl className="claim-summary">
            <div>
              <dt>Policy reference</dt>
              <dd>{selectedClaim.policyId ?? "Not provided"}</dd>
            </div>
            <div>
              <dt>Loss date</dt>
              <dd>{selectedClaim.lossDate ?? "Not provided"}</dd>
            </div>
            <div>
              <dt>Reported date</dt>
              <dd>{selectedClaim.reportedDate ?? "Not provided"}</dd>
            </div>
            <div className="statement">
              <dt>Intake statement</dt>
              <dd>{selectedClaim.claimantStatement ?? "Not provided"}</dd>
            </div>
          </dl>
        )}
      </section>

      {error && (
        <section className="error" role="alert">
          <strong>Request could not be completed.</strong> {error}
        </section>
      )}

      {assessment && (
        <div className="results-grid">
          <section className="panel" aria-labelledby="assessment-heading">
            <div className="section-heading">
              <div>
                <p className="eyebrow">02 / Assessment</p>
                <h2 id="assessment-heading">Review summary</h2>
              </div>
              <span className="status-pill">{describeCode(assessment.triageRecommendation)}</span>
            </div>
            <p className="summary">{assessment.summary}</p>
            <div className="review-callout">
              <span aria-hidden="true">!</span>
              <p>
                <strong>Handler action required</strong>
                <br />
                Confirm the automated checks or follow up on the flagged information.
              </p>
            </div>
            <h3>Deterministic review flags</h3>
            {assessment.completenessFindings.length ? (
              <ul className="finding-list">
                {assessment.completenessFindings.map((finding, index) => (
                  <li key={`${finding.findingCode}-${index}`}>
                    <span className={`finding-status ${finding.status}`}>
                      {describeCode(finding.status)}
                    </span>
                    <div>
                      <strong>{describeCode(finding.findingCode)}</strong>
                      <p>{finding.description}</p>
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">No deterministic review flags were raised.</p>
            )}
            <h3>Confirm or follow up</h3>
            <ul>
              {assessment.recommendedActions.map((action) => (
                <li key={action.actionId}>{action.rationale}</li>
              ))}
            </ul>
          </section>

          <aside className="panel" aria-labelledby="evidence-heading">
            <p className="eyebrow">03 / Evidence</p>
            <h2 id="evidence-heading">Policy references</h2>
            <p className="muted">
              Retrieved excerpts help a handler review the file; they are not coverage decisions.
            </p>
            {assessment.policyCitations.length ? (
              assessment.policyCitations.map((citation) => (
                <article className="citation" key={citation.citationId}>
                  <h3>{citation.section}</h3>
                  <p>{citation.quotedText}</p>
                  <small>{citation.sourceDocument}</small>
                </article>
              ))
            ) : (
              <p className="empty-state">No matching policy section was retrieved.</p>
            )}
          </aside>

          {result.responseDraft ? <section className="panel draft" aria-labelledby="draft-heading">
            <p className="eyebrow">04 / Communication</p>
            <h2 id="draft-heading">Customer update draft</h2>
            <p>{result.responseDraft.body}</p>
            <span className="draft-warning">Draft only — human approval required before use.</span>
          </section> : <section className="notice" role="note">
            Azure data checks only. Foundry specialists and the Copilot Studio supervisor
            are not connected here; no customer communication was generated.
          </section>}
        </div>
      )}
      <footer>All names, IDs, policies, claims, and evidence in this demo are synthetic.</footer>
    </main>
  );
}
