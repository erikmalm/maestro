export const reviewOutcomeLabel = (outcome) =>
  ({
    accepted: "Accepted",
    writing_defect: "Writing correction needed",
    evidence_gap: "Evidence gap",
    partial: "Partial",
    unverifiable: "Unable to verify",
  })[outcome] || "Review unavailable";

export default function FinalReview({ review, assessment }) {
  const items = new Map(
    (assessment?.items || []).map((item) => [item.id, item.criterion]),
  );
  return (
    <section className="research-section" aria-label="Final answer review">
      <h3>Final answer review</h3>
      <p>
        <strong>
          {review.basis === "semantic"
            ? reviewOutcomeLabel(review.outcome)
            : "Review not completed"}
        </strong>
      </p>
      <p className="research-request">{review.reason}</p>
      <ol className="research-assessment-list">
        {(review.items || []).map((item) => (
          <li key={item.item_id}>
            <h4>{items.get(item.item_id) || "Requested item"}</h4>
            <p>
              <strong>{reviewOutcomeLabel(item.outcome)}</strong>
            </p>
            <p className="research-request">{item.reason}</p>
          </li>
        ))}
      </ol>
      <p className="form-note">
        Review checks the answer against the supplied evidence. Saved source
        identity and model agreement do not establish the publisher's factual
        correctness. No automatic rewrite or further research runs here.
      </p>
    </section>
  );
}
