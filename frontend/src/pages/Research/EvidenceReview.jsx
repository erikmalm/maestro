import { sourceDate, sourceURL } from "../Evidence/useEvidence";

const stateLabels = {
  covered: "Covered",
  missing: "Missing",
  stale: "Stale for this request",
  conflicting: "Conflicting",
  unavailable: "Unavailable",
};
const temporalLabels = {
  historical: "Historical reporting period",
  current: "Current observation",
  refresh: "Requires refresh",
  unspecified: "Temporal scope unspecified",
};
const freshnessLabels = {
  explicit_requested_period: "Matches the requested historical period",
  requested_period_not_established: "Requested period is not established",
  past_observation_for_current_requirement:
    "Observation is too old for a current request",
  current_origin_verification_unknown: "Current source verification is unknown",
  fresh_origin_check_required: "A fresh source check is required",
  no_temporal_requirement: "No time-sensitive condition was requested",
};
const date = (value) => sourceDate(value) || "Unknown";

function Passage({ support, passages, sources }) {
  const passage = passages.get(support.passage_id);
  const source = passage && sources.get(passage.source_id);
  if (
    !passage ||
    !source ||
    (support.source_id && support.source_id !== source.id)
  )
    return <li className="form-note">This assessed passage is unavailable.</li>;
  const url = sourceURL(source.url);
  return (
    <li className="research-reviewed-passage">
      <p>
        <strong>{source.title || "Untitled saved source"}</strong>
      </p>
      <blockquote>{support.quote}</blockquote>
      <dl className="research-metadata">
        <div>
          <dt>Temporal scope</dt>
          <dd>{temporalLabels[support.temporal_scope] || "Not established"}</dd>
        </div>
        {support.period && (
          <div>
            <dt>Reporting period</dt>
            <dd>{support.period}</dd>
          </div>
        )}
        {support.observation_date && (
          <div>
            <dt>Observation date</dt>
            <dd>{support.observation_date}</dd>
          </div>
        )}
        <div>
          <dt>Freshness basis</dt>
          <dd>
            {freshnessLabels[support.freshness_basis] || "Not established"}
          </dd>
        </div>
        <div>
          <dt>Best-before date</dt>
          <dd>
            {support.best_before
              ? date(support.best_before)
              : "Not established"}
          </dd>
        </div>
        <div>
          <dt>Retrieved</dt>
          <dd>{date(source.retrieved_at)}</dd>
        </div>
        <div>
          <dt>Published</dt>
          <dd>{date(source.published_at)}</dd>
        </div>
        {source.modified_at && (
          <div>
            <dt>Modified</dt>
            <dd>{date(source.modified_at)}</dd>
          </div>
        )}
      </dl>
      <p className="form-note">
        Reuse-window flag at lookup:{" "}
        {source.stale ? "Outside reuse window" : "Within reuse window"}. This
        flag does not establish suitability for the requested period.
      </p>
      <p className="form-note">
        {source.completeness?.maestro_truncated
          ? "Truncated saved excerpt."
          : "Saved excerpt; provider completeness is unknown."}{" "}
        This is not a full origin document.
      </p>
      {url && (
        <a href={url} target="_blank" rel="noopener noreferrer">
          Open live source
        </a>
      )}
      <details className="research-candidate">
        <summary>Passage and saved-version references</summary>
        <pre className="research-passage-text">{passage.text}</pre>
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
            <dt>Passage hash</dt>
            <dd>
              <code>{passage.passage_hash}</code>
            </dd>
          </div>
          <div>
            <dt>Passage offsets</dt>
            <dd>
              {passage.start}–{passage.end} (Unicode code points)
            </dd>
          </div>
        </dl>
      </details>
    </li>
  );
}

export default function EvidenceReview({ review, assessment }) {
  const requirements = new Map(
    (assessment?.items || []).map((item, index) => [
      item.id,
      { ...item, number: index + 1 },
    ]),
  );
  const subjects = new Map(
    (assessment?.subjects || []).map((subject) => [subject.id, subject.name]),
  );
  const passages = new Map(
    (review.passages || []).map((passage) => [passage.id, passage]),
  );
  const sources = new Map(
    (review.sources || []).map((source) => [source.id, source]),
  );
  const lookup = review.lookup || {};
  const limited =
    lookup.state === "limited" ||
    lookup.search_limited ||
    lookup.skipped_count > 0;
  const lookupMessages = {
    disabled: "Source saving is disabled. Saved evidence was not inspected.",
    unconfigured:
      "No source archive is configured. Saved evidence was not inspected.",
    unavailable: "Saved evidence could not be inspected reliably.",
  };
  return (
    <section className="research-section" aria-label="Saved-evidence review">
      <h3>Saved-evidence review</h3>
      <p>
        This section records the saved-evidence assessment. Final answer review
        has not run.
      </p>
      <p className="form-note">
        Assessment recorded: {date(review.checked_at)}
      </p>
      {lookupMessages[lookup.state] && <p>{lookupMessages[lookup.state]}</p>}
      {limited && (
        <p className="research-evidence-notice">
          Saved-evidence lookup was limited. Supporting evidence may exist
          outside the inspected candidates.
          {lookup.skipped_count > 0 &&
            ` ${lookup.skipped_count} records were skipped.`}
        </p>
      )}
      {review.selection?.limited && (
        <p className="research-evidence-notice">
          Only a bounded selection was reviewed.
          {review.selection.omitted_count > 0 &&
            ` ${review.selection.omitted_count} candidates were not included in this assessment.`}
        </p>
      )}
      <p className="form-note">
        Missing means no sufficient support was found in the inspected
        candidates; it does not establish that evidence is absent elsewhere.
      </p>
      <ol className="research-assessment-list research-evidence-items">
        {(review.items || []).map((item) => {
          const requirement = requirements.get(item.item_id);
          return (
            <li key={item.item_id} value={requirement?.number}>
              <section
                aria-label={`Evidence for requested item ${requirement?.number || item.item_id}`}
              >
                <h4>{requirement?.criterion || "Requested item"}</h4>
                {requirement && (
                  <p className="form-note">
                    {[
                      ...(requirement.subject_ids || [])
                        .map((id) => subjects.get(id))
                        .filter(Boolean),
                      requirement.period,
                      requirement.unit,
                      requirement.currency,
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </p>
                )}
                <p>
                  <strong>{stateLabels[item.state] || "Unavailable"}</strong>
                </p>
                <p className="research-request">{item.reason}</p>
                {item.suitability_reason &&
                  item.suitability_reason !== item.reason && (
                    <p className="form-note">{item.suitability_reason}</p>
                  )}
                {(item.supports || []).length > 0 && (
                  <ul className="research-reviewed-passages">
                    {item.supports.map((support, index) => (
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
