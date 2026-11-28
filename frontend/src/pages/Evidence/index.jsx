import { useRef } from "react";
import useEvidence from "./useEvidence";
import SourceTable from "./SourceTable";
import SourceDetail from "./SourceDetail";
import ArchiveSettings from "../ArchiveSettings";
import "./styles.css";

const stateMessages = {
  disabled: "Source saving is disabled.",
  unconfigured: "No source archive is configured.",
  unavailable: "Source archive is unavailable.",
};

export default function Evidence({ archive, onOpenSettings }) {
  const page = useRef(null);
  const data = useEvidence((source) => {
    const opener =
      source &&
      page.current?.querySelector(
        `button[data-capture-id="${CSS.escape(source.capture_id)}"]`,
      );
    const target =
      opener &&
      !opener.disabled &&
      opener.getAttribute("aria-disabled") !== "true"
        ? opener
        : page.current?.querySelector("h1");
    target?.focus();
  });
  const {
    inventory,
    loading,
    refreshing,
    error,
    selected,
    detail,
    filters,
    appliedFilters,
  } = data;
  const searchIncomplete =
    appliedFilters &&
    (inventory?.search_limited ||
      inventory?.state === "limited" ||
      inventory?.skipped_count > 0);
  return (
    <main className="page-content evidence-page" ref={page}>
      <div className="page-heading">
        <div>
          <h1 tabIndex={-1}>Evidence</h1>
          <p>
            Inspect saved source versions. Opening saved text verifies that
            version.
          </p>
        </div>
      </div>
      <p className="page-note">
        Searching saved evidence uses no web searches or model calls. Retrieval
        time does not establish when its facts were current.
      </p>
      <ArchiveSettings data={archive} onOpenSettings={onOpenSettings} />
      <form
        className="card entry-form evidence-search"
        aria-label="Search saved evidence"
        onSubmit={(event) => {
          event.preventDefault();
          data.search();
        }}
      >
        <div className="evidence-filters">
          <label>
            Keywords
            <input
              type="search"
              maxLength={512}
              value={filters.query}
              onChange={(event) =>
                data.updateFilter("query", event.target.value)
              }
              placeholder="Words in saved titles or text"
            />
          </label>
          <label>
            Exact source domain
            <input
              type="text"
              maxLength={253}
              value={filters.domain}
              onChange={(event) =>
                data.updateFilter("domain", event.target.value)
              }
              placeholder="reports.example.org"
              autoCapitalize="none"
              spellCheck={false}
            />
          </label>
          <label>
            Retrieved from (UTC)
            <input
              type="date"
              max={filters.retrieved_to || undefined}
              value={filters.retrieved_from}
              onChange={(event) =>
                data.updateFilter("retrieved_from", event.target.value)
              }
            />
          </label>
          <label>
            Retrieved through (UTC)
            <input
              type="date"
              min={filters.retrieved_from || undefined}
              value={filters.retrieved_to}
              onChange={(event) =>
                data.updateFilter("retrieved_to", event.target.value)
              }
            />
          </label>
        </div>
        <p className="form-note">
          Use up to 16 literal keywords. Domains match exactly; retrieval dates
          include both days.
        </p>
        <div className="evidence-search-actions">
          <button className="button primary" type="submit">
            Search saved evidence
          </button>
          <button
            className="button secondary"
            type="button"
            onClick={data.clearSearch}
          >
            Clear filters
          </button>
        </div>
      </form>
      <p className="page-note">
        {appliedFilters
          ? `${appliedFilters.query ? "Exact-query matches first, then keyword rank." : "Filtered versions, newest retrieval first."} Up to 20 verified versions; narrow the search to find more.`
          : "Saved versions, newest retrieval first."}
      </p>
      {loading && <p role="status">Loading saved sources…</p>}
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      {inventory && !["ready", "limited"].includes(inventory.state) && (
        <p role="status">
          {inventory.message || stateMessages[inventory.state]}
        </p>
      )}
      {inventory && ["ready", "limited"].includes(inventory.state) && (
        <>
          {(searchIncomplete || (appliedFilters && inventory.message)) && (
            <p role="status">
              {inventory.message ||
                "Search results may be incomplete. Narrow the keywords or filters to inspect more evidence."}
            </p>
          )}
          {inventory.sources.length ? (
            <SourceTable
              sources={inventory.sources}
              loading={loading}
              selected={selected}
              onOpen={data.openSource}
            />
          ) : (
            <p role="status">
              {appliedFilters
                ? searchIncomplete
                  ? "No matching saved sources could be returned. Results may be incomplete."
                  : "No matching saved sources."
                : inventory.skipped_count > 0 || inventory.next_cursor
                  ? "No available sources in this page."
                  : "No saved sources yet."}
            </p>
          )}
          {inventory.skipped_count > 0 && (
            <p className="page-note">
              {inventory.skipped_count.toLocaleString()} unavailable or
              ineligible records were skipped. This is a bounded view of indexed{" "}
              {appliedFilters ? "versions." : "metadata."}
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
