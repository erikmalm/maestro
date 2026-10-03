import { useEffect, useRef } from "react";
import { Check, LoaderCircle } from "lucide-react";
import * as api from "./api";
import type { Chat, ProviderStatus, WorkStatus } from "./api";
import { NumberFields, useSetupForm } from "./SetupForm";

const changeLabels = { add: "Added", update: "Updated", remove: "Forgot" };

export default function WorkSetup({
  initialStatus,
  provider,
  chats,
  onChange,
}: {
  initialStatus: WorkStatus;
  provider: ProviderStatus;
  chats: Chat[];
  onChange: (status: WorkStatus) => void;
}) {
  const {
    status,
    config,
    setConfig,
    dirty,
    busy,
    error,
    setError,
    notice,
    act,
  } = useSetupForm(initialStatus, onChange, "Could not update task settings.");
  const local = provider.config.protocol === "ollama";
  const models = local ? provider.models : [];
  const polling = useRef(false);
  const pollRevision = useRef(0);
  const changed = useRef(onChange);
  changed.current = onChange;
  const enabled = status.config.enabled;
  const pending = status.queued > 0 || status.running;

  useEffect(() => {
    if (busy || !(enabled || pending)) return;
    let active = true;
    async function poll() {
      if (!active || document.hidden || polling.current) return;
      polling.current = true;
      const revision = pollRevision.current;
      const apply = changed.current;
      try {
        const next = await api.loadReflection();
        if (active && !document.hidden && revision === pollRevision.current)
          apply(next);
      } catch (reason) {
        if (active && !document.hidden)
          setError(
            reason instanceof Error
              ? reason.message
              : "Could not refresh reflection.",
          );
      } finally {
        polling.current = false;
      }
    }
    const timer = window.setInterval(() => void poll(), 3000);
    document.addEventListener("visibilitychange", poll);
    return () => {
      active = false;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", poll);
    };
  }, [busy, enabled, pending]);

  return (
    <section className="card reflection-box">
      <div className="reflection-title">
        <div>
          <h2>Task models &amp; reflection</h2>
          <p>
            Build private memory and reflect on working style while chat is
            idle.
          </p>
        </div>
        <span className="badge neutral">
          {status.worker_available
            ? status.waiting_for_ollama
              ? "Waiting for Ollama"
              : status.running
                ? "Reflecting"
                : enabled
                  ? "Enabled"
                  : "Paused"
            : "Not running"}
        </span>
      </div>
      {status.worker_available && (
        <p className="reflection-note">
          {status.queued} queued · {status.today_jobs} /{" "}
          {status.config.max_jobs_per_day} jobs today · {status.today_tokens} /{" "}
          {status.config.max_tokens_per_day} tokens today
          {status.last_stop_reason && <> · {status.last_stop_reason}</>}
        </p>
      )}
      {error && (
        <div className="error-banner" role="alert">
          {error}
        </div>
      )}
      {notice && <p role="status">{notice}</p>}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          pollRevision.current += 1;
          void act(
            "save",
            () => api.saveWorkConfig(config),
            "Task settings saved.",
          );
        }}
      >
        {(
          [
            ["reflection_model", "Reflection model"],
            ["memory_model", "Memory extraction model"],
            ["coding_model", "Coding model"],
          ] as const
        ).map(([key, label]) => (
          <label key={key}>
            {label}
            <select
              value={config[key]}
              disabled={!!busy}
              onChange={(event) => setConfig({ [key]: event.target.value })}
            >
              <option value="">Recommended installed model</option>
              {[...new Set([config[key], ...models])]
                .filter(Boolean)
                .map((model) => (
                  <option key={model} value={model}>
                    {model}
                    {!models.includes(model) && " (unavailable)"}
                  </option>
                ))}
            </select>
          </label>
        ))}
        <p className="reflection-note">
          Automatic choices prefer gpt-oss for reflection, Qwen for memory
          extraction, and Devstral for coding, then use the local chat default.
          Automatic memory uses the memory model to form ideas and the
          reflection model to review them. An explicit choice stays selected if
          a model is removed. Coding tasks do not run.
          {!local &&
            " Select Ollama in Model connection to use local task models."}
        </p>
        <label className="reflection-check">
          <input
            type="checkbox"
            checked={config.enabled}
            disabled={
              !!busy || !status.worker_available || (!local && !config.enabled)
            }
            onChange={(event) => setConfig({ enabled: event.target.checked })}
          />
          Enable background reflection
        </label>
        <label className="reflection-check">
          <input
            type="checkbox"
            checked={config.auto_curate}
            disabled={!!busy}
            onChange={(event) =>
              setConfig({
                auto_curate: event.target.checked,
                ...(!event.target.checked
                  ? { periodic_reflection: false }
                  : {}),
              })
            }
          />
          AI curates memory automatically
        </label>
        <label className="reflection-check">
          <input
            type="checkbox"
            checked={config.periodic_reflection}
            disabled={!!busy || !config.auto_curate}
            onChange={(event) =>
              setConfig({ periodic_reflection: event.target.checked })
            }
          />
          Reflect periodically on identity and working style
        </label>
        <label>
          Reflection interval (minutes)
          <input
            type="number"
            required
            min={30}
            max={10080}
            step={1}
            value={config.reflection_interval_minutes}
            disabled={
              !!busy || !config.auto_curate || !config.periodic_reflection
            }
            onChange={(event) =>
              setConfig({
                reflection_interval_minutes: Number(event.target.value),
              })
            }
          />
        </label>
        <p className="reflection-note">
          360 minutes is six hours. Periodic reflection starts while idle after
          enabling, then follows this interval.
          {status.next_reflection_at && (
            <> Next: {new Date(status.next_reflection_at).toLocaleString()}.</>
          )}
        </p>
        {dirty && (
          <p className="reflection-note">
            Unsaved changes. Save task settings to apply them.
          </p>
        )}
        {status.worker_available && (
          <p className="reflection-note">
            {config.auto_curate
              ? "Memories are maintained automatically after model review. You can edit or forget them; your edits are protected from automation. Identity and working-style notes remain distinct from user facts."
              : "Conversation activity produces suggestions for your review; memories are saved only when you accept them."}{" "}
            Calls run locally while chat is idle and models unload afterward.
            Pausing stops new calls; a running call may finish.
          </p>
        )}
        {!status.worker_available && (
          <p className="reflection-note">
            The reflection worker is being developed. These settings are saved
            for it; selecting task models does not start background inference or
            coding tasks.
          </p>
        )}
        <details className="reflection-cleanup">
          <summary>Work limits &amp; memory recall</summary>
          <div className="reflection-fields">
            <NumberFields
              values={config}
              onChange={(key, value) => setConfig({ [key]: value })}
              fields={[
                ["max_output_tokens", "Reflection output tokens", 1, 1024],
                ["max_jobs_per_day", "Daily reflection jobs", 0, 1000],
                ["max_tokens_per_day", "Daily reflection tokens", 0, 10000000],
                ["debounce_seconds", "Debounce (seconds)", 0, 3600],
                ["idle_seconds", "Chat idle time (seconds)", 0, 3600],
                ["timeout_seconds", "Job timeout (seconds)", 1, 1800],
                ["memory_recall_count", "Memories per reply", 0, 5],
                [
                  "memory_recall_characters",
                  "Memory context characters",
                  0,
                  1000,
                ],
              ]}
            />
          </div>
          <p className="reflection-note">
            Set either memory limit to 0 to turn off recall. Saved memory is
            used only in local chat with hosted web search off. Reflection
            limits apply to each call and the daily allowance.
          </p>
        </details>
        <div className="reflection-actions">
          <button type="submit" className="button primary" disabled={!!busy}>
            {busy ? (
              <LoaderCircle size={15} className="spin" />
            ) : (
              <Check size={15} />
            )}
            Save task settings
          </button>
        </div>
      </form>
      <details className="reflection-cleanup">
        <summary>Private reflection journal</summary>
        <p className="reflection-note">
          Brief outcomes, memory changes and source references are saved only on
          this computer.
        </p>
        {!status.journal?.length && (
          <p className="reflection-note">No reflections recorded yet.</p>
        )}
        {status.journal?.map((entry) => (
          <div className="memory-entry" key={entry.id}>
            <div>
              <p>{entry.summary}</p>
              <small>
                {entry.kind === "reflection"
                  ? "Working-style reflection"
                  : "Memory curation"}{" "}
                · {new Date(entry.created_at).toLocaleString()}
                {entry.outcome && <> · {entry.outcome}</>}
                {entry.models.length > 0 && <> · {entry.models.join(" → ")}</>}
              </small>
              <details>
                <summary>
                  {entry.changes.length} memory changes · {entry.sources.length}{" "}
                  source messages
                </summary>
                <ul>
                  {entry.changes.map((change, index) => (
                    <li key={`${change.memory_id}:${index}`}>
                      {changeLabels[change.operation]} {change.kind}:{" "}
                      {change.content || `memory ${change.memory_id}`}
                    </li>
                  ))}
                </ul>
                {entry.sources.map((source) => (
                  <p
                    className="reflection-note"
                    key={`${source.chat_id}:${source.message_id}`}
                  >
                    {chats.find((chat) => chat.id === source.chat_id)?.title ||
                      "Conversation"}{" "}
                    · message {source.message_id}
                  </p>
                ))}
              </details>
            </div>
          </div>
        ))}
      </details>
    </section>
  );
}
