import { Check, LoaderCircle } from "lucide-react";
import type { ProviderStatus, WorkConfig, WorkStatus } from "./api";
import { NumberFields, useSetupForm } from "./SetupForm";

export default function WorkSetup({
  initialStatus,
  provider,
  onSave,
}: {
  initialStatus: WorkStatus;
  provider: ProviderStatus;
  onSave: (config: WorkConfig) => Promise<WorkStatus>;
}) {
  const { status, config, setConfig, dirty, busy, error, notice, act } =
    useSetupForm(initialStatus, () => {}, "Could not update task settings.");
  const local = provider.config.protocol === "ollama";
  const models = local ? provider.models : [];
  return (
    <section className="card reflection-box">
      <div className="reflection-title">
        <div>
          <h2>Task models &amp; reflection</h2>
          <p>
            Build private memory and explore improvements to Maestro's working
            style while chat is idle.
          </p>
        </div>
        <span className="badge neutral">
          {status.config.enabled ? "Enabled" : "Paused"}
        </span>
      </div>
      {error && (
        <div className="error-banner" role="alert">
          {error}
        </div>
      )}
      {notice && <p role="status">{notice}</p>}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void act("save", () => onSave(config), "Task settings saved.");
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
                  ? { periodic_reflection: false, auto_create_tasks: false }
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
              setConfig({
                periodic_reflection: event.target.checked,
                ...(!event.target.checked ? { auto_create_tasks: false } : {}),
              })
            }
          />
          Reflect periodically on identity and working style
        </label>
        <label className="reflection-check">
          <input
            type="checkbox"
            checked={config.auto_create_tasks ?? false}
            disabled={
              !!busy || !config.auto_curate || !config.periodic_reflection
            }
            onChange={(event) =>
              setConfig({ auto_create_tasks: event.target.checked })
            }
          />
          AI can create tasks during reflection
        </label>
        <p className="reflection-note">
          Reviewed follow-ups and self-improvement experiments are added to
          Tasks with an initiator and suggested assignee.
        </p>
        <label>
          Reflection interval (minutes)
          <input
            type="number"
            required
            min={5}
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
          15 minutes helps evaluate recurring passes; 360 minutes is six hours.
          Reflection reviews current practices even without new chats. Chat
          activity or a running generation can delay a pass; daily allowances
          can pause it.
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
                [
                  "background_context_tokens",
                  "Background context tokens",
                  8192,
                  131072,
                ],
                [
                  "reflection_exchange_count",
                  "Exchanges per periodic review",
                  1,
                  32,
                ],
                [
                  "reflection_context_characters",
                  "Conversation review characters",
                  1000,
                  200000,
                ],
                ["max_output_tokens", "Reflection output tokens", 1, 16384],
                ["max_jobs_per_day", "Daily reflection jobs", 0, 1000],
                ["max_tokens_per_day", "Daily reflection tokens", 0, 10000000],
                ["debounce_seconds", "Debounce (seconds)", 0, 3600],
                ["idle_seconds", "Chat idle time (seconds)", 0, 3600],
                ["timeout_seconds", "Job timeout (seconds)", 1, 1800],
                ["memory_recall_count", "Memories per reply", 0, 100],
                [
                  "memory_recall_characters",
                  "Memory context characters",
                  0,
                  200000,
                ],
              ]}
            />
          </div>
          <p className="reflection-note">
            Set either memory limit to 0 to turn off recall. Saved memory is
            used only in local chat with hosted web search off. Reflection
            limits apply to each call and the daily allowance. Larger contexts
            let reviews read more of each exchange and use more memory.
            Conversation review characters are shared across the sampled
            exchanges; Ollama's context setting can lower the effective limit.
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
    </section>
  );
}
