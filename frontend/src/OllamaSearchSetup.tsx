import { useEffect, useState } from "react";
import { LoaderCircle, Search, ShieldCheck } from "lucide-react";
import * as api from "./api";
import type { ProviderStatus, WebSearchConfig, WebSearchStatus } from "./api";

export default function OllamaSearchSetup({
  provider,
  onChange,
}: {
  provider: ProviderStatus;
  onChange: () => void;
}) {
  const [status, setStatus] = useState<WebSearchStatus | null>(null);
  const [config, setConfig] = useState<WebSearchConfig | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [persist, setPersist] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  function update(next: WebSearchStatus) {
    setStatus(next);
    setConfig(next.config);
    onChange();
  }

  useEffect(() => {
    let active = true;
    void api
      .loadWebSearch()
      .then((next) => {
        if (active) {
          setStatus(next);
          setConfig(next.config);
        }
      })
      .catch((reason: unknown) => {
        if (active)
          setError(
            reason instanceof Error
              ? reason.message
              : "Could not load search settings.",
          );
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!status?.paused_until) return;
    const remaining = Date.parse(status.paused_until) - Date.now() + 20;
    if (!Number.isFinite(remaining) || remaining > 2147483647) return;
    const timer = window.setTimeout(
      () => {
        setStatus((previous) =>
          previous ? { ...previous, paused_until: null } : previous,
        );
      },
      Math.max(0, remaining),
    );
    return () => window.clearTimeout(timer);
  }, [status?.paused_until]);

  async function act(
    name: string,
    action: () => Promise<WebSearchStatus>,
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
          : "Could not update search settings.",
      );
      if (name === "test") {
        try {
          update(await api.loadWebSearch());
        } catch {
          // Keep the original error if the local status request also fails.
        }
      }
    } finally {
      setBusy("");
    }
  }

  const localReady =
    provider.config.protocol === "ollama" &&
    provider.config.ollama_context_tokens >= 8192;
  const verified =
    !!status?.credentials_present && !!status.tested_at && !apiKey.trim();
  const dirty =
    !!config &&
    !!status &&
    (JSON.stringify(config) !== JSON.stringify(status.config) ||
      apiKey.length > 0);
  const inCooldown =
    !!status?.paused_until && Date.parse(status.paused_until) > Date.now();

  return (
    <section className="card reflection-box ollama-search-setup">
      <div className="reflection-title">
        <div>
          <h2>Optional web search</h2>
          <p>Your model stays local. Search queries go to Ollama.com.</p>
        </div>
        <span className="badge neutral">
          {status?.config.enabled ? "Enabled" : "Disabled"}
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
            ? "Reload to try loading search settings again."
            : "Loading search settings…"}
        </p>
      ) : (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (config.enabled && (!verified || !localReady)) return;
            const submittedKey = apiKey;
            setApiKey("");
            void act(
              "save",
              () => api.saveWebSearch(config, submittedKey, persist),
              "Search settings saved.",
            );
          }}
        >
          <p className="reflection-note">
            {status.searches_today} / {status.config.daily_limit} searches today
            · {status.remaining_today} remaining. One search maximum per
            message. Search fees are not reported or included in LLM cost
            estimates.
          </p>
          {inCooldown && status.paused_until && (
            <p role="status" className="reflection-note">
              Search service cooldown until{" "}
              {new Date(status.paused_until).toLocaleString()}.
            </p>
          )}
          <label>
            Ollama search API key
            <input
              type="password"
              autoComplete="new-password"
              spellCheck={false}
              maxLength={4096}
              value={apiKey}
              placeholder={
                status.credentials_present
                  ? "Leave blank to keep the current search key"
                  : "Enter your Ollama search API key"
              }
              onChange={(event) => {
                setApiKey(event.target.value);
                setConfig({ ...config, enabled: false });
                setNotice("");
              }}
            />
          </label>
          <label className="reflection-check">
            <input
              type="checkbox"
              checked={persist}
              onChange={(event) => setPersist(event.target.checked)}
            />
            Save search key in Windows Credential Manager
          </label>
          <p className="reflection-note">
            <ShieldCheck size={15} />
            {persist
              ? "The search key is stored outside the repository."
              : "A new search key stays in server memory until Maestro stops."}
            {status.credentials_present &&
              ` Current source: ${status.credential_source.replaceAll("_", " ")}.`}
          </p>
          <div className="reflection-fields">
            <label>
              Daily search cap
              <input
                required
                type="number"
                min="0"
                max="200"
                step="1"
                value={config.daily_limit}
                onChange={(event) => {
                  setConfig({
                    ...config,
                    daily_limit: Number(event.target.value),
                  });
                  setNotice("");
                }}
              />
            </label>
            <label>
              Results per search
              <input
                required
                type="number"
                min="1"
                max="3"
                step="1"
                value={config.max_results}
                onChange={(event) => {
                  setConfig({
                    ...config,
                    max_results: Number(event.target.value),
                  });
                  setNotice("");
                }}
              />
            </label>
          </div>
          <label className="reflection-check">
            <input
              type="checkbox"
              checked={config.enabled}
              disabled={
                !!busy || (!config.enabled && (!verified || !localReady))
              }
              onChange={(event) => {
                setConfig({ ...config, enabled: event.target.checked });
                setNotice("");
              }}
            />
            Let Maestro decide when to search
          </label>
          {!verified && (
            <p className="reflection-note">
              Save and test your search key before enabling automatic search.
            </p>
          )}
          {!localReady && (
            <p className="reflection-note">
              Select local Ollama and set its context size to at least 8192
              tokens in Local worker settings, then save. Search excerpts need
              that space.
            </p>
          )}
          <p className="reflection-note">
            Testing sends one fixed public query to Ollama.com and counts
            against your daily cap. Testing does not enable automatic search;
            check the option and save to enable it. Maestro uses hosted search
            without requesting each result website.
          </p>
          <div className="reflection-actions">
            <button
              className="button primary"
              type="submit"
              disabled={
                !!busy || (config.enabled && (!verified || !localReady))
              }
            >
              {busy === "save" && <LoaderCircle size={15} className="spin" />}
              Save search settings
            </button>
            <button
              className="button secondary"
              type="button"
              disabled={
                !!busy ||
                inCooldown ||
                (!status.credentials_present && !apiKey.trim()) ||
                config.daily_limit <= status.searches_today
              }
              onClick={() => {
                const submittedKey = apiKey;
                setApiKey("");
                void act(
                  "test",
                  async () => {
                    if (dirty)
                      update(
                        await api.saveWebSearch(
                          { ...config, enabled: false },
                          submittedKey,
                          persist,
                        ),
                      );
                    return api.testWebSearch();
                  },
                  "Search connection verified. The test counted toward your daily cap.",
                );
              }}
            >
              {busy === "test" ? (
                <LoaderCircle size={15} className="spin" />
              ) : (
                <Search size={15} />
              )}
              Test search connection
            </button>
            <button
              className="button subtle"
              type="button"
              disabled={!!busy || !status.credentials_present}
              onClick={() => {
                setApiKey("");
                void act(
                  "remove",
                  api.deleteWebSearchKey,
                  "Search key removed. Automatic search is disabled.",
                );
              }}
            >
              Remove search key
            </button>
          </div>
          {config.daily_limit <= status.searches_today && (
            <p className="reflection-note">
              The daily cap blocks further searches and connection tests.
              Increase it or wait until tomorrow.
            </p>
          )}
          {status.tested_at && (
            <p className="reflection-note">
              Search connection tested{" "}
              {new Date(status.tested_at).toLocaleString()}.
            </p>
          )}
        </form>
      )}
    </section>
  );
}
