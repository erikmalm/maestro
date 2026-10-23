import { sourceDate } from "../Evidence/useEvidence";
import Assessment from "./Assessment";
import EvidenceReview from "./EvidenceReview";

const number = (value) =>
  typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString(undefined, { maximumFractionDigits: 2 })
    : "Unknown";
const date = (value) => sourceDate(value) || "Unknown";
export const stateLabel = (state) =>
  ({
    queued: "Queued",
    running: "Running",
    partial: "Partial — no reviewed answer",
    clarification: "Clarification needed — no reviewed answer",
    cancelled: "Cancelled",
    interrupted: "Interrupted",
    failed: "Failed",
  })[state] || "Unknown";
const lookupLabels = {
  ready: "Saved-evidence lookup completed.",
  limited: "Saved-evidence lookup was incomplete.",
  disabled: "Source saving is disabled. Saved evidence was not inspected.",
  unconfigured: "No source archive is configured.",
  unavailable:
    "Saved evidence was unavailable. This is not an exhaustive no-match.",
};

export default function RunDetails({ run, busy, onCancel, onDelete }) {
  const requirements = ["requirements", "evidence_review"].includes(
    run.workflow,
  );
  const result = run.result;
  const candidates = result?.candidates || [];
  return (
    <section
      className="card research-detail"
      aria-label="Selected research run"
    >
      <div className="research-detail-heading">
        <div>
          <h2>Recorded research run</h2>
          <p role="status">{stateLabel(run.state)}</p>
        </div>
        <div className="research-actions">
          {["queued", "running"].includes(run.state) && (
            <button
              className="button secondary"
              disabled={Boolean(busy)}
              onClick={onCancel}
            >
              {busy === "cancel" ? "Cancelling…" : "Cancel run"}
            </button>
          )}
          <button
            className="button subtle"
            disabled={Boolean(busy)}
            onClick={onDelete}
          >
            {busy === "delete" ? "Deleting…" : "Delete run"}
          </button>
        </div>
      </div>
      <dl className="research-metadata">
        <div>
          <dt>Request</dt>
          <dd className="research-request">{run.request}</dd>
        </div>
        {!requirements && (
          <div>
            <dt>Saved-evidence keywords</dt>
            <dd>{run.query || "No keywords — bounded saved inventory"}</dd>
          </div>
        )}
        <div>
          <dt>Run ID</dt>
          <dd>
            <code>{run.id}</code>
          </dd>
        </div>
        <div>
          <dt>Updated</dt>
          <dd>{date(run.updated_at)}</dd>
        </div>
      </dl>
      {requirements && <Assessment assessment={run.assessment} />}
      {run.evidence_review && (
        <EvidenceReview
          review={run.evidence_review}
          assessment={run.assessment}
        />
      )}
      <section
        aria-label="Captured sharing permissions"
        className="research-section"
      >
        <h3>Captured sharing permissions</h3>
        <dl className="research-metadata research-permissions">
          <div>
            <dt>Sources</dt>
            <dd>
              {run.permissions.source_mode === "saved_only"
                ? "Saved evidence only"
                : "Unknown"}
            </dd>
          </div>
          <div>
            <dt>Hosted search permission</dt>
            <dd>
              {run.permissions.hosted_allowed ? "Permitted" : "Not permitted"}
            </dd>
          </div>
          <div>
            <dt>Personal memory recall</dt>
            <dd>
              {run.permissions.memory_recall ? "Permitted" : "Not permitted"}
            </dd>
          </div>
          <div>
            <dt>Conversation restrictions</dt>
            <dd>
              {run.permissions.memory_derived
                ? "Memory-derived conversation restrictions retained"
                : run.chat_id
                  ? "Originating conversation retained"
                  : "No conversation attached"}
            </dd>
          </div>
        </dl>
      </section>
      <section aria-label="Recorded activity" className="research-section">
        <h3>Recorded activity</h3>
        {run.events?.length ? (
          <ol className="research-events">
            {run.events.map((event) => (
              <li key={event.sequence}>
                <p>{event.summary}</p>
                <time dateTime={event.at}>{date(event.at)}</time>
              </li>
            ))}
          </ol>
        ) : (
          <p className="form-note">No recorded activity is available yet.</p>
        )}
      </section>
      {!requirements && (
        <section
          aria-label="Saved-version receipt"
          className="research-section"
        >
          <h3>Saved-version receipt</h3>
          {result ? (
            <>
              <p>
                {lookupLabels[result.lookup_state] ||
                  "Lookup outcome is unavailable."}
              </p>
              {result.summary && <p className="form-note">{result.summary}</p>}
              <p>
                {number(candidates.length)} available saved-version references.
              </p>
              {result.references_unavailable > 0 && (
                <p className="form-note">
                  {number(result.references_unavailable)} previously recorded{" "}
                  {result.references_unavailable === 1
                    ? "reference is"
                    : "references are"}{" "}
                  no longer available.
                </p>
              )}
              {(result.search_limited || result.skipped_count > 0) && (
                <p className="form-note">
                  Results may be incomplete. {number(result.skipped_count)}{" "}
                  records were skipped.
                </p>
              )}
              {candidates.map((candidate) => (
                <details
                  className="research-candidate"
                  key={candidate.capture_id}
                >
                  <summary>
                    Saved version <code>{candidate.capture_id}</code>
                  </summary>
                  <dl className="research-metadata">
                    <div>
                      <dt>Retrieved</dt>
                      <dd>{date(candidate.retrieved_at)}</dd>
                    </div>
                    <div>
                      <dt>Reuse-window flag at lookup</dt>
                      <dd>
                        {candidate.stale
                          ? "Outside reuse window"
                          : "Within reuse window"}
                      </dd>
                    </div>
                    <div>
                      <dt>Content hash</dt>
                      <dd>
                        <code>{candidate.content_hash}</code>
                      </dd>
                    </div>
                    <div>
                      <dt>Manifest hash</dt>
                      <dd>
                        <code>{candidate.manifest_hash}</code>
                      </dd>
                    </div>
                  </dl>
                </details>
              ))}
              <p className="form-note">
                These are saved-version candidates, not accepted evidence.
                Retrieval time and the reuse-window flag do not establish that
                facts are current.
              </p>
            </>
          ) : (
            <p className="form-note">No lookup receipt has been recorded.</p>
          )}
        </section>
      )}
      {run.budget && (
        <section aria-label="Shared allowance" className="research-section">
          <h3>Shared allowance</h3>
          <div className="research-table-scroll">
            <table className="research-budget">
              <caption>Recorded shared allowance</caption>
              <thead>
                <tr>
                  <th scope="col">Resource</th>
                  {["Total", "Consumed", "Reserved", "Remaining"].map(
                    (label) => (
                      <th scope="col" key={label}>
                        {label}
                      </th>
                    ),
                  )}
                </tr>
              </thead>
              <tbody>
                {[
                  ["model_calls", "Model calls"],
                  ["tokens", "Tokens"],
                  ["seconds", "Time (seconds)"],
                  ["estimated_model_usd", "Estimated model USD"],
                  ["hosted_searches", "Hosted searches"],
                ].map(([key, label]) => (
                  <tr key={key}>
                    <th scope="row">{label}</th>
                    {["total", "consumed", "reserved", "remaining"].map(
                      (field) => (
                        <td key={field}>{number(run.budget[field]?.[key])}</td>
                      ),
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="form-note">
            Deadline: {date(run.budget.deadline)}. Daily hosted allowance
            remaining at submission:{" "}
            {number(run.budget.daily_search?.remaining)}. Model spend excludes
            hardware and electricity.
          </p>
        </section>
      )}
    </section>
  );
}
