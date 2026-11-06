import { sourceDate } from "../Evidence/useEvidence";
import { Passage } from "./EvidenceReview";

const reasonMessages = {
  draft_requires_review:
    "A cited draft was prepared from the assessed saved evidence. Final review has not run.",
  no_supported_facts: "No supported facts are available to write a draft.",
  writer_capacity:
    "The writer could not run within the remaining allowance. The evidence assessment remains available.",
  source_unavailable:
    "Saved evidence is no longer available. The previous draft text is withheld.",
};
const coverageLabels = {
  covered: "Covered",
  missing: "Missing",
  stale: "Stale for this request",
  conflicting: "Conflicting",
  unavailable: "Unavailable",
};

export default function Draft({ draft, assessment, review }) {
  const subjects = new Map(
    (assessment?.subjects || []).map((s) => [s.id, s.name]),
  );
  const numbers = new Map(
    (assessment?.items || []).map((item, i) => [item.id, i + 1]),
  );
  const sources = new Map(
    (review?.sources || []).map((source) => [source.id, source]),
  );
  const passages = new Map(
    (review?.passages || []).map((passage) => [passage.id, passage]),
  );
  const available = (support) => {
    const passage = passages.get(support.passage_id);
    return (
      sources.has(support.source_id) &&
      passage?.source_id === support.source_id &&
      support.quote &&
      passage.text.includes(support.quote)
    );
  };
  return (
    <section className="research-section" aria-label="Research draft">
      <h3>
        {draft.state === "unreviewed"
          ? "Unreviewed draft"
          : "Draft unavailable"}
      </h3>
      <p>
        <strong>Not finally reviewed.</strong>{" "}
        {reasonMessages[draft.reason] ||
          "Draft availability could not be established."}
      </p>
      {review?.checked_at && (
        <p className="form-note">
          Evidence assessed: {sourceDate(review.checked_at) || "Unknown"}
        </p>
      )}
      <ol className="research-assessment-list research-evidence-items">
        {(draft.items || []).map((item) => {
          const supports = item.supports || [];
          const withheld =
            draft.state !== "unreviewed" ||
            (item.kind !== "gap" &&
              (!supports.length || !supports.every(available)));
          const proseUnavailable =
            item.kind === "gap" && item.gap_reason === "insufficient_evidence";
          const label = withheld
            ? "Draft unavailable"
            : item.kind === "gap"
              ? item.gap_reason === "calculation_required"
                ? "Derived calculation unavailable"
                : proseUnavailable
                  ? "Supported prose unavailable"
                  : "Evidence gap"
              : item.kind === "fact"
                ? "Source fact"
                : "Interpretation";
          return (
            <li key={item.item_id} value={numbers.get(item.item_id)}>
              <section
                aria-label={`Draft for requested item ${numbers.get(item.item_id) || item.item_id}`}
              >
                <h4>{item.criterion}</h4>
                <p className="form-note">
                  {[
                    ...(item.subject_ids || [])
                      .map((id) => subjects.get(id))
                      .filter(Boolean),
                    item.period,
                    item.unit,
                    item.currency,
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </p>
                {item.freshness && (
                  <p className="form-note">
                    Freshness requirement: {item.freshness}
                  </p>
                )}
                <p>
                  <strong>{label}</strong>
                  {" · "}
                  {!withheld && proseUnavailable && "Evidence assessment: "}
                  {withheld
                    ? "Unavailable"
                    : coverageLabels[item.coverage_state] || "Unavailable"}
                </p>
                <p className="research-request">
                  {withheld
                    ? draft.reason === "source_unavailable"
                      ? reasonMessages.source_unavailable
                      : "A supported draft statement is unavailable for this item."
                    : item.text}
                </p>
                {!withheld && supports.length > 0 && (
                  <ul
                    className="research-reviewed-passages"
                    aria-label="Draft source quotes"
                  >
                    {supports.map((support, index) => (
                      <Passage
                        key={`${support.passage_id}-${index}`}
                        support={support}
                        passages={passages}
                        sources={sources}
                      />
                    ))}
                  </ul>
                )}
              </section>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
