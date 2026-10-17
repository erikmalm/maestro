import useEvidence from "./useEvidence";
import SourceTable from "./SourceTable";
import SourceDetail from "./SourceDetail";
import "./styles.css";

const stateMessages = {
  disabled: "Source saving is disabled.",
  unconfigured: "No source archive is configured.",
  unavailable: "Source archive is unavailable.",
};

export default function Evidence() {
  const data = useEvidence();
  const { inventory, loading, refreshing, error, selected, detail } = data;
  return (
    <main className="page-content evidence-page">
      <div className="page-heading">
        <div>
          <h1>Evidence</h1>
          <p>
            Inspect saved source versions. Opening saved text verifies that
            version.
          </p>
        </div>
      </div>
      <p className="page-note">
        Browsing saved evidence uses no searches. Retrieval time does not
        establish when its facts were current.
      </p>
      {loading && <p role="status">Loading saved sources…</p>}
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      {inventory && inventory.state !== "ready" && (
        <p role="status">
          {inventory.message || stateMessages[inventory.state]}
        </p>
      )}
      {inventory?.state === "ready" && (
        <>
          {inventory.sources.length ? (
            <SourceTable
              sources={inventory.sources}
              loading={loading}
              selected={selected}
              onOpen={data.openSource}
            />
          ) : (
            <p role="status">
              {inventory.skipped_count > 0 || inventory.next_cursor
                ? "No available sources in this page."
                : "No saved sources yet."}
            </p>
          )}
          {inventory.skipped_count > 0 && (
            <p className="page-note">
              {inventory.skipped_count.toLocaleString()} unavailable or
              ineligible records were skipped. This is a bounded view of indexed
              metadata.
            </p>
          )}
          {inventory.next_cursor && (
            <button
              className="button secondary evidence-more"
              disabled={loading}
              aria-disabled={refreshing || undefined}
              onClick={data.loadMore}
            >
              Load more sources
            </button>
          )}
        </>
      )}
      {selected && (
        <SourceDetail
          selected={selected}
          detail={detail}
          onClose={data.closeSource}
        />
      )}
    </main>
  );
}
