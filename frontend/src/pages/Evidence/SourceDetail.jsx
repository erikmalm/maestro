import { useEffect, useRef } from "react";
import { sourceDate, sourceURL } from "./useEvidence";

export default function SourceDetail({ selected, detail, onClose }) {
  const heading = useRef(null);
  useEffect(() => heading.current?.focus(), [selected.capture_id]);
  const source = detail.source;
  const url = source && sourceURL(source.url);
  return (
    <section
      className="card evidence-detail"
      aria-labelledby="evidence-detail-title"
      aria-busy={detail.loading || undefined}
    >
      <div className="evidence-detail-heading">
        <h2 id="evidence-detail-title" tabIndex={-1} ref={heading}>
          Saved source text
        </h2>
        <button className="button secondary" onClick={onClose}>
          Close saved text
        </button>
      </div>
      {detail.loading && <p role="status">Loading saved text…</p>}
      {detail.error && (
        <p className="error-banner" role="alert">
          {detail.error}
        </p>
      )}
      {source && (
        <>
          <h3>{source.title || "Untitled source"}</h3>
          {url && (
            <a href={url} target="_blank" rel="noopener noreferrer">
              Open live source
            </a>
          )}
          <p className="evidence-secondary">
            Search excerpt · Retrieved{" "}
            {sourceDate(source.retrieved_at) || "date unknown"}
          </p>
          <p className="evidence-secondary">
            {sourceDate(source.published_at)
              ? `Published ${sourceDate(source.published_at)}`
              : "Publication date unknown"}
          </p>
          {source.modified_at && (
            <p className="evidence-secondary">
              Updated {sourceDate(source.modified_at) || "date unknown"}
            </p>
          )}
          <p className="evidence-secondary">
            {selected.content_bytes.toLocaleString()} bytes ·{" "}
            {source.completeness.maestro_truncated
              ? "Truncated during capture"
              : "Provider completeness unknown"}
          </p>
          <pre
            className="evidence-source-text"
            aria-label="Saved source content"
          >
            {source.content}
          </pre>
        </>
      )}
    </section>
  );
}
