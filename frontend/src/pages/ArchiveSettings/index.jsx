import { sourceDate } from "../Evidence/useEvidence";
import "./styles.css";

const count = (value) =>
  typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString()
    : "Unknown";
const date = (value) => sourceDate(value) || "Not recorded";

export default function ArchiveSettings({
  data,
  editable = false,
  onOpenSettings = undefined,
}) {
  const { status, loading, refreshing, saving, error, notice } = data;
  const config = status?.config;
  const readiness = !status
    ? "Unknown"
    : status.path_error
      ? "Unavailable"
      : !status.configured
        ? "Not configured"
        : status.available
          ? "Available"
          : "Unavailable";
  return (
    <section
      className="card settings-card archive-settings"
      aria-label="Source archive"
    >
      <div className="archive-heading">
        <h2>Source archive</h2>
        {config && (
          <span className="badge neutral">
            {config.enabled ? "Enabled" : "Disabled"}
          </span>
        )}
      </div>
      {loading && <p role="status">Loading source archive…</p>}
      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {status && (
        <>
          {status.path_error && (
            <p className="error-banner" role="alert">
              {status.path_error}
            </p>
          )}
          {status.config_error && (
            <p className="error-banner" role="alert">
              {status.config_error}
            </p>
          )}
          <dl className="archive-paths">
            {(editable
              ? [
                  ["Private database", status.database_path],
                  ["Local source index", status.index_path],
                  ["Source archive", status.path],
                ]
              : [["Source archive", status.path]]
            ).map(([label, value]) => (
              <div key={label}>
                <dt>{label}</dt>
                <dd>
                  <code>
                    {value ||
                      (status.path_error ? "Unavailable" : "Not configured")}
                  </code>
                </dd>
              </div>
            ))}
          </dl>
          <dl className="archive-statistics">
            {[
              ["Archive write availability", readiness],
              ["Indexed sources", count(status.indexed_count)],
              ...(editable
                ? [
                    [
                      "Capture policy",
                      config
                        ? config.capture_policy === "all_public"
                          ? "All eligible public sources"
                          : `Approved sources only (${count(config.public_sources.length)})`
                        : "Unavailable",
                    ],
                    [
                      "Recorded logical archive bytes",
                      count(status.archive_bytes),
                    ],
                    ["Archive items", count(status.archive_items)],
                    ["Missing records", count(status.missing_count)],
                    ["Corrupt records", count(status.corrupt_count)],
                    ["Last capture", date(status.last_capture?.retrieved_at)],
                    ["Last indexed", date(status.last_indexed_at)],
                  ]
                : []),
            ].map(([label, value]) => (
              <div key={label}>
                <dt>{label}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
          {editable && (
            <p className="form-note">
              Automatic search capture is planned; the archive may be empty.
            </p>
          )}
          {status.last_error && (
            <p className="error-banner" role="alert">
              {status.last_error}
            </p>
          )}
          {editable && config && !status.config_error && (
            <button
              className="button secondary"
              disabled={
                saving ||
                loading ||
                !status.configured ||
                Boolean(status.path_error)
              }
              aria-disabled={refreshing || undefined}
              onClick={() => data.setEnabled(!config.enabled)}
            >
              {saving
                ? "Saving source archive…"
                : config.enabled
                  ? "Disable source saving"
                  : "Enable source saving"}
            </button>
          )}
        </>
      )}
      {editable ? (
        <p className="form-note archive-startup-note">
          Paths are set at startup. Update MAESTRO_DATA_DIR and
          MAESTRO_CONTEXT_ARCHIVE_DIR, then restart Maestro to change them. The
          local index follows the private database.
        </p>
      ) : (
        <button className="button secondary" onClick={onOpenSettings}>
          Open archive settings
        </button>
      )}
    </section>
  );
}
