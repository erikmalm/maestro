import { useEffect } from "react";
import { LoaderCircle, Search } from "lucide-react";
import * as api from "./api";
import type { ProviderStatus, WebSearchStatus } from "./api";
import { KeyStorage, NumberFields, useSetupForm } from "./SetupForm";

export default function OllamaSearchSetup({
  initialStatus,
  provider,
  onChange,
}: {
  initialStatus: WebSearchStatus;
  provider: ProviderStatus;
  onChange: () => void;
}) {
  const {
    status,
    config,
    setStatus,
    setConfig,
    update,
    apiKey,
    setApiKey,
    persist,
    setPersist,
    dirty,
    busy,
    error,
    notice,
    act,
  } = useSetupForm(
    initialStatus,
    onChange,
    "Could not update search settings.",
  );

  useEffect(() => {
    if (!status.paused_until) return;
    const remaining = Date.parse(status.paused_until) - Date.now() + 20;
    if (!Number.isFinite(remaining) || remaining > 2147483647) return;
    const timer = window.setTimeout(
      () => {
        setStatus((previous) => ({ ...previous, paused_until: null }));
      },
      Math.max(0, remaining),
    );
    return () => window.clearTimeout(timer);
  }, [status.paused_until]);

  const localReady =
    provider.config.protocol === "ollama" &&
    provider.config.ollama_context_tokens >= 8192;
  const verified =
    status.credentials_present && !!status.tested_at && !apiKey.trim();
  const inCooldown =
    !!status.paused_until && Date.parse(status.paused_until) > Date.now();

  return (
    <section className="card reflection-box ollama-search-setup">
      <div className="reflection-title">
        <div>
          <h2>Optional web search</h2>
          <p>Search for local Ollama models. Queries go to Ollama.com.</p>
        </div>
        <span className="badge neutral">
          {status.config.enabled ? "Enabled" : "Disabled"}
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
          if (config.enabled && (!verified || !localReady)) return;
          void act(
            "save",
            () => api.saveWebSearch(config, apiKey, persist),
            "Search settings saved.",
          );
        }}
      >
        <p className="reflection-note">
          One search maximum per message. Search fees are not reported or
          included in LLM cost estimates.
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
            disabled={status.managed_credentials}
            value={apiKey}
            placeholder={
              status.credentials_present
                ? "Leave blank to keep the current search key"
                : "Enter your Ollama search API key"
            }
            onChange={(event) => {
              setApiKey(event.target.value);
              setConfig({ enabled: false });
            }}
          />
        </label>
        <KeyStorage
          status={status}
          persist={persist}
          onChange={setPersist}
          search
        />
        <div className="reflection-fields">
          <NumberFields
            values={config}
            onChange={(key, value) => setConfig({ [key]: value })}
            fields={[
              ["daily_limit", "Daily search cap", 0, 200],
              ["max_results", "Results per search", 1, 3],
            ]}
          />
        </div>
        <label className="reflection-check">
          <input
            type="checkbox"
            checked={config.enabled}
            disabled={!!busy || (!config.enabled && (!verified || !localReady))}
            onChange={(event) => setConfig({ enabled: event.target.checked })}
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
            Select local Ollama and set its context size to at least 8192 tokens
            in Local worker settings, then save. Search excerpts need that
            space.
          </p>
        )}
        <p className="reflection-note">
          Testing sends one fixed public query to Ollama.com and counts against
          your daily cap. Testing does not enable automatic search; check the
          option and save to enable it. Maestro uses hosted search without
          requesting each result website.
        </p>
        <div className="reflection-actions">
          <button
            className="button primary"
            type="submit"
            disabled={!!busy || (config.enabled && (!verified || !localReady))}
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
              void act(
                "test",
                async () => {
                  if (dirty)
                    update(
                      await api.saveWebSearch(
                        { ...config, enabled: false },
                        apiKey,
                        persist,
                      ),
                    );
                  return api.testWebSearch();
                },
                "Search connection verified. The test counted toward your daily cap.",
                api.loadWebSearch,
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
            disabled={
              !!busy ||
              !status.credentials_present ||
              !!status.managed_credentials
            }
            onClick={() => {
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
            The daily cap blocks further searches and connection tests. Increase
            it or wait until tomorrow.
          </p>
        )}
        {status.tested_at && (
          <p className="reflection-note">
            Search connection tested{" "}
            {new Date(status.tested_at).toLocaleString()}.
          </p>
        )}
      </form>
    </section>
  );
}
