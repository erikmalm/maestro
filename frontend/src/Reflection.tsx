import { useEffect, useState } from "react";
import { BookOpen, Pause, Play, RefreshCw, ShieldCheck } from "lucide-react";
import * as api from "./api";
import type { ReflectionConfig, ReflectionStatus } from "./api";

const cost = (value: number) => `$${value.toFixed(4)}`;

export default function Reflection({
  status,
  onChange,
}: {
  status: ReflectionStatus | null;
  onChange: (value: ReflectionStatus) => void;
}) {
  const [config, setConfig] = useState<ReflectionConfig | null>(null);
  const [text, setText] = useState("");
  const [share, setShare] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  useEffect(() => {
    if (status && !config) setConfig(status.config);
  }, [status, config]);
  async function act(action: () => Promise<ReflectionStatus>, message: string) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const next = await action();
      onChange(next);
      setNotice(message);
      return true;
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not update reflection.",
      );
      return false;
    } finally {
      setBusy(false);
    }
  }
  if (!status || !config) return <p>Loading reflection controls…</p>;
  const active = status.jobs.some((job) =>
    ["queued", "running"].includes(job.status),
  );
  const field = (
    key: keyof ReflectionConfig,
    value: number | string | boolean,
  ) => setConfig({ ...config, [key]: value });
  return (
    <div className="reflection-layout">
      <section className="card reflection-overview">
        <div className="reflection-title">
          <div>
            <span className="eyebrow">BACKGROUND WORKER · REAL API USAGE</span>
            <h2>Learn from the work</h2>
          </div>
          <span className="badge neutral">
            {status.state.replaceAll("_", " ")}
          </span>
        </div>
        <p>
          Feedback → lesson → critique → revision → your review. The worker
          watches while Maestro is running and processes new, approved feedback
          once. Idle checks make no model calls.
        </p>
        <div className="reflection-metrics">
          <div>
            <small>Today, estimated USD</small>
            <strong>{cost(status.usage.today_usd)}</strong>
          </div>
          <div>
            <small>Month, estimated USD</small>
            <strong>{cost(status.usage.month_usd)}</strong>
          </div>
          <div>
            <small>Tokens today</small>
            <strong>{status.usage.tokens.toLocaleString()}</strong>
          </div>
          <div>
            <small>Reserved / uncertain</small>
            <strong>{cost(status.usage.reserved_usd)}</strong>
          </div>
        </div>
        <div className="reflection-actions">
          <button
            className="button primary"
            disabled={
              busy ||
              active ||
              !status.credentials_present ||
              status.usage.unresolved
            }
            onClick={() =>
              void act(
                api.runReflection,
                "One cycle queued with the saved settings.",
              )
            }
          >
            <Play size={15} />
            Run one cycle
          </button>
          <button
            className="button secondary"
            disabled={busy}
            onClick={() =>
              void act(
                () => api.saveReflection({ ...status.config, enabled: false }),
                "Paused. An in-flight call may finish; further calls are blocked.",
              ).then((ok) => {
                if (ok) setConfig({ ...status.config, enabled: false });
              })
            }
          >
            <Pause size={15} />
            Pause background
          </button>
        </div>
        {!status.credentials_present && (
          <p className="reflection-note">
            <ShieldCheck size={16} />
            No backend API key configured. Feedback and settings work locally;
            model calls stay blocked. Set OPENAI_API_KEY in the server's
            environment and restart. Keys are never entered here.
          </p>
        )}
        {status.usage.unresolved && (
          <p role="alert">
            Unknown provider charges block further calls. Check provider billing
            before reconciliation; restarting or clearing history does not reset
            this safeguard.
          </p>
        )}
      </section>
      {error && (
        <div className="error-banner" role="alert">
          {error}
        </div>
      )}
      {notice && <p role="status">{notice}</p>}
      {status.uncertain_charges.length > 0 && (
        <section className="card reflection-box">
          <h3>Reconcile unknown charges</h3>
          <p>
            Check OpenAI usage for the failed request, then enter the actual
            billed amount in USD. This is your confirmation, not an automated
            billing lookup. Token counts remain unknown for failed calls.
          </p>
          {status.uncertain_charges.map((entry) => (
            <form
              key={entry.id}
              onSubmit={(event) => {
                event.preventDefault();
                const data = new FormData(event.currentTarget);
                void act(
                  () =>
                    api.reconcileCharge(
                      entry.id,
                      Number(data.get("billed_usd")),
                    ),
                  "Charge reconciled. Background work remains paused.",
                );
              }}
            >
              <label>
                {new Date(entry.at).toLocaleString()} · reserved{" "}
                {cost(entry.reserved_usd)}
                <input
                  name="billed_usd"
                  type="number"
                  required
                  min="0"
                  max="100000"
                  step="0.000001"
                  placeholder="Verified billed USD"
                />
              </label>
              <button
                className="button secondary"
                disabled={busy || status.config.enabled || active}
              >
                Confirm verified charge
              </button>
            </form>
          ))}
        </section>
      )}
      <div className="reflection-columns">
        <section className="card reflection-box">
          <h3>Give it something to learn</h3>
          <p>
            Use a correction or an observed outcome. Existing chats, documents
            and memories are not collected automatically.
          </p>
          <form
            onSubmit={async (event) => {
              event.preventDefault();
              if (
                await act(
                  () => api.addFeedback(text, share),
                  "Feedback saved privately.",
                )
              ) {
                setText("");
                setShare(false);
              }
            }}
          >
            <label htmlFor="reflection-feedback">Feedback</label>
            <textarea
              id="reflection-feedback"
              required
              maxLength={2000}
              rows={4}
              value={text}
              onChange={(event) => setText(event.target.value)}
              placeholder="The last summary missed its sources. Future summaries should include a source for each factual claim."
            />
            <label className="reflection-check">
              <input
                type="checkbox"
                checked={share}
                onChange={(event) => setShare(event.target.checked)}
              />
              Allow this feedback to be sent to OpenAI for reflection.
            </label>
            <button
              className="button primary"
              disabled={busy || !text.trim()}
              type="submit"
            >
              Save feedback
            </button>
          </form>
          <div className="reflection-feedback-list">
            {status.evidence
              .slice(-5)
              .reverse()
              .map((item) => (
                <div key={item.id}>
                  <span className="badge neutral">
                    {item.share ? "Provider sharing approved" : "Local only"}
                  </span>
                  <p>{item.text}</p>
                </div>
              ))}
          </div>
        </section>
        <section className="card reflection-box">
          <h3>Set the boundaries</h3>
          <p>
            Prices are your estimates per million tokens. Verify them for the
            exact model; this is not a provider billing feed. Workspace limits
            also apply.
          </p>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void act(
                () => api.saveReflection(config),
                "Reflection settings saved.",
              );
            }}
          >
            <label htmlFor="reflection-model">OpenAI model</label>
            <input
              id="reflection-model"
              value={config.model}
              maxLength={100}
              onChange={(event) => field("model", event.target.value)}
              placeholder="Exact model ID"
            />
            <div className="reflection-fields">
              {(
                [
                  [
                    "input_usd_per_million",
                    "Input USD / 1M tokens",
                    0,
                    10000,
                    "0.001",
                  ],
                  [
                    "output_usd_per_million",
                    "Output USD / 1M tokens",
                    0,
                    10000,
                    "0.001",
                  ],
                  ["cycle_usd", "USD per cycle", 0, 10, "0.01"],
                  ["daily_usd", "Reflection USD per day", 0, 100, "0.01"],
                  ["interval_minutes", "Check interval, minutes", 5, 1440, "1"],
                  ["max_passes", "Maximum revision passes", 1, 3, "1"],
                ] as const
              ).map(([key, label, min, max, step]) => (
                <label key={key}>
                  {label}
                  <input
                    type="number"
                    required
                    min={min}
                    max={max}
                    step={step}
                    value={config[key]}
                    onChange={(event) => field(key, Number(event.target.value))}
                  />
                </label>
              ))}
            </div>
            <label className="reflection-check">
              <input
                type="checkbox"
                checked={config.pricing_verified}
                onChange={(event) =>
                  field("pricing_verified", event.target.checked)
                }
              />
              I verified these model prices.
            </label>
            <label className="reflection-check">
              <input
                type="checkbox"
                checked={config.enabled}
                onChange={(event) => field("enabled", event.target.checked)}
              />
              Enable background reflection on new approved feedback.
            </label>
            <button className="button primary" disabled={busy} type="submit">
              Save reflection settings
            </button>
          </form>
        </section>
      </div>
      <section className="card reflection-box">
        <div className="reflection-title">
          <div>
            <h3>Cycles & lessons</h3>
            <p>
              Kept lessons guide future reflection cycles. Live chat recall and
              tested prompt promotion are still planned.
            </p>
          </div>
          <RefreshCw size={20} />
        </div>
        {status.jobs.length === 0 && (
          <p className="reflection-empty">
            No cycles yet. Save provider-approved feedback and configure the
            backend to start.
          </p>
        )}
        {status.jobs.map((job) => (
          <article className="reflection-job" key={job.id}>
            <div className="reflection-title">
              <strong>{new Date(job.at).toLocaleString()}</strong>
              <span className="badge neutral">
                {job.status.replaceAll("_", " ")}
              </span>
            </div>
            <p>{job.reason}</p>
            <small>
              {cost(job.cost)} · {job.tokens.toLocaleString()} tokens ·{" "}
              {job.events.length} calls
            </small>
            {job.draft && (
              <>
                <h4>{job.draft.summary}</h4>
                <p>
                  <BookOpen size={15} /> {job.draft.lesson}
                </p>
                <p className="reflection-note">
                  Critique: {job.draft.critique}
                </p>
              </>
            )}
            {job.status === "needs_review" && (
              <div className="reflection-actions">
                <button
                  className="button primary"
                  disabled={busy}
                  onClick={() =>
                    void act(
                      () => api.reviewReflection(job.id, true),
                      "Lesson kept as private reflection guidance.",
                    )
                  }
                >
                  Keep lesson
                </button>
                <button
                  className="button secondary"
                  disabled={busy}
                  onClick={() =>
                    void act(
                      () => api.reviewReflection(job.id, false),
                      "Lesson declined.",
                    )
                  }
                >
                  Decline
                </button>
              </div>
            )}
          </article>
        ))}
        <details className="reflection-cleanup">
          <summary>Clear private reflection history</summary>
          <p>
            Removes feedback, drafts and kept lessons from live storage.
            Accounting remains so budgets cannot be reset. This does not erase
            backups or copies already sent to a provider.
          </p>
          <button
            className="button secondary"
            disabled={busy || active}
            onClick={() =>
              void act(
                api.clearReflection,
                "Reflection content cleared; usage accounting retained.",
              )
            }
          >
            Clear feedback and lessons
          </button>
        </details>
      </section>
    </div>
  );
}
