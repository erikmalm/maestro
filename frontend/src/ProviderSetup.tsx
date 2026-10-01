import { useEffect, useState } from "react";
import { Check, LoaderCircle, ShieldCheck } from "lucide-react";
import * as api from "./api";
import type { ProviderConfig, ProviderStatus } from "./api";

export default function ProviderSetup({
  onChange,
}: {
  onChange: (status: ProviderStatus) => void;
}) {
  const [status, setStatus] = useState<ProviderStatus | null>(null);
  const [config, setConfig] = useState<ProviderConfig | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [persist, setPersist] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  function update(next: ProviderStatus) {
    setStatus(next);
    setConfig(next.config);
    onChange(next);
  }

  useEffect(() => {
    let active = true;
    void api
      .loadProvider()
      .then((next) => {
        if (active) update(next);
      })
      .catch((reason: unknown) => {
        if (active)
          setError(
            reason instanceof Error
              ? reason.message
              : "Could not load the connection.",
          );
      });
    return () => {
      active = false;
    };
  }, []);

  async function act(
    name: string,
    action: () => Promise<ProviderStatus>,
    message: string,
  ) {
    setBusy(name);
    setError("");
    setNotice("");
    try {
      update(await action());
      setNotice(message);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not update the connection.",
      );
    } finally {
      setBusy("");
    }
  }

  const dirty =
    !!config &&
    !!status &&
    (JSON.stringify(config) !== JSON.stringify(status.config) ||
      apiKey.length > 0);
  const field = <K extends keyof ProviderConfig>(
    key: K,
    value: ProviderConfig[K],
  ) => {
    if (config)
      setConfig({
        ...config,
        [key]: value,
        ...([
          "base_url",
          "protocol",
          "model",
          "input_usd_per_million",
          "output_usd_per_million",
        ].includes(key)
          ? { pricing_verified: false }
          : {}),
      });
    setNotice("");
  };

  return (
    <section className="card reflection-box provider-setup">
      <div className="reflection-title">
        <div>
          <h2>Model connection</h2>
          <p>Choose the API endpoint and model used for chat.</p>
        </div>
        <span className="badge neutral">
          {status?.credentials_present ? "API key configured" : "No API key"}
        </span>
      </div>
      {error && (
        <div className="error-banner" role="alert">
          {error}
        </div>
      )}
      {notice && <p role="status">{notice}</p>}
      {!config || !status ? (
        <p>
          {error
            ? "Connection settings could not be loaded. Reload to try again."
            : "Loading connection settings…"}
        </p>
      ) : (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            const submittedKey = apiKey;
            setApiKey("");
            void act(
              "save",
              () => api.saveProvider(config, submittedKey, persist),
              "Connection settings saved.",
            );
          }}
        >
          <label>
            API base URL
            <input
              type="url"
              required
              maxLength={2048}
              value={config.base_url}
              onChange={(event) => field("base_url", event.target.value)}
              placeholder="https://api.openai.com/v1"
            />
          </label>
          <p className="reflection-note">
            OpenAI or a compatible HTTPS endpoint. A local server can use HTTP
            on a loopback address. Chat and the API key are sent to this
            endpoint.
          </p>
          <div className="reflection-fields">
            <label>
              API protocol
              <select
                aria-label="API protocol"
                value={config.protocol}
                onChange={(event) =>
                  field(
                    "protocol",
                    event.target.value as ProviderConfig["protocol"],
                  )
                }
              >
                <option value="responses">Responses</option>
                <option value="chat_completions">Chat Completions</option>
              </select>
            </label>
            <label>
              Model ID
              <input
                maxLength={200}
                list="provider-models"
                value={config.model}
                onChange={(event) => field("model", event.target.value)}
                placeholder="Enter a model ID"
              />
              <datalist id="provider-models">
                {status.models.map((model) => (
                  <option value={model} key={model} />
                ))}
              </datalist>
            </label>
          </div>
          {status.models.length > 0 && (
            <label>
              Available models
              <select
                aria-label="Available models"
                value={status.models.includes(config.model) ? config.model : ""}
                onChange={(event) => field("model", event.target.value)}
              >
                <option value="">Choose a model from this connection</option>
                {status.models.map((model) => (
                  <option key={model} value={model}>
                    {model}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label>
            API key
            <input
              type="password"
              autoComplete="new-password"
              spellCheck={false}
              maxLength={4096}
              value={apiKey}
              onChange={(event) => {
                setApiKey(event.target.value);
                setNotice("");
              }}
              placeholder={
                status.credentials_present
                  ? "Leave blank to keep the current key"
                  : "Enter your provider API key"
              }
            />
          </label>
          <label className="reflection-check">
            <input
              type="checkbox"
              checked={persist}
              onChange={(event) => setPersist(event.target.checked)}
            />
            Save a new key in Windows Credential Manager
          </label>
          <p className="reflection-note">
            <ShieldCheck size={15} />
            {persist
              ? "Keys are kept outside the repository and never returned by the API."
              : "A new key stays in server memory and is lost when Maestro stops."}
            {status.credentials_present &&
              ` Current source: ${status.credential_source.replaceAll("_", " ")}.`}
          </p>
          <div className="form-section-label">COST ESTIMATES · USD</div>
          <div className="reflection-fields">
            {(
              [
                ["input_usd_per_million", "Input USD / 1M tokens"],
                ["output_usd_per_million", "Output USD / 1M tokens"],
              ] as const
            ).map(([key, label]) => (
              <label key={key}>
                {label}
                <input
                  required
                  type="number"
                  min="0"
                  max="10000"
                  step="any"
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
            I verified these prices for this model
          </label>
          <p className="reflection-note">
            Costs use provider-reported token counts and your saved prices. They
            are estimates; provider billing may include discounts or other
            charges.
            {config.base_url === "https://api.openai.com/v1" && (
              <>
                {" "}
                <a
                  href="https://developers.openai.com/api/docs/pricing"
                  target="_blank"
                  rel="noreferrer"
                >
                  OpenAI model prices
                </a>
              </>
            )}
          </p>
          <label>
            Maximum output tokens per reply
            <input
              required
              type="number"
              min="64"
              max="32768"
              step="1"
              value={config.max_output_tokens}
              onChange={(event) =>
                field("max_output_tokens", Number(event.target.value))
              }
            />
          </label>
          <div className="reflection-actions">
            <button type="submit" className="button primary" disabled={!!busy}>
              {busy === "save" ? (
                <LoaderCircle size={15} className="spin" />
              ) : (
                <Check size={15} />
              )}
              Save connection
            </button>
            <button
              type="button"
              className="button secondary"
              disabled={!!busy || dirty}
              onClick={() =>
                void act(
                  "test",
                  api.testProvider,
                  "Model-list request succeeded. Send a chat message to verify generation.",
                )
              }
            >
              {busy === "test" && <LoaderCircle size={15} className="spin" />}
              Test connection
            </button>
            <button
              type="button"
              className="button subtle"
              disabled={!!busy || !status.credentials_present}
              onClick={() => {
                setApiKey("");
                void act(
                  "remove",
                  api.deleteProviderKey,
                  "Saved API key removed.",
                );
              }}
            >
              Remove API key
            </button>
          </div>
          <p className="reflection-note">
            {dirty
              ? "Save your changes before testing."
              : status.tested_at
                ? `Model-list access checked ${new Date(status.tested_at).toLocaleString()}.`
                : "Connection has not been tested."}{" "}
            Testing requests the model list and makes no generation call.
          </p>
        </form>
      )}
    </section>
  );
}
