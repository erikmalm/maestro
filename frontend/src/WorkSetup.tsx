import { Check, LoaderCircle } from "lucide-react";
import * as api from "./api";
import type { ProviderStatus, WorkStatus } from "./api";
import { NumberFields, useSetupForm } from "./SetupForm";

export default function WorkSetup({
  initialStatus,
  provider,
  onChange,
}: {
  initialStatus: WorkStatus;
  provider: ProviderStatus;
  onChange: (status: WorkStatus) => void;
}) {
  const { status, config, setConfig, busy, error, notice, act } = useSetupForm(
    initialStatus,
    onChange,
    "Could not update task settings.",
  );
  const local = provider.config.protocol === "ollama";
  const models = local ? provider.models : [];

  return (
    <section className="card reflection-box">
      <div className="reflection-title">
        <div>
          <h2>Task models &amp; reflection</h2>
          <p>Choose local models and limits for background work.</p>
        </div>
        <span className="badge neutral">
          {status.worker_available
            ? config.enabled
              ? "Enabled"
              : "Paused"
            : "Not running"}
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
          An explicit choice stays selected if a model is removed.
          {!local &&
            " Select Ollama in Model connection to use local task models."}
        </p>
        <label className="reflection-check">
          <input
            type="checkbox"
            checked={config.enabled}
            disabled={!!busy || !status.worker_available}
            onChange={(event) => setConfig({ enabled: event.target.checked })}
          />
          Enable background reflection
        </label>
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
            limits will apply when the worker is available.
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
