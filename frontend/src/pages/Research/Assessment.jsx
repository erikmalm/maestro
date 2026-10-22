const qualifierLabels = {
  period: "Period",
  unit: "Unit",
  currency: "Currency",
  freshness: "Freshness",
};
const constraintLabels = {
  require: "Required",
  prefer: "Preferred",
  forbid: "Excluded",
};

export default function Assessment({ assessment }) {
  if (!assessment)
    return (
      <section
        aria-label="Requirements assessment"
        className="research-section"
      >
        <h3>Requirements assessment</h3>
        <p>
          No validated assessment has been recorded. No answer was produced.
        </p>
      </section>
    );
  const subjects = new Map(
    assessment.subjects.map((subject) => [subject.id, subject.name]),
  );
  const itemNumbers = new Map(
    assessment.items.map((item, index) => [item.id, index + 1]),
  );
  const appliesTo = (ids) =>
    ids.length
      ? "Requested items: " + ids.map((id) => itemNumbers.get(id)).join(", ")
      : "Applies to the whole request";
  return (
    <section aria-label="Requirements assessment" className="research-section">
      <h3>Requirements assessment</h3>
      <p>
        {assessment.decision === "clarify"
          ? "Clarification is needed before dependent research can begin."
          : "Requested scope recorded. No reviewed answer has been produced."}
      </p>
      {assessment.clarifications.length > 0 && (
        <section
          aria-label="Clarification needed"
          className="research-clarifications"
        >
          <h4>Clarification needed</h4>
          <p>
            Submit a revised request above when ready. This run will not
            continue automatically.
          </p>
          <ol>
            {assessment.clarifications.map((clarification, index) => (
              <li key={index}>
                <p>
                  <strong>{clarification.question}</strong>
                </p>
                <blockquote>{clarification.quote}</blockquote>
                <p className="form-note">{appliesTo(clarification.item_ids)}</p>
              </li>
            ))}
          </ol>
        </section>
      )}
      {assessment.subjects.length > 0 && (
        <section aria-label="Requested subjects">
          <h4>Requested subjects</h4>
          <ul className="research-assessment-list">
            {assessment.subjects.map((subject) => (
              <li key={subject.id}>
                <p>
                  <strong>{subject.name}</strong> ({subject.kind})
                </p>
                {subject.identifiers.length > 0 && (
                  <ul>
                    {subject.identifiers.map((identifier, index) => (
                      <li key={index}>
                        {identifier.kind === "isin" ? "ISIN" : identifier.kind}:{" "}
                        {identifier.text}
                      </li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
      <section aria-label="Requested items">
        <h4>Requested items</h4>
        <p className="form-note">
          Every listed item is required for completion.
        </p>
        <ol className="research-assessment-list">
          {assessment.items.map((item) => (
            <li key={item.id}>
              <p className="research-request">{item.criterion}</p>
              <dl className="research-metadata">
                {item.subject_ids.length > 0 && (
                  <div>
                    <dt>Subjects</dt>
                    <dd>
                      {item.subject_ids
                        .map((id) => subjects.get(id))
                        .join(", ")}
                    </dd>
                  </div>
                )}
                {Object.entries(qualifierLabels).map(([key, label]) =>
                  item[key] ? (
                    <div key={key}>
                      <dt>{label}</dt>
                      <dd>{item[key]}</dd>
                    </div>
                  ) : null,
                )}
              </dl>
            </li>
          ))}
        </ol>
      </section>
      {assessment.constraints.length > 0 && (
        <section aria-label="Research constraints">
          <h4>Research constraints</h4>
          <ul className="research-assessment-list">
            {assessment.constraints.map((constraint, index) => (
              <li key={index}>
                <p>
                  <strong>
                    {constraintLabels[constraint.strength]} ({constraint.kind})
                  </strong>
                  : {constraint.criterion}
                </p>
                <p className="form-note">{appliesTo(constraint.item_ids)}</p>
              </li>
            ))}
          </ul>
        </section>
      )}
      <details className="research-candidate">
        <summary>Overall completion criterion</summary>
        <p className="research-request">{assessment.completion_criterion}</p>
      </details>
      <p className="form-note">
        This assessment records what needs researching. It does not verify
        facts, inspect evidence, write an answer or perform a final review.
      </p>
    </section>
  );
}
