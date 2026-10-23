import { useState } from "react";
import useResearchRuns from "./useResearchRuns";
import RunDetails, { stateLabel } from "./RunDetails";
import "./styles.css";

export { researchPageRequested } from "./useResearchRuns";

export default function Research({ chatId }) {
  const data = useResearchRuns(chatId);
  const [request, setRequest] = useState("");
  return (
    <main className="page-content research-page">
      <div className="page-heading">
        <div>
          <h1>Research</h1>
          <p>Assess requirements and review saved evidence.</p>
        </div>
        <span className="badge neutral">Preview</span>
      </div>
      <p className="page-note">
        This preview assesses your request, then reviews a bounded selection of
        saved evidence with an installed local model. It makes no hosted
        searches or personal-memory recall, and produces no written or finally
        reviewed answer.
      </p>
      <form
        className="card entry-form research-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (request.trim() && !data.pendingId)
            void data.submit(request.trim());
        }}
      >
        <h2>Review saved evidence</h2>
        <label>
          Research request
          <textarea
            required
            maxLength={32000}
            rows={4}
            value={request}
            disabled={Boolean(data.busy || data.pendingId)}
            onChange={(event) => setRequest(event.target.value)}
          />
        </label>
        <p className="form-note">
          The current conversation's sharing restrictions are retained. Material
          ambiguity stops the run for clarification. Answer writing and final
          review are later steps.
        </p>
        <div className="research-actions">
          <button
            className="button primary"
            disabled={!request.trim() || Boolean(data.busy || data.pendingId)}
          >
            {data.busy === "submit"
              ? "Recording request…"
              : "Review saved evidence"}
          </button>
        </div>
        {data.pendingId && !data.busy && (
          <div className="research-pending" role="status">
            <p>
              An earlier submission may already have been recorded. Recent runs
              are checked automatically.
            </p>
            <div className="research-actions">
              {data.canRetry && (
                <button
                  type="button"
                  className="button secondary"
                  onClick={data.retry}
                >
                  Retry same submission
                </button>
              )}
              <button
                type="button"
                className="button subtle"
                onClick={data.newSubmission}
              >
                Start a new submission
              </button>
            </div>
          </div>
        )}
      </form>
      {(data.readError || data.actionError) && (
        <p className="error-banner" role="alert">
          {data.actionError || data.readError}
        </p>
      )}
      {data.notice && (
        <p role="status" className="research-notice">
          {data.notice}
        </p>
      )}
      <div className="research-columns">
        <section
          className="card research-history"
          aria-label="Recent research runs"
        >
          <h2>Recent runs</h2>
          {data.loading && <p role="status">Loading research runs…</p>}
          {!data.loading && !data.runs.length && (
            <p className="form-note">No recorded runs are available.</p>
          )}
          <ul>
            {data.runs.map((run) => (
              <li key={run.id}>
                <button
                  disabled={Boolean(data.busy)}
                  aria-pressed={run.id === data.selectedId}
                  onClick={() => data.select(run)}
                >
                  <span>
                    {run.request.length > 100
                      ? run.request.slice(0, 100) + "…"
                      : run.request}
                  </span>
                  <small>{stateLabel(run.state)}</small>
                </button>
              </li>
            ))}
          </ul>
          {data.skipped > 0 && (
            <p className="form-note">
              {data.skipped} unavailable records were omitted.
            </p>
          )}
        </section>
        <div>
          {data.detailLoading && !data.selected && (
            <p role="status">Loading recorded activity…</p>
          )}
          {data.selected && (
            <RunDetails
              run={data.selected}
              busy={data.busy}
              onCancel={data.cancel}
              onDelete={data.remove}
            />
          )}
          {!data.detailLoading && !data.selected && (
            <p className="page-note">
              Select a recorded run to inspect its activity.
            </p>
          )}
        </div>
      </div>
    </main>
  );
}
