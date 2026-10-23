import { sourceDate, sourceURL } from "../Evidence/useEvidence";

const kindLabels = {
  fact: "Source fact",
  interpretation: "Interpretation",
  calculation: "Calculation",
  gap: "Evidence gap",
};
const coverageLabels = {
  covered: "Covered",
  missing: "Missing",
  stale: "Stale for this request",
  conflicting: "Conflicting",
  unavailable: "Unavailable",
};
const reasonMessages = {
  draft_requires_review:
    "A draft was prepared from the assessed saved evidence. Final review has not run.",
  no_supported_facts: "No supported facts are available to write a draft.",
  writer_capacity:
    "The writer could not run within the remaining allowance. The evidence assessment remains below.",
  source_unavailable:
    "Saved evidence is no longer available. The previous draft text is withheld.",
};

function expression(calculation) {
  if (calculation.operands?.length !== 2) return null;
  const [first, second] = calculation.operands.map((operand) => operand.value);
  return {
    sum: `${first} + ${second}`,
    difference: `${first} − ${second}`,
    ratio: `${first} ÷ ${second}`,
    percent_change: `(${second} − ${first}) ÷ ${first} × 100`,
  }[calculation.operation];
}

export default function Draft({ draft, assessment, review }) {
  const subjects = new Map(
    (assessment?.subjects || []).map((subject) => [subject.id, subject.name]),
  );
  const numbers = new Map(
    (assessment?.items || []).map((item, index) => [item.id, index + 1]),
  );
  const sources = new Map(
    (review?.sources || []).map((source) => [source.id, source]),
  );
  const passages = new Map(
    (review?.passages || []).map((passage) => [passage.id, passage]),
  );
  const available = (support) => {
    const source = sources.get(support.source_id);
    const passage = passages.get(support.passage_id);
    return (
      source &&
      passage?.source_id === source.id &&
      support.quote &&
      passage.text.includes(support.quote)
    );
  };
  return (
    <section
      className="research-section research-draft"
      aria-label="Research draft"
    >
      <h3>
        {draft.state === "unreviewed"
          ? "Unreviewed draft"
          : "Draft unavailable"}
      </h3>
      <p className="research-draft-boundary">
        <strong>Not finally reviewed.</strong>{" "}
        {reasonMessages[draft.reason] ||
          "Draft availability could not be established."}
      </p>
      {review?.checked_at && (
        <p className="form-note">
          Evidence assessed: {sourceDate(review.checked_at) || "Unknown"}
        </p>
      )}
      <ol className="research-assessment-list research-draft-items">
        {(draft.items || []).map((item) => {
          const supports = item.supports || [];
          const missingSupport =
            item.kind !== "gap" &&
            (!supports.length || !supports.every(available));
          const sourceUnavailable =
            draft.reason === "source_unavailable" || missingSupport;
          const withheld = draft.state !== "unreviewed" || missingSupport;
          const calculation = !withheld && item.calculation;
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
                <p className="form-note">
                  {withheld || item.kind === "gap"
                    ? !sourceUnavailable && item.coverage_state === "covered"
                      ? "Draft unavailable"
                      : "Evidence gap"
                    : kindLabels[item.kind] || "Evidence gap"}{" "}
                  ·{" "}
                  {sourceUnavailable
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
                {calculation && (
                  <section
                    className="research-calculation"
                    aria-label="Calculated result"
                  >
                    <p>
                      <strong>Calculated result: {calculation.result}</strong>
                    </p>
                    <p className="research-request">
                      {expression(calculation)} = {calculation.result}
                    </p>
                    <p className="form-note">
                      Computed from the cited values. Rounded to{" "}
                      {calculation.precision} decimal places using half-even
                      rounding.
                    </p>
                    <p className="form-note">
                      {calculation.operands
                        .map(
                          (operand) =>
                            `${operand.value} (source quote ${operand.support_index + 1})`,
                        )
                        .join("; ")}
                    </p>
                  </section>
                )}
                {!withheld && supports.length > 0 && (
                  <ol
                    className="research-draft-citations"
                    aria-label="Draft source quotes"
                  >
                    {supports.map((support, index) => {
                      const source = sources.get(support.source_id);
                      const url = sourceURL(source.url);
                      return (
                        <li key={`${support.passage_id}-${index}`}>
                          <blockquote>{support.quote}</blockquote>
                          <p>
                            <strong>
                              {source.title || "Untitled saved source"}
                            </strong>
                          </p>
                          <p className="form-note">
                            Retrieved:{" "}
                            {sourceDate(source.retrieved_at) || "Unknown"}.
                            Published:{" "}
                            {sourceDate(source.published_at) || "Unknown"}.
                          </p>
                          {url && (
                            <a
                              href={url}
                              target="_blank"
                              rel="noopener noreferrer"
                            >
                              Open draft source
                            </a>
                          )}
                          <details className="research-candidate">
                            <summary>Draft citation references</summary>
                            <dl className="research-metadata">
                              <div>
                                <dt>Capture ID</dt>
                                <dd>
                                  <code>{source.capture_id}</code>
                                </dd>
                              </div>
                              <div>
                                <dt>Content hash</dt>
                                <dd>
                                  <code>{source.content_hash}</code>
                                </dd>
                              </div>
                              <div>
                                <dt>Manifest hash</dt>
                                <dd>
                                  <code>{source.manifest_hash}</code>
                                </dd>
                              </div>
                              <div>
                                <dt>Support offsets</dt>
                                <dd>
                                  {support.start}–{support.end} (Unicode code
                                  points)
                                </dd>
                              </div>
                            </dl>
                          </details>
                        </li>
                      );
                    })}
                  </ol>
                )}
              </section>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
