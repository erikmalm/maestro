import useResearchRuns from "./useResearchRuns";
import RunDetails, { stateLabel } from "./RunDetails";
import "./styles.css";

export { researchPageRequested } from "./useResearchRuns";

export default function Research({ chatId }) {
  const data = useResearchRuns(chatId);
  return (
    <main className="page-content research-page">
      <div className="page-heading">
        <div>
          <h1>Research</h1>
          <p>Write an unreviewed draft from assessed saved evidence.</p>
        </div>
        <span className="badge neutral">Preview</span>
      </div>
      <p className="page-note">
        This preview assesses your requirements, reviews a bounded selection of
        saved evidence, and writes a cited draft with an installed local model.
        It makes no hosted searches or personal-memory recall. The draft has not
        received final review.
      </p>
      <form
        className="card entry-form research-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (data.request.trim() && !data.formLocked)
            void data.submit(data.request.trim());
        }}
      >
        <h2>Write saved-evidence draft</h2>
        <label>
          Research request
          <textarea
            required
            maxLength={32000}
            rows={4}
            value={data.request}
            disabled={data.formLocked}
            onChange={(event) => data.updateRequest(event.target.value)}
          />
        </label>
        <p className="form-note">
          The current conversation's sharing restrictions are retained. Material
          ambiguity stops the run for clarification. Missing or unsuitable
          evidence remains explicit in the draft. Final review is a later step.
        </p>
        <div className="research-actions">
          <button
            className="button primary"
            disabled={!data.request.trim() || data.formLocked}
            aria-busy={data.busy === "submit" || data.working}
          >
            Write saved-evidence draft
          </button>
        </div>
        {data.working && (
          <p className="form-note" role="status">
            Research is running. The request field will unlock when it finishes.
          </p>
        )}
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
