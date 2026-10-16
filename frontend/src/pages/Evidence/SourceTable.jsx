import { sourceDate } from "./useEvidence";

export default function SourceTable({ sources, loading, selected, onOpen }) {
  return (
    <div className="evidence-table-scroll">
      <table className="evidence-table" aria-busy={loading || undefined}>
        <caption>Saved source versions</caption>
        <thead>
          <tr>
            <th scope="col">Source</th>
            <th scope="col">Retrieved</th>
            <th scope="col">Stored content</th>
            <th scope="col">Inspect</th>
          </tr>
        </thead>
        <tbody>
          {sources.map((source) => (
            <tr
              key={source.capture_id}
              aria-selected={
                selected?.capture_id === source.capture_id || undefined
              }
            >
              <th scope="row">
                {source.title || "Untitled source"}
                <span className="evidence-secondary">{source.url}</span>
                <span className="evidence-secondary">
                  {sourceDate(source.published_at)
                    ? `Published ${sourceDate(source.published_at)}`
                    : "Publication date unknown"}
                </span>
              </th>
              <td>
                {sourceDate(source.retrieved_at) || "Retrieval date unknown"}
              </td>
              <td>
                Search excerpt
                <span className="evidence-secondary">
                  {source.content_bytes.toLocaleString()} bytes
                </span>
                <span className="evidence-secondary">
                  {source.completeness.maestro_truncated
                    ? "Truncated during capture"
                    : "Provider completeness unknown"}
                </span>
              </td>
              <td>
                <button
                  className="button secondary"
                  disabled={loading}
                  onClick={(event) => onOpen(source, event.currentTarget)}
                >
                  View saved text
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
