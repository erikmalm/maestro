import { useEffect, useState } from "react";
import { Check, LoaderCircle, ShieldCheck } from "lucide-react";
import * as api from "./api";
import type { ProviderConfig, ProviderStatus } from "./api";

// Standard text rates, checked against https://developers.openai.com/api/docs/pricing.
// Expired or unlisted rates require manual confirmation; never infer model prices from an ID.
const OPENAI_PRICES_CHECKED = "2026-10-01";
const OPENAI_PRICES: Record<
  string,
  { input: number; output: number; label: string }
> = {
  "gpt-6-luna": { input: 0.1, output: 0.5, label: "GPT-6 Luna · economical" },
  "gpt-6.1-sol": { input: 2, output: 10, label: "GPT-6.1 Sol · balanced" },
  "gpt-6-astra": { input: 10, output: 50, label: "GPT-6 Astra · highest cost" },
};
function openAIPrices(model: string) {
  return Object.hasOwn(OPENAI_PRICES, model) ? OPENAI_PRICES[model] : undefined;
}
function pricesAreCurrent() {
  const age = Date.now() - Date.parse(OPENAI_PRICES_CHECKED);
  return age >= 0 && age <= 30 * 86400000;
}

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
  const isOllama = config?.protocol === "ollama";
  const isOpenAI =
    !isOllama &&
    config?.base_url.replace(/\/+$/, "") === "https://api.openai.com/v1";
  const sameConnection =
    !!config &&
    !!status &&
    config.base_url.replace(/\/+$/, "") === status.config.base_url &&
    isOllama === (status.config.protocol === "ollama");
  const credentialsRequired = sameConnection
    ? status.credentials_required !== false
    : !isOllama;
  const models = sameConnection ? status.models : [];
  const pricesCurrent = pricesAreCurrent();
  const preset = isOpenAI && config ? openAIPrices(config.model) : undefined;
  const canSave =
    !!config?.model.trim() &&
    !!config.pricing_verified &&
    (!credentialsRequired ||
      !!apiKey.trim() ||
      (!!status?.credentials_present && sameConnection));
  function selectProvider(provider: string) {
    if (!config) return;
    setApiKey("");
    setError("");
    setNotice("");
    setConfig({
      ...config,
      base_url:
        provider === "ollama"
          ? "http://127.0.0.1:11434"
          : provider === "openai"
            ? "https://api.openai.com/v1"
            : "",
      protocol:
        provider === "ollama"
          ? "ollama"
          : provider === "openai"
            ? "responses"
            : "chat_completions",
      model: "",
      input_usd_per_million: 0,
      output_usd_per_million: 0,
      pricing_verified: provider === "ollama",
    });
  }
  function selectModel(model: string) {
    if (!config) return;
    const prices =
      isOpenAI && pricesAreCurrent() ? openAIPrices(model) : undefined;
    setConfig({
      ...config,
      model,
      ...(prices ? { protocol: "responses" as const } : {}),
      input_usd_per_million: prices?.input ?? 0,
      output_usd_per_million: prices?.output ?? 0,
      pricing_verified: isOllama || !!prices,
    });
    setNotice("");
  }
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
          ? { pricing_verified: isOllama }
          : {}),
      });
    setNotice("");
  };

  return (
    <section className="card reflection-box provider-setup">
      <div className="reflection-title">
        <div>
          <h2>Model connection</h2>
          <p>Choose a local model or connect to a model provider.</p>
        </div>
        <span className="badge neutral">
          {isOllama
            ? "Local · no API key"
            : sameConnection && status?.credentials_present
              ? "API key configured"
              : "No API key"}
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
            if (!canSave) return;
            const submittedKey = apiKey;
            setApiKey("");
            void act(
              "save",
              () => api.saveProvider(config, submittedKey, persist),
              "Chat model settings saved. Return to Workspace to send a message.",
            );
          }}
        >
          <label>
            Model provider
            <select
              aria-label="Model provider"
              value={isOllama ? "ollama" : isOpenAI ? "openai" : "custom"}
              disabled={!!busy}
              onChange={(event) => selectProvider(event.target.value)}
            >
              <option value="ollama">Ollama · local</option>
              <option value="openai">OpenAI</option>
              <option value="custom">Custom API</option>
            </select>
          </label>
          <p className="reflection-note">
            {isOllama
              ? "Start Ollama locally, connect to load installed models, choose one, then save. No API key is needed."
              : "An API key can access multiple models; it is not attached to one. Connect to load your model list, choose a chat model, then save."}
            {!isOllama &&
              sameConnection &&
              status.credentials_present &&
              !config.model &&
              " Your key is saved, but no chat model is selected yet."}
          </p>
          <label>
            API base URL
            <input
              type="url"
              required
              maxLength={2048}
              value={config.base_url}
              onChange={(event) => field("base_url", event.target.value)}
              placeholder={
                isOllama
                  ? "http://127.0.0.1:11434"
                  : "https://api.openai.com/v1"
              }
            />
          </label>
          <p className="reflection-note">
            {isOllama
              ? "Use the local Ollama server address without /v1. Chat stays on this computer; remote Ollama addresses are blocked."
              : "OpenAI or a compatible HTTPS endpoint. A local server can use HTTP on a loopback address. Chat and the API key are sent to this endpoint."}
          </p>
          <div className="reflection-fields">
            <label>
              API protocol
              <select
                aria-label="API protocol"
                value={config.protocol}
                disabled={isOllama}
                onChange={(event) =>
                  field(
                    "protocol",
                    event.target.value as ProviderConfig["protocol"],
                  )
                }
              >
                <option value="responses">Responses</option>
                <option value="chat_completions">Chat Completions</option>
                {isOllama && <option value="ollama">Ollama native API</option>}
              </select>
            </label>
            <label>
              Model ID
              <input
                maxLength={200}
                list="provider-models"
                value={config.model}
                onChange={(event) => selectModel(event.target.value)}
                placeholder="Enter a model ID"
              />
              <datalist id="provider-models">
                {models.map((model) => (
                  <option value={model} key={model} />
                ))}
              </datalist>
            </label>
          </div>
          {models.length > 0 && (
            <label>
              Available models
              <select
                aria-label="Available models"
                value={models.includes(config.model) ? config.model : ""}
                onChange={(event) => selectModel(event.target.value)}
              >
                <option value="">Choose a model from this connection</option>
                {[...models]
                  .sort(
                    (a, b) =>
                      Number(!!(isOpenAI && openAIPrices(b))) -
                        Number(!!(isOpenAI && openAIPrices(a))) ||
                      a.localeCompare(b),
                  )
                  .map((model) => (
                    <option key={model} value={model}>
                      {isOpenAI ? (openAIPrices(model)?.label ?? model) : model}
                    </option>
                  ))}
              </select>
            </label>
          )}
          {isOllama &&
            sameConnection &&
            status.tested_at &&
            models.length === 0 && (
              <p className="reflection-note">
                No installed models found. Install a model in Ollama, then
                connect again.
              </p>
            )}
          {!isOllama && (
            <>
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
                    sameConnection && status.credentials_present
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
                {sameConnection &&
                  status.credentials_present &&
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
                      onChange={(event) =>
                        field(key, Number(event.target.value))
                      }
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
                Use these prices for cost estimates
              </label>
              <p className="reflection-note">
                Costs use provider-reported token counts and your saved prices.
                They are estimates; provider billing may include discounts or
                other charges.
                {preset &&
                  pricesCurrent &&
                  config.input_usd_per_million === preset.input &&
                  config.output_usd_per_million === preset.output && (
                    <>
                      {" "}
                      Standard OpenAI rates filled automatically; checked{" "}
                      {OPENAI_PRICES_CHECKED}.
                    </>
                  )}
                {isOpenAI && (
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
            </>
          )}
          {isOllama && (
            <p className="reflection-note">
              Local inference has $0 provider API charges. Hardware and
              electricity costs are excluded. Token usage is still tracked.
            </p>
          )}
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
            <button
              type="submit"
              className="button primary"
              disabled={!!busy || !canSave}
            >
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
              disabled={!!busy}
              onClick={() => {
                const submittedKey = apiKey;
                setApiKey("");
                void act(
                  "test",
                  async () => {
                    if (dirty)
                      update(
                        await api.saveProvider(config, submittedKey, persist),
                      );
                    return api.testProvider();
                  },
                  isOllama
                    ? "Local model list loaded. Choose an installed model and save; sending a message verifies generation."
                    : "API key accepted. Choose a chat model and save; sending a message verifies generation.",
                );
              }}
            >
              {busy === "test" && <LoaderCircle size={15} className="spin" />}
              Connect and load models
            </button>
            {!isOllama && (
              <button
                type="button"
                className="button subtle"
                disabled={
                  !!busy || !sameConnection || !status.credentials_present
                }
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
            )}
          </div>
          <p className="reflection-note">
            {!config.model.trim()
              ? "Choose a model before saving the chat connection. "
              : !isOllama && !config.pricing_verified
                ? "Confirm this model's prices before saving the chat connection. "
                : !canSave
                  ? "Connect an API key for this endpoint before saving. "
                  : ""}
            {dirty
              ? "Save to apply your model and cost settings. Connect and load models also saves your changes."
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
