import { useEffect, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import Reply from "./Reply";
import AnswerFeedback from "./AnswerFeedback";
import MemorySetup from "./MemorySetup";
import WorkSetup from "./WorkSetup";
import ReflectionJournal from "./ReflectionJournal";
import {
  ArrowRight,
  Check,
  ChevronDown,
  CheckSquare,
  Coins,
  Globe,
  LoaderCircle,
  Menu,
  MessageCircle,
  Network,
  Pencil,
  Plus,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Trash2,
  X,
} from "lucide-react";
import * as api from "./api";
import type {
  ContextArchiveStatus,
  ContextCapture,
  ContextMode,
  ContextSearchResult,
  Limits,
  LocalRequest,
  MessageFeedback,
  Task,
  WorkConfig,
  WorkStatus,
  Workspace,
  WebSearchStatus,
} from "./api";
import ProviderSetup from "./ProviderSetup";
import OllamaSearchSetup from "./OllamaSearchSetup";
import { NumberFields, useSetupForm } from "./SetupForm";

type Page = "workspace" | "tasks" | "memory" | "settings";
type Panel = "task" | "usage" | "rename-chat" | "delete-chat" | null;
type SavedSourceSelection = {
  id: string;
  expectedHash?: string;
  expectedManifestHash?: string;
};
const money = (value: number) =>
  `$${value.toFixed(value > 0 && value < 0.01 ? 4 : 2)}`;
const tokens = (value: number) =>
  value >= 1000 ? `${(value / 1000).toFixed(1)}k` : String(value);
const navigation = [
  { id: "workspace", name: "Workspace", icon: MessageCircle },
  { id: "tasks", name: "Tasks", icon: CheckSquare },
  { id: "memory", name: "Memory", icon: Network },
  { id: "settings", name: "Settings", icon: Settings2 },
] as const;

function sourceURL(value: string) {
  try {
    const url = new URL(value);
    return ["https:", "http:"].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

function sourceDate(value: string | null) {
  if (value === null) return "date unknown";
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? date.toLocaleString() : value;
}

function archiveSize(value: number) {
  if (value < 1024) return `${value.toLocaleString()} B`;
  if (value < 1048576) return `${(value / 1024).toFixed(1)} KiB`;
  if (value < 1073741824) return `${(value / 1048576).toFixed(1)} MiB`;
  return `${(value / 1073741824).toFixed(1)} GiB`;
}

function ArchiveAccounting({
  status,
  compact = false,
}: {
  status: ContextArchiveStatus;
  compact?: boolean;
}) {
  const capture = status.last_capture;
  return (
    <div className="archive-accounting" aria-label="Archive storage usage">
      <p>
        {status.indexed_count.toLocaleString()} saved source snapshots
        {!compact &&
          ` · ${status.missing_count} missing · ${status.corrupt_count} corrupt`}
      </p>
      <p>
        Archive storage: {archiveSize(status.archive_bytes)}{" "}
        {!compact && `(${status.archive_bytes.toLocaleString()} bytes) `}of{" "}
        {archiveSize(status.config.max_bytes)} ·{" "}
        {status.archive_items.toLocaleString()} of{" "}
        {status.config.max_items.toLocaleString()} files/directories
      </p>
      {capture && (
        <>
          <p>
            Last fresh retrieval {sourceDate(capture.retrieved_at)} ·{" "}
            {capture.sources_saved} of {capture.sources_received} results saved
            · {archiveSize(capture.excerpt_bytes)} excerpt text ·{" "}
            {archiveSize(capture.new_bytes)} new source files
          </p>
          {!compact && (
            <p>
              New text objects: {archiveSize(capture.object_bytes)} · New
              snapshot manifests: {archiveSize(capture.manifest_bytes)}. New
              source-file size excludes archive control files and the private
              index; total archive storage includes control files.
            </p>
          )}
        </>
      )}
    </div>
  );
}

function SearchOptions({
  status,
  archive,
  mode,
  onMode,
  memoryDerived,
  busy,
  onSettings,
  onManageSources,
  onNewChat,
}: {
  status: WebSearchStatus;
  archive?: ContextArchiveStatus;
  mode: ContextMode;
  onMode: (mode: ContextMode) => void;
  memoryDerived: boolean;
  busy: boolean;
  onSettings: () => void;
  onManageSources: () => void;
  onNewChat: () => void;
}) {
  const popup = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const [expanded, setExpanded] = useState(false);
  const savedAvailable = !!archive?.configured && archive.config.enabled;
  const publicPolicy = archive?.config.capture_policy === "all_public";
  const modeLabel =
    mode === "saved_only"
      ? "Saved only"
      : mode === "refresh"
        ? "Fresh search"
        : "Prefer saved";
  const readiness = memoryDerived
    ? "online search blocked in this chat"
    : mode === "saved_only"
      ? "online search off"
      : status.ready
        ? "online search ready"
        : "online search unavailable";
  const indicator =
    mode === "saved_only"
      ? "saved"
      : memoryDerived || !status.ready
        ? "unavailable"
        : "ready";
  const saving = !archive?.configured
    ? "Source saving is not configured."
    : !archive.config.enabled
      ? "Source saving is off."
      : !publicPolicy && !archive.config.public_sources.length
        ? "Source saving needs approved public sources."
        : !archive.available
          ? "Source saving is unavailable."
          : publicPolicy
            ? "Saving eligible public search results."
            : "Saving approved public sources.";
  function close() {
    popup.current?.hidePopover();
    trigger.current?.focus();
  }
  return (
    <div className="search-control">
      <button
        ref={trigger}
        className="search-trigger"
        type="button"
        popoverTarget="chat-search-options"
        aria-label={`Search options: ${modeLabel}; ${readiness}`}
        title={`Search options: ${modeLabel}; ${readiness}`}
        aria-haspopup="dialog"
        aria-expanded={expanded}
        aria-controls="chat-search-options"
      >
        <Globe size={18} aria-hidden="true" />
        <span className={`search-indicator ${indicator}`} aria-hidden="true" />
      </button>
      <div
        id="chat-search-options"
        className="search-options"
        ref={popup}
        popover="auto"
        role="dialog"
        aria-labelledby="search-options-title"
        onBeforeToggle={(event) =>
          setExpanded((event.nativeEvent as ToggleEvent).newState === "open")
        }
        onToggle={(event) => {
          const opened = (event.nativeEvent as ToggleEvent).newState === "open";
          if (opened)
            popup.current
              ?.querySelector<HTMLElement>("select, button")
              ?.focus();
        }}
      >
        <div className="search-options-heading">
          <h3 id="search-options-title">Search options</h3>
          <button
            className="icon-button"
            type="button"
            onClick={close}
            aria-label="Close search options"
          >
            <X size={16} />
          </button>
        </div>
        {archive && (
          <>
            <label className="context-mode-control">
              Source mode
              <select
                value={mode}
                disabled={busy}
                onChange={(event) => onMode(event.target.value as ContextMode)}
              >
                <option value="prefer_saved">Prefer saved</option>
                <option value="refresh">Fresh search</option>
                <option value="saved_only">Saved only</option>
              </select>
            </label>
            <p>
              {mode === "saved_only"
                ? "Saved only uses archived evidence without online search."
                : mode === "refresh"
                  ? "Fresh search requires ready online search and a chat without replies informed by private memory."
                  : "Prefer saved reuses archived evidence locally. Current information, including weather, needs fresh search when ready and permitted for this chat."}
            </p>
          </>
        )}
        <div
          className="search-readiness"
          role="status"
          aria-label="Online search readiness"
        >
          <p>
            <strong>
              {memoryDerived
                ? "Online search is blocked in this chat."
                : mode === "saved_only"
                  ? "Saved only keeps online search off."
                  : status.ready
                    ? "Online search is ready."
                    : "Online search is unavailable."}
            </strong>
          </p>
          {memoryDerived && (
            <p>
              Earlier replies used private memory. Start a new chat to use
              online search.
            </p>
          )}
          {!status.ready && <p>{status.unavailable_reason}</p>}
          {!status.ready &&
            status.paused_until &&
            Date.parse(status.paused_until) > Date.now() && (
              <p>Cooldown ends {sourceDate(status.paused_until)}.</p>
            )}
          {status.ready && (memoryDerived || mode === "saved_only") && (
            <p>
              The online search setup is ready for a chat and source mode that
              permit it.
            </p>
          )}
          {savedAvailable && (!status.ready || memoryDerived) && (
            <p>
              {mode === "refresh"
                ? "Choose Prefer saved or Saved only to use saved public sources locally."
                : "Saved public sources remain available locally."}
            </p>
          )}
          <div className="search-readiness-actions">
            <button
              className="button subtle"
              type="button"
              onClick={() => {
                close();
                onSettings();
              }}
            >
              Open Settings
            </button>
            {memoryDerived && (
              <button
                className="button secondary"
                type="button"
                disabled={busy}
                onClick={onNewChat}
              >
                <Plus size={14} />
                New chat for online search
              </button>
            )}
          </div>
        </div>
        {status.config.enabled && !memoryDerived && mode !== "saved_only" && (
          <p>
            Automatic web search · one search maximum per message ·{" "}
            {status.searches_today} / {status.config.daily_limit} today. Queries
            go to Ollama.com.
          </p>
        )}
        <div
          className="source-saving-status"
          role="status"
          aria-label="Source saving status"
        >
          <p>
            <strong>{saving}</strong>
          </p>
          <p>
            Search can work without saving.{" "}
            {publicPolicy
              ? "When enabled, eligible public results are saved automatically as dated search excerpts."
              : "Only results from approved public URLs are archived."}
          </p>
          {archive?.configured && (
            <>
              <ArchiveAccounting status={archive} compact />
              {!publicPolicy && (
                <p>
                  {archive.config.public_sources.length} approved public{" "}
                  {archive.config.public_sources.length === 1 ? "URL" : "URLs"}
                </p>
              )}
            </>
          )}
          {archive?.last_error && <p>{archive.last_error}</p>}
          <button
            className="button subtle"
            type="button"
            onClick={() => {
              close();
              onManageSources();
            }}
          >
            Manage saved sources
          </button>
        </div>
      </div>
    </div>
  );
}

function Modal({
  title,
  children,
  onClose,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current;
    const opener = document.activeElement;
    element?.showModal();
    return () => {
      element?.close();
      if (opener instanceof HTMLElement && opener.isConnected) opener.focus();
    };
  }, []);
  return (
    <dialog
      ref={dialog}
      className="modal"
      aria-labelledby="modal-title"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="modal-heading">
        <h2 id="modal-title">{title}</h2>
        <button
          className="icon-button"
          onClick={onClose}
          aria-label="Close dialog"
        >
          <X size={19} />
        </button>
      </div>
      {children}
    </dialog>
  );
}

function ContextArchiveSetup({
  initialStatus,
  onAction,
  active,
  onOpenSource,
  removedCaptureIds,
}: {
  initialStatus: ContextArchiveStatus;
  active: boolean;
  onOpenSource: (source: SavedSourceSelection) => void;
  removedCaptureIds: ReadonlySet<string>;
  onAction: (
    action: () => Promise<ContextArchiveStatus>,
  ) => Promise<ContextArchiveStatus>;
}) {
  const { status, config, setConfig, busy, error, notice, act } = useSetupForm(
    initialStatus,
    () => {},
    "Could not update saved web sources.",
  );
  const [rebuildProgress, setRebuildProgress] = useState<
    ContextArchiveStatus["rebuild_progress"] | undefined
  >(undefined);
  const [pauseRequested, setPauseRequested] = useState(false);
  const rebuildContinue = useRef(false);
  const rebuildActive = useRef(active);
  useEffect(() => {
    rebuildActive.current = active;
    setRebuildProgress(undefined);
    return () => {
      rebuildActive.current = false;
      rebuildContinue.current = false;
    };
  }, [active]);
  const progress =
    rebuildProgress === undefined ? status.rebuild_progress : rebuildProgress;
  async function rebuild() {
    if (busy) return;
    rebuildContinue.current = true;
    setPauseRequested(false);
    await act(
      "rebuild",
      () =>
        onAction(() =>
          api.rebuildContextArchive((next) => {
            if (!rebuildActive.current) return false;
            setRebuildProgress(next.rebuild_progress);
            return rebuildContinue.current;
          }),
        ),
      "",
      () => onAction(api.loadContextArchive),
    );
    if (rebuildActive.current) setRebuildProgress(undefined);
  }
  return (
    <section className="card reflection-box context-archive-setup">
      <div className="reflection-title">
        <div>
          <h2 id="saved-web-sources">Saved web sources</h2>
          <p>Reuse dated public search excerpts before searching again.</p>
        </div>
        <span className="badge neutral">
          {!status.configured
            ? "Not configured"
            : !status.available
              ? "Unavailable"
              : status.config.enabled
                ? "Enabled"
                : "Disabled"}
        </span>
      </div>
      {error && (
        <div className="error-banner" role="alert">
          {error}
        </div>
      )}
      {notice && <p role="status">{notice}</p>}
      <p className="reflection-note archive-path">
        {status.configured ? (
          <>Archive folder: {status.path}</>
        ) : (
          "An archive folder has not been configured for this Maestro server."
        )}
      </p>
      {status.last_error && (
        <p className="reflection-note" role="status">
          {status.last_error}
        </p>
      )}
      <ArchiveAccounting status={status} />
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (busy) return;
          void act(
            "save",
            () =>
              onAction(() =>
                api.saveContextArchive({
                  ...config,
                  public_sources: config.public_sources
                    .map((value) => value.trim())
                    .filter(Boolean),
                }),
              ),
            "Saved source settings updated.",
            () => onAction(api.loadContextArchive),
          );
        }}
      >
        <label className="reflection-check">
          <input
            type="checkbox"
            checked={config.enabled}
            disabled={!!busy || (!config.enabled && !status.configured)}
            onChange={(event) => setConfig({ enabled: event.target.checked })}
          />
          Save and reuse public search results
        </label>
        <label>
          Source saving policy
          <select
            value={config.capture_policy ?? "approved_sources"}
            onChange={(event) =>
              setConfig({
                capture_policy: event.target.value as
                  "all_public" | "approved_sources",
              })
            }
          >
            <option value="all_public">
              Save eligible public search results
            </option>
            <option value="approved_sources">Only approved URLs</option>
          </select>
        </label>
        {(config.capture_policy ?? "approved_sources") ===
          "approved_sources" && (
          <>
            <label>
              Approved public source URLs (one per line)
              <textarea
                rows={4}
                maxLength={32000}
                value={config.public_sources.join("\n")}
                placeholder="https://docs.example.org/"
                onChange={(event) =>
                  setConfig({
                    public_sources: event.target.value.split(/\r?\n/),
                  })
                }
              />
            </label>
            <p className="reflection-note">
              URLs without parameters cover that public path and its child
              pages. A URL with parameters approves only that complete URL.
              Approve only public sources suitable for the archive folder.
            </p>
          </>
        )}
        <p className="reflection-note">
          Maestro saves eligible search excerpts automatically, independently of
          the model's reply. Each fresh retrieval has its own dated snapshot;
          these contain excerpts, not full pages. Queries, conversations and
          private memory stay in private Maestro storage. OneDrive folders sync
          the saved excerpts to your account.
        </p>
        <div className="reflection-fields">
          <NumberFields
            values={config}
            onChange={(key, value) => setConfig({ [key]: value })}
            fields={[
              ["reuse_hours", "Reuse saved searches for (hours)", 1, 168],
              ["max_items", "Maximum archive files/directories", 100, 200000],
            ]}
          />
          <label>
            Archive size limit (MiB)
            <input
              required
              type="number"
              min={1}
              max={102400}
              step="any"
              value={config.max_bytes / 1048576}
              onChange={(event) =>
                setConfig({
                  max_bytes: Math.round(Number(event.target.value) * 1048576),
                })
              }
            />
          </label>
        </div>
        <div className="reflection-actions">
          <button className="button primary" disabled={!!busy}>
            {busy === "save" && <LoaderCircle size={15} className="spin" />}
            Save source settings
          </button>
          <button
            className="button secondary"
            type="button"
            disabled={!!busy}
            onClick={() =>
              void act(
                "refresh",
                () => onAction(api.loadContextArchive),
                "Archive status refreshed.",
              )
            }
          >
            Refresh archive status
          </button>
          <button
            className="button subtle"
            type="button"
            disabled={!!busy || !status.available || !status.config.enabled}
            onClick={() => void rebuild()}
          >
            {busy === "rebuild" && <LoaderCircle size={15} className="spin" />}
            {progress && !progress.complete && busy !== "rebuild"
              ? "Continue source index rebuild"
              : "Rebuild source index"}
          </button>
          {busy === "rebuild" && (
            <button
              className="button subtle"
              type="button"
              disabled={pauseRequested}
              onClick={() => {
                rebuildContinue.current = false;
                setPauseRequested(true);
              }}
            >
              {pauseRequested
                ? "Pausing after this batch…"
                : "Pause after current batch"}
            </button>
          )}
        </div>
        {progress && (
          <p className="reflection-note" role="status">
            {progress.complete
              ? "Source index rebuild complete"
              : busy === "rebuild"
                ? "Rebuilding source index"
                : "Source index rebuild paused"}
            : {progress.processed.toLocaleString()} of{" "}
            {progress.total.toLocaleString()} source records checked.
            {!progress.complete &&
              " The previous index stays available until the rebuild finishes."}
          </p>
        )}
        {status.last_indexed_at && (
          <p className="reflection-note">
            Last indexed {sourceDate(status.last_indexed_at)}.
          </p>
        )}
      </form>
      <SavedSourceFinder
        status={status}
        active={active}
        onOpenSource={onOpenSource}
        removedCaptureIds={removedCaptureIds}
      />
    </section>
  );
}

function SavedSourceFinder({
  status,
  active,
  onOpenSource,
  removedCaptureIds,
}: {
  status: ContextArchiveStatus;
  active: boolean;
  onOpenSource: (source: SavedSourceSelection) => void;
  removedCaptureIds: ReadonlySet<string>;
}) {
  const emptyDraft = () => ({
    query: "",
    domain: "",
    retrieved_from: "",
    retrieved_to: "",
    limit: 10,
    allow_stale: true,
  });
  const [draft, setDraft] = useState(emptyDraft);
  const [result, setResult] = useState<ContextSearchResult | null>(null);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState("");
  const revision = useRef(0);
  const keywords = useRef<HTMLInputElement>(null);
  const ready = active && status.configured && status.config.enabled;
  const eligibility = JSON.stringify([
    status.config.capture_policy ?? "approved_sources",
    status.config.public_sources,
    status.config.reuse_hours,
  ]);
  useEffect(() => {
    revision.current += 1;
    setResult(null);
    setSearching(false);
    setError("");
    return () => {
      revision.current += 1;
    };
  }, [ready, eligibility]);
  function edit(next: Partial<typeof draft>) {
    revision.current += 1;
    setDraft((current) => ({ ...current, ...next }));
    setResult(null);
    setError("");
    setSearching(false);
  }
  function clear() {
    edit(emptyDraft());
    keywords.current?.focus();
  }
  async function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!ready || searching) return;
    if (!draft.query.trim()) {
      setError("Enter keywords to search the saved sources.");
      return;
    }
    const request = ++revision.current;
    setSearching(true);
    setError("");
    setResult(null);
    try {
      const next = await api.searchContextArchive({
        query: draft.query.trim(),
        limit: draft.limit,
        allow_stale: draft.allow_stale,
        ...(draft.domain.trim() ? { domain: draft.domain.trim() } : {}),
        ...(draft.retrieved_from
          ? { retrieved_from: draft.retrieved_from }
          : {}),
        ...(draft.retrieved_to ? { retrieved_to: draft.retrieved_to } : {}),
      });
      if (revision.current === request) setResult(next);
    } catch (reason) {
      if (revision.current === request)
        setError(
          reason instanceof Error
            ? reason.message
            : "Could not search the saved sources.",
        );
    } finally {
      if (revision.current === request) setSearching(false);
    }
  }
  const sources =
    result?.sources
      .slice(0, 20)
      .filter((source) => !removedCaptureIds.has(source.capture_id)) ?? [];
  return (
    <section
      className="saved-source-finder"
      aria-labelledby="saved-source-finder-title"
    >
      <h3 id="saved-source-finder-title">Find saved sources</h3>
      <p className="reflection-note" id="saved-source-finder-note">
        Search saved excerpt text locally. This makes no online search or AI
        request. Date filters use when Maestro retrieved a source (UTC dates);
        publication dates may be unknown.
      </p>
      <form onSubmit={(event) => void search(event)} aria-busy={searching}>
        <label>
          Keywords
          <input
            ref={keywords}
            required
            maxLength={512}
            value={draft.query}
            aria-describedby="saved-source-finder-note"
            onChange={(event) => edit({ query: event.target.value })}
          />
        </label>
        <div className="archive-search-fields">
          <label>
            Domain (optional)
            <input
              maxLength={253}
              placeholder="docs.example.org"
              value={draft.domain}
              onChange={(event) => edit({ domain: event.target.value })}
            />
          </label>
          <label>
            Retrieved from
            <input
              type="date"
              value={draft.retrieved_from}
              onChange={(event) => edit({ retrieved_from: event.target.value })}
            />
          </label>
          <label>
            Retrieved to
            <input
              type="date"
              value={draft.retrieved_to}
              onChange={(event) => edit({ retrieved_to: event.target.value })}
            />
          </label>
          <label>
            Maximum results
            <input
              required
              type="number"
              min={1}
              max={20}
              step={1}
              value={draft.limit}
              onChange={(event) => edit({ limit: Number(event.target.value) })}
            />
          </label>
        </div>
        <p className="reflection-note">
          Use an exact hostname without a URL, port or path.
        </p>
        <label className="reflection-check">
          <input
            type="checkbox"
            checked={draft.allow_stale}
            onChange={(event) => edit({ allow_stale: event.target.checked })}
          />
          Include older saved evidence
        </label>
        <div className="reflection-actions">
          <button className="button primary" disabled={!ready || searching}>
            {searching && <LoaderCircle size={15} className="spin" />}
            Search saved sources
          </button>
          <button className="button secondary" type="button" onClick={clear}>
            Clear saved search
          </button>
        </div>
      </form>
      {!ready && active && (
        <p className="reflection-note">
          Enable a configured source archive to search saved text.
        </p>
      )}
      {searching && <p role="status">Searching saved source text…</p>}
      {error && (
        <div className="error-banner" role="alert">
          {error}
        </div>
      )}
      {result && ready && (
        <section
          className="archive-search-results"
          aria-label="Saved source results"
        >
          <p role="status">
            {sources.length
              ? `${sources.length} saved ${sources.length === 1 ? "source" : "sources"} for “${result.query}”.`
              : result.sources.length
                ? "No saved sources remain in these results."
                : result.search_limited
                  ? `No saved sources found for “${result.query}” within this search’s work limit.`
                  : `No saved sources matched “${result.query}”.`}
          </p>
          {result.search_limited && (
            <p className="reflection-note" role="status">
              Saved search reached its work limit. Narrow the keywords, domain
              or retrieval dates.
            </p>
          )}
          <ol>
            {sources.map((source, index) => {
              const url = sourceURL(source.url);
              const domain = url ? new URL(url).hostname : "";
              return (
                <li key={source.capture_id}>
                  <h4>{source.title || "Untitled saved source"}</h4>
                  <p className="archive-result-url">
                    {url ? (
                      <a href={url} target="_blank" rel="noopener noreferrer">
                        {source.url}
                      </a>
                    ) : (
                      source.url
                    )}
                  </p>
                  <p className="reflection-note">
                    {domain && `${domain} · `}Search excerpt · Retrieved{" "}
                    {sourceDate(source.retrieved_at)}
                    {source.stale && <> · Older saved evidence</>}
                    <br />
                    {source.published_at
                      ? `Published ${sourceDate(source.published_at)}`
                      : "Publication date unknown"}
                  </p>
                  <p className="archive-result-excerpt">
                    {source.content.slice(0, 360)}
                    {source.content.length > 360 && "…"}
                  </p>
                  <button
                    className="button subtle"
                    type="button"
                    onClick={() =>
                      onOpenSource({
                        id: source.capture_id,
                        expectedHash: source.content_hash,
                        expectedManifestHash: source.manifest_hash,
                      })
                    }
                  >
                    View saved result {index + 1}
                  </button>
                </li>
              );
            })}
          </ol>
        </section>
      )}
    </section>
  );
}

function SavedSource({
  id,
  expectedHash,
  expectedManifestHash,
  onAction,
  onRemoved,
}: {
  id: string;
  expectedHash?: string;
  expectedManifestHash?: string;
  onAction: (
    action: () => Promise<ContextArchiveStatus>,
  ) => Promise<ContextArchiveStatus>;
  onRemoved: (id: string) => void;
}) {
  const [capture, setCapture] = useState<ContextCapture | null>(null);
  const [error, setError] = useState("");
  const [removing, setRemoving] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  useEffect(() => {
    let active = true;
    void api.loadContextCapture(id, expectedHash, expectedManifestHash).then(
      (next) => {
        if (active) setCapture(next);
      },
      (reason: Error) => {
        if (active) setError(reason.message);
      },
    );
    return () => {
      active = false;
    };
  }, [id, expectedHash, expectedManifestHash]);
  async function remove() {
    if (removing) return;
    setRemoving(true);
    setError("");
    try {
      await onAction(() => api.deleteContextCapture(id));
      onRemoved(id);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not remove the saved source.",
      );
    } finally {
      setRemoving(false);
    }
  }
  if (!capture && !error) return <p role="status">Loading saved source…</p>;
  const url = capture && sourceURL(capture.url);
  return (
    <div className="saved-source-view">
      {error && (
        <div className="error-banner" role="alert">
          {error}
        </div>
      )}
      {capture && (
        <>
          <h3>{capture.title}</h3>
          {url && (
            <a href={url} target="_blank" rel="noopener noreferrer">
              Open live source
            </a>
          )}
          <p className="reflection-note">
            Search excerpt · Retrieved {sourceDate(capture.retrieved_at)}
            <br />
            {capture.published_at
              ? `Published ${sourceDate(capture.published_at)}`
              : "Publication date unknown"}
            {capture.modified_at && (
              <>
                <br />
                Updated {sourceDate(capture.modified_at)}
              </>
            )}
          </p>
          <pre
            className="source-snapshot"
            tabIndex={0}
            aria-label="Saved source text"
          >
            {capture.content}
          </pre>
          {confirmRemove ? (
            <div className="source-remove-confirmation">
              <p>
                Remove this saved source? Earlier citations will show that it is
                unavailable.
              </p>
              <div className="form-actions">
                <button
                  className="button secondary"
                  disabled={removing}
                  onClick={() => setConfirmRemove(false)}
                >
                  Keep source
                </button>
                <button
                  className="button primary"
                  disabled={removing}
                  onClick={() => void remove()}
                >
                  {removing && <LoaderCircle size={15} className="spin" />}
                  Confirm removal
                </button>
              </div>
            </div>
          ) : (
            <button
              className="button subtle"
              onClick={() => setConfirmRemove(true)}
            >
              Remove saved source
            </button>
          )}
        </>
      )}
    </div>
  );
}

function LimitsForm({
  limits,
  onSave,
  busy,
}: {
  limits: Limits;
  onSave: (limits: Limits) => void;
  busy: boolean;
}) {
  const form = useRef<HTMLFormElement>(null);
  const savedLimits = useRef(limits);
  useEffect(() => {
    // Update saved fields without replacing drafts in the other limits form.
    for (const key of Object.keys(limits) as (keyof Limits)[]) {
      const input = form.current?.elements.namedItem(key) as HTMLInputElement;
      if (input?.value && Number(input.value) === savedLimits.current[key]) {
        input.value = String(limits[key]);
      }
    }
    savedLimits.current = limits;
  }, [limits]);
  return (
    <form
      ref={form}
      className="limits-form"
      onSubmit={(event) => {
        event.preventDefault();
        const data = new FormData(event.currentTarget);
        onSave({
          run_usd: Number(data.get("run_usd")),
          daily_usd: Number(data.get("daily_usd")),
          monthly_usd: Number(data.get("monthly_usd")),
          max_tokens: Number(data.get("max_tokens")),
        });
      }}
    >
      <div className="form-section-label">SPENDING LIMITS · USD</div>
      <div className="budget-inputs">
        {(
          [
            { key: "run_usd", label: "Per chat request", max: 10000 },
            { key: "daily_usd", label: "Per day", max: 100000 },
            { key: "monthly_usd", label: "Per month", max: 1000000 },
          ] as const
        ).map((field) => (
          <label key={field.key}>
            {field.label}
            <input
              required
              type="number"
              min="0"
              max={field.max}
              step="0.01"
              name={field.key}
              defaultValue={limits[field.key]}
            />
          </label>
        ))}
      </div>
      <label>
        Tokens per chat request
        <input
          required
          type="number"
          min="0"
          max="10000000"
          step="1"
          name="max_tokens"
          defaultValue={limits.max_tokens}
        />
      </label>
      <p className="form-note">
        Spending and token limits are checked before each chat request. Local
        inference has no provider API charge.
      </p>
      <div className="form-actions">
        <button className="button primary" type="submit" disabled={busy}>
          {busy ? (
            <LoaderCircle size={16} className="spin" />
          ) : (
            <Check size={16} />
          )}{" "}
          Save limits
        </button>
      </div>
    </form>
  );
}

function UsageDetails({ workspace }: { workspace: Workspace }) {
  const { usage, limits, web_search } = workspace;
  return (
    <div className="usage-details">
      <div className="usage-total">
        <span>Estimated spend today</span>
        <strong>
          {money(usage.today_usd)} <small>/ {money(limits.daily_usd)}</small>
        </strong>
      </div>
      <div className="usage-breakdown">
        {(
          [
            ["Input tokens", usage.input_tokens],
            ["Output tokens", usage.output_tokens],
            ["API requests", usage.calls],
          ] as const
        ).map(([label, value]) => (
          <div key={label}>
            <span>{label}</span>
            <strong>{value.toLocaleString()}</strong>
          </div>
        ))}
      </div>
      <div className="usage-month">
        <span>Estimated month to date</span>
        <strong>
          {money(usage.month_usd)} / {money(limits.monthly_usd)}
        </strong>
      </div>
      <div className="usage-month">
        <span>Web searches today</span>
        <strong>
          {web_search.searches_today} / {web_search.config.daily_limit}
        </strong>
      </div>
      <p className="form-note">
        Tokens come from provider responses. USD uses your configured prices;
        cache discounts and search fees are excluded. Reserved or uncertain
        spend: {money(usage.reserved_usd)}.
      </p>
    </div>
  );
}

function LocalRequestRecovery({
  entry,
  busy,
  onRecover,
}: {
  entry: LocalRequest;
  busy: boolean;
  onRecover: (id: string, originalURL: string) => void;
}) {
  const [originalURL, setOriginalURL] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const endpoint = entry.base_url ?? originalURL.trim();
  return (
    <form
      className="entry-form charge-form local-recovery-form"
      onSubmit={(event) => {
        event.preventDefault();
        if (busy || !confirmed || !endpoint) return;
        onRecover(entry.id, endpoint);
      }}
    >
      <div>
        <h3>Interrupted local request</h3>
        <p>
          Model: {entry.model || "Unknown model"}. Requested{" "}
          <time dateTime={entry.at}>{new Date(entry.at).toLocaleString()}</time>
          .
        </p>
        {entry.base_url ? (
          <p>
            Original Ollama server: <strong>{entry.base_url}</strong>
          </p>
        ) : (
          <p>
            This older request has no saved server URL. Enter its original
            Ollama server URL; Maestro cannot determine it from your current
            connection settings.
          </p>
        )}
        <p>
          The request may still be running and holds the generation slot. Stop
          this same original Ollama server completely and restart it before
          confirming below. Keep other clients idle during recovery. Restarting
          interrupts other work on this server. Maestro cannot verify that
          restart automatically. After your confirmation, it checks that the
          server has no loaded models before releasing the slot.
        </p>
      </div>
      {!entry.base_url && (
        <label>
          Original Ollama server URL
          <input
            type="url"
            required
            maxLength={2048}
            value={originalURL}
            disabled={busy}
            onChange={(event) => {
              setOriginalURL(event.target.value);
              setConfirmed(false);
            }}
          />
        </label>
      )}
      <label className="reflection-check">
        <input
          type="checkbox"
          checked={confirmed}
          disabled={busy || !endpoint}
          onChange={(event) => setConfirmed(event.target.checked)}
        />
        I stopped this original Ollama server completely and restarted it.
      </label>
      <button
        className="button secondary"
        type="submit"
        disabled={busy || !confirmed || !endpoint}
      >
        {busy && <LoaderCircle size={16} className="spin" />}
        Verify and release slot
      </button>
    </form>
  );
}

function TaskRow({
  task,
  busy,
  onToggle,
  onDelete,
  onAssign,
}: {
  task: Task;
  busy: boolean;
  onToggle: () => void;
  onDelete: () => void;
  onAssign: (assignee: NonNullable<Task["suggested_assignee"]>) => void;
}) {
  return (
    <div className={`task-row ${task.done ? "task-done" : ""}`}>
      <button
        className={`task-checkbox ${task.done ? "checked" : ""}`}
        disabled={busy}
        onClick={onToggle}
        aria-label={`${task.done ? "Reopen" : "Complete"} ${task.title}`}
        aria-pressed={task.done}
      >
        {task.done && <Check size={14} />}
      </button>
      <div className="task-copy">
        <span className="task-title">{task.title}</span>
        {task.details && <p>{task.details}</p>}
        {task.priority === "high" && (
          <span className="priority-tag">High priority</span>
        )}
        <div className="task-metadata">
          <span>
            Initiated by {task.initiated_by === "maestro" ? "Maestro" : "You"}
          </span>
          <label>
            Suggested for
            <select
              aria-label={`Suggested assignee for ${task.title}`}
              value={task.suggested_assignee ?? "user"}
              disabled={busy}
              onChange={(event) =>
                onAssign(
                  event.target.value as NonNullable<Task["suggested_assignee"]>,
                )
              }
            >
              <option value="user">You</option>
              <option value="maestro">Maestro</option>
            </select>
          </label>
        </div>
      </div>
      <button
        className="icon-button"
        disabled={busy}
        onClick={onDelete}
        aria-label={`Delete ${task.title}`}
      >
        <Trash2 size={16} />
      </button>
    </div>
  );
}

export default function App() {
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [page, setPage] = useState<Page>("workspace");
  const [panel, setPanel] = useState<Panel>(null);
  const [busy, setBusy] = useState("");
  const [toast, setToast] = useState("");
  const [error, setError] = useState("");
  const [chat, setChat] = useState("");
  const [replyId, setReplyId] = useState("");
  const [feedbackSaving, setFeedbackSaving] = useState(false);
  const [model, setModel] = useState("");
  const [contextMode, setContextMode] = useState<ContextMode>("prefer_saved");
  const [sourceView, setSourceView] = useState<SavedSourceSelection | null>(
    null,
  );
  const [removedCaptureIds, setRemovedCaptureIds] = useState<Set<string>>(
    () => new Set(),
  );
  const modelPicker = useRef<HTMLDivElement>(null);
  const [mobileNav, setMobileNav] = useState(false);
  const navigationDrawer = useRef<HTMLElement>(null);
  const navigationMenu = useRef<HTMLButtonElement>(null);
  const chatEnd = useRef<HTMLDivElement>(null);
  const workspaceRevision = useRef(0);
  const workspaceRefresh = useRef(0);
  const archiveQueue = useRef(Promise.resolve());
  const reflectionPolling = useRef(false);
  const [reflectionActions, setReflectionActions] = useState(0);
  const reflectionVisible = page === "settings" || page === "memory";
  const reflectionEnabled = workspace?.work?.config.enabled;
  const reflectionPending =
    !!workspace?.work && (workspace.work.queued > 0 || workspace.work.running);

  useEffect(() => {
    let active = true;
    void api.loadWorkspace().then(
      (next) => {
        if (active) applyWorkspace(next);
      },
      (reason: Error) => {
        if (active) setError(reason.message);
      },
    );
    return () => {
      active = false;
    };
  }, []);
  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(""), 3500);
    return () => window.clearTimeout(timer);
  }, [toast]);
  useEffect(() => {
    chatEnd.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [workspace?.messages.length]);
  useEffect(() => {
    const original = workspace?.web_search;
    if (
      page !== "workspace" ||
      workspace?.provider.config.protocol !== "ollama" ||
      busy ||
      feedbackSaving ||
      reflectionActions ||
      !original?.config.enabled ||
      !original.credentials_present ||
      !original.tested_at ||
      original.remaining_today <= 0 ||
      original.ready !== false
    )
      return;
    let active = true;
    let polling = false;
    let timer: number | undefined;
    const cooldown = original.paused_until
      ? Date.parse(original.paused_until) - Date.now()
      : 0;
    const delay = Number.isFinite(cooldown)
      ? Math.min(60000, Math.max(5000, cooldown))
      : 5000;
    let refreshAt = Date.now() + delay;
    function schedule() {
      window.clearTimeout(timer);
      if (active && !document.hidden)
        timer = window.setTimeout(
          () => void poll(),
          Math.max(0, refreshAt - Date.now()),
        );
    }
    async function poll() {
      if (!active || document.hidden || polling) return;
      polling = true;
      refreshAt = Date.now() + delay;
      const revision = workspaceRevision.current;
      try {
        const status = await api.loadWebSearch();
        if (active && !document.hidden)
          setWorkspace((current) =>
            current &&
            workspaceRevision.current === revision &&
            current.web_search === original
              ? { ...current, web_search: status }
              : current,
          );
      } catch {
        // Keep the current readiness and retry without replacing chat errors.
      } finally {
        polling = false;
        schedule();
      }
    }
    schedule();
    document.addEventListener("visibilitychange", schedule);
    return () => {
      active = false;
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", schedule);
    };
  }, [
    workspace?.web_search,
    workspace?.provider.config.protocol,
    page,
    busy,
    feedbackSaving,
    reflectionActions,
  ]);
  useEffect(() => {
    if (
      busy ||
      feedbackSaving ||
      reflectionActions ||
      !(reflectionEnabled || reflectionPending)
    )
      return;
    let active = true;
    async function poll() {
      if (!active || document.hidden || reflectionPolling.current) return;
      reflectionPolling.current = true;
      const revision = workspaceRevision.current;
      try {
        const work = await api.loadReflection();
        if (active && !document.hidden) applyReflection(work, revision);
      } catch (reason) {
        if (
          active &&
          !document.hidden &&
          revision === workspaceRevision.current
        )
          setError(
            reason instanceof Error
              ? reason.message
              : "Could not refresh reflection.",
          );
      } finally {
        reflectionPolling.current = false;
      }
    }
    const timer = window.setInterval(() => void poll(), 3000);
    document.addEventListener("visibilitychange", poll);
    return () => {
      active = false;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", poll);
    };
  }, [
    busy,
    feedbackSaving,
    reflectionActions,
    reflectionEnabled,
    reflectionPending,
  ]);
  useEffect(() => {
    if (!mobileNav) return;
    const mobile = window.matchMedia("(max-width: 760px)");
    if (!mobile.matches) {
      setMobileNav(false);
      return;
    }
    const drawer = navigationDrawer.current;
    if (!drawer) return;
    const buttons = () =>
      Array.from(
        drawer.querySelectorAll<HTMLButtonElement>("button:not(:disabled)"),
      );
    buttons()[0]?.focus();
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    function trapFocus(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        setMobileNav(false);
      } else if (event.key === "Tab") {
        const available = buttons();
        const first = available[0];
        const last = available[available.length - 1];
        if (
          !drawer?.contains(document.activeElement) ||
          (event.shiftKey
            ? document.activeElement === first
            : document.activeElement === last)
        ) {
          event.preventDefault();
          (event.shiftKey ? last : first)?.focus();
        }
      }
    }
    function resize() {
      if (!mobile.matches) setMobileNav(false);
    }
    document.addEventListener("keydown", trapFocus);
    mobile.addEventListener("change", resize);
    return () => {
      document.removeEventListener("keydown", trapFocus);
      mobile.removeEventListener("change", resize);
      document.body.style.overflow = previousOverflow;
      if (mobile.matches) navigationMenu.current?.focus();
    };
  }, [mobileNav]);

  function setChatUrl(id: string | null) {
    const url = new URL(window.location.href);
    if (id) url.searchParams.set("chat", id);
    else url.searchParams.delete("chat");
    window.history.replaceState(null, "", url);
  }
  function applyWorkspace(next: Workspace) {
    workspaceRevision.current += 1;
    setChatUrl(next.active_chat_id);
    setWorkspace(next);
  }
  function refreshWorkspace(): Promise<void> {
    const request = ++workspaceRefresh.current;
    const revision = ++workspaceRevision.current;
    return api
      .loadWorkspace()
      .then((next) => {
        if (workspaceRevision.current === revision) applyWorkspace(next);
        else if (workspaceRefresh.current === request)
          return refreshWorkspace();
      })
      .catch((reason: Error) => {
        if (workspaceRevision.current === revision) setError(reason.message);
        else if (workspaceRefresh.current === request)
          return refreshWorkspace();
      });
  }
  function archiveAction(action: () => Promise<ContextArchiveStatus>) {
    // Serialize archive requests so reads cannot overtake a pending mutation.
    const result = archiveQueue.current.then(async () => {
      const status = await action();
      workspaceRevision.current += 1;
      setWorkspace((current) =>
        current ? { ...current, context_archive: status } : current,
      );
      return status;
    });
    archiveQueue.current = result.then(
      () => {},
      () => {},
    );
    return result;
  }
  function applyReflection(work: WorkStatus, revision: number) {
    setWorkspace((current) =>
      current && workspaceRevision.current === revision
        ? {
            ...current,
            work,
            ...(work.memories ? { memories: work.memories } : {}),
            ...(work.tasks ? { tasks: work.tasks } : {}),
            ...(work.usage ? { usage: work.usage } : {}),
          }
        : current,
    );
  }
  async function reflectionAction<T>(action: () => Promise<T>) {
    workspaceRevision.current += 1;
    setReflectionActions((count) => count + 1);
    try {
      return await action();
    } catch (reason) {
      refreshWorkspace();
      throw reason;
    } finally {
      setReflectionActions((count) => count - 1);
    }
  }
  function saveWorkConfig(config: WorkConfig) {
    return reflectionAction(async () => {
      const revision = workspaceRevision.current;
      const work = await api.saveWorkConfig(config);
      if (workspaceRevision.current === revision)
        applyReflection(work, revision);
      else refreshWorkspace();
      return work;
    });
  }
  async function perform(
    key: string,
    action: () => Promise<Workspace>,
    success?: string,
    selectReturnedChat = false,
  ) {
    let revision = ++workspaceRevision.current;
    let completed = false;
    setBusy(key);
    setError("");
    try {
      let next = await action();
      completed = true;
      const selected = selectReturnedChat
        ? next.active_chat_id
        : new URLSearchParams(window.location.search).get("chat");
      if (
        workspaceRevision.current !== revision ||
        (selected && next.active_chat_id !== selected)
      ) {
        if (selectReturnedChat) setChatUrl(selected);
        revision = ++workspaceRevision.current;
        next = await api.loadWorkspace(selected);
      }
      if (workspaceRevision.current === revision) applyWorkspace(next);
      if (success) setToast(success);
      return true;
    } catch (reason) {
      refreshWorkspace();
      setError(reason instanceof Error ? reason.message : "Please try again.");
      return completed;
    } finally {
      setBusy("");
    }
  }
  function navigate(next: Page) {
    if (next !== "workspace") setReplyId("");
    setPage(next);
    setMobileNav(false);
  }
  async function switchChat(
    action: () => Promise<Workspace>,
    preserveDraft = false,
  ) {
    if (await perform("select-chat", action, undefined, true)) {
      setReplyId("");
      if (!preserveDraft) setChat("");
      navigate("workspace");
    }
  }
  async function createTask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    if (
      await perform(
        "task",
        () =>
          api.addTask(
            String(data.get("title")),
            String(data.get("details")),
            String(data.get("priority")),
            String(data.get("suggested_assignee")) as NonNullable<
              Task["suggested_assignee"]
            >,
          ),
        "Task saved locally.",
      )
    )
      setPanel(null);
  }
  async function submitChat(event: { preventDefault(): void }) {
    event.preventDefault();
    if (!chat.trim() || busy || !ready) return;
    const submitted = chat;
    if (
      await perform("chat", async () => {
        let id = workspace?.active_chat_id;
        if (!id) {
          const next = await api.createChat();
          applyWorkspace(next);
          id = next.active_chat_id;
        }
        if (!id)
          throw new Error("Could not create a conversation. Please try again.");
        const next = await api.sendMessage(
          submitted,
          id,
          requestedModel,
          local ? contextMode : "prefer_saved",
        );
        const reply = next.messages.at(-1);
        if (reply?.role === "assistant") setReplyId(reply.id);
        return next;
      })
    )
      setChat((draft) => (draft === submitted ? "" : draft));
  }
  async function saveLimits(limits: Limits) {
    if (await perform("limits", () => api.saveLimits(limits), "Limits saved."))
      setPanel(null);
  }
  async function saveFeedback(
    chatId: string,
    messageId: string,
    rating: MessageFeedback["rating"] | null,
    comment: string,
  ) {
    const revision = workspaceRevision.current;
    setFeedbackSaving(true);
    try {
      const result = await api.saveMessageFeedback(
        chatId,
        messageId,
        rating,
        comment,
      );
      if (revision !== workspaceRevision.current) {
        refreshWorkspace();
        return false;
      }
      workspaceRevision.current += 1;
      setWorkspace((current) =>
        current?.active_chat_id === chatId
          ? {
              ...current,
              work: result.work ?? current.work,
              memories: result.memories ?? current.memories,
              tasks: result.work?.tasks ?? current.tasks,
              messages: current.messages.map((message) =>
                message.id === result.message_id
                  ? { ...message, feedback: result.feedback }
                  : message,
              ),
            }
          : current,
      );
      return true;
    } catch (reason) {
      refreshWorkspace();
      throw reason;
    } finally {
      setFeedbackSaving(false);
    }
  }

  if (!workspace)
    return (
      <div className="loading-screen">
        <h1>Maestro</h1>
        {error ? (
          <>
            <p role="alert">{error}</p>
            <button
              className="button primary"
              onClick={() => window.location.reload()}
            >
              Try again
            </button>
          </>
        ) : (
          <p>
            <LoaderCircle size={16} className="spin" /> Opening workspace…
          </p>
        )}
      </div>
    );
  const openTasks = workspace.tasks.filter((task) => !task.done);
  const activeChat = workspace.chats.find(
    (thread) => thread.id === workspace.active_chat_id,
  );
  const local = workspace.provider.config.protocol === "ollama";
  const memoryDerived = workspace.messages.some(
    (message) => !!message.memory_ids?.length,
  );
  const requestedModel = local ? model : "";
  const archivePolicy =
    workspace.context_archive?.config.capture_policy ?? "approved_sources";
  const sourceViewEligibility = JSON.stringify([
    workspace.context_archive?.configured ?? false,
    workspace.context_archive?.config.enabled ?? false,
    archivePolicy,
    archivePolicy === "approved_sources"
      ? [...(workspace.context_archive?.config.public_sources ?? [])].sort()
      : [],
  ]);
  const defaultModel =
    local && workspace.provider.config.chat_routing === "orchestrator"
      ? workspace.provider.config.orchestrator_model ||
        workspace.provider.config.model
      : workspace.provider.config.model;
  const selectedModel = requestedModel || defaultModel;
  const unavailableModel =
    local &&
    !!selectedModel &&
    !workspace.provider.models.includes(selectedModel);
  const ready =
    (workspace.provider.credentials_required === false ||
      workspace.provider.credentials_present) &&
    !unavailableModel &&
    !!selectedModel &&
    workspace.provider.config.pricing_verified;

  return (
    <div className="app-shell">
      <aside
        ref={navigationDrawer}
        id="navigation-drawer"
        className={`sidebar ${mobileNav ? "sidebar-open" : ""}`}
        role={mobileNav ? "dialog" : undefined}
        aria-modal={mobileNav || undefined}
        aria-label={mobileNav ? "Navigation" : undefined}
      >
        <div className="brand">
          <strong>maestro.</strong>
          <button
            className="icon-button mobile-close"
            onClick={() => setMobileNav(false)}
            aria-label="Close navigation"
          >
            <X size={19} />
          </button>
        </div>
        <nav aria-label="Main navigation">
          {navigation.map((item) => (
            <button
              key={item.id}
              className={`nav-item ${page === item.id ? "active" : ""}`}
              onClick={() => navigate(item.id)}
              aria-current={page === item.id ? "page" : undefined}
            >
              <item.icon size={18} />
              <span>{item.name}</span>
              {item.id === "tasks" && <small>{openTasks.length}</small>}
            </button>
          ))}
        </nav>
        <section className="chat-sidebar" aria-label="Recent chats">
          <button
            className="button secondary new-chat"
            disabled={!!busy}
            onClick={() => void switchChat(api.createChat)}
          >
            <Plus size={16} /> New chat
          </button>
          <div className="chat-list">
            {workspace.chats.map((thread) => (
              <button
                key={thread.id}
                className={`chat-link ${thread.id === workspace.active_chat_id ? "active" : ""}`}
                aria-label={`Open chat: ${thread.title}`}
                aria-pressed={thread.id === workspace.active_chat_id}
                title={thread.title}
                disabled={!!busy}
                onClick={() =>
                  void switchChat(() => api.loadWorkspace(thread.id))
                }
              >
                {thread.title}
              </button>
            ))}
          </div>
        </section>
        <p className="sidebar-note">
          Tasks, conversations and settings are stored on this computer.
        </p>
      </aside>
      {mobileNav && (
        <button
          className="nav-backdrop"
          aria-label="Close navigation"
          tabIndex={-1}
          onClick={() => setMobileNav(false)}
        />
      )}
      <div className="main-shell" inert={mobileNav}>
        <header className="topbar">
          <button
            className="icon-button mobile-menu"
            ref={navigationMenu}
            aria-label="Open navigation"
            aria-expanded={mobileNav}
            aria-controls="navigation-drawer"
            onClick={() => setMobileNav(true)}
          >
            <Menu size={19} />
          </button>
          <span className="page-label">
            {navigation.find((item) => item.id === page)?.name}
          </span>
          <button
            className="usage-strip"
            onClick={() => setPanel("usage")}
            aria-label="View usage and manage budgets"
          >
            <Coins size={15} />
            <strong>{money(workspace.usage.today_usd)}</strong>
            <small>today · est.</small>
            <span className="usage-tokens">
              {tokens(
                workspace.usage.input_tokens + workspace.usage.output_tokens,
              )}{" "}
              tokens
            </span>
            <SlidersHorizontal size={15} />
          </button>
        </header>
        {error && (
          <div className="error-banner" role="alert">
            <span>{error}</span>
            {!!workspace.usage.local_requests?.length && (
              <button
                className="button secondary"
                onClick={() => setPanel("usage")}
              >
                Open local recovery
              </button>
            )}
            <button
              className="icon-button"
              onClick={() => setError("")}
              aria-label="Dismiss error"
            >
              <X size={16} />
            </button>
          </div>
        )}

        {page === "workspace" && (
          <main className="conversation-pane">
            <div className="page-heading">
              <div>
                <h1>{activeChat?.title ?? "Chat"}</h1>
                <p>
                  {selectedModel ||
                    "Connect a model in Settings to start a conversation."}
                </p>
              </div>
              {activeChat && (
                <div className="chat-actions">
                  <button
                    className="icon-button"
                    aria-label="Rename chat"
                    disabled={!!busy}
                    onClick={() => setPanel("rename-chat")}
                  >
                    <Pencil size={17} />
                  </button>
                  <button
                    className="icon-button"
                    aria-label="Delete chat"
                    disabled={!!busy}
                    onClick={() => setPanel("delete-chat")}
                  >
                    <Trash2 size={17} />
                  </button>
                </div>
              )}
            </div>
            {!ready && (
              <button
                className="button primary connection-prompt"
                onClick={() => navigate("settings")}
              >
                Set up model connection
              </button>
            )}
            {workspace.messages.length === 0 ? (
              <div className="empty-conversation">
                <MessageCircle size={30} />
                <h2>Start a conversation</h2>
                <p>
                  Messages go to your configured model. Tasks are managed
                  separately.
                </p>
              </div>
            ) : (
              <div className="chat-history" aria-live="polite">
                {workspace.messages.map((message) => (
                  <div
                    key={message.id}
                    className={`chat-message ${message.role}`}
                  >
                    <span className="message-author">
                      {message.role === "assistant"
                        ? `${message.kind === "orchestrator" ? "Orchestrator" : "Maestro"} · ${message.model ?? workspace.provider.config.model}`
                        : "You"}
                    </span>
                    {message.role === "assistant" ? (
                      <Reply
                        text={message.text}
                        animate={message.id === replyId}
                      />
                    ) : (
                      <p>{message.text}</p>
                    )}
                    {message.cost !== undefined && (
                      <small className="message-usage">
                        {message.input_tokens} input · {message.output_tokens}{" "}
                        output tokens · {money(message.cost)} estimated
                      </small>
                    )}
                    {!!message.memory_ids?.length && (
                      <small className="message-usage">
                        Used {message.memory_ids.length} saved{" "}
                        {message.memory_ids.length === 1
                          ? "memory"
                          : "memories"}
                      </small>
                    )}
                    {(message.web_search ||
                      !!message.source_context?.citations?.length) && (
                      <div className="message-sources">
                        <p>
                          {message.web_search
                            ? `${message.web_search.from_cache ? "Saved sources for:" : "Search query:"} ${message.web_search.query}`
                            : "Sources from earlier answers"}
                        </p>
                        {message.web_search?.from_cache && (
                          <p>Reused saved evidence; no new web search.</p>
                        )}
                        {message.web_search?.stale && (
                          <p>Older saved evidence</p>
                        )}
                        {message.web_search?.search_limited && (
                          <p role="status">
                            Saved lookup reached its work limit; narrow keywords
                            or saved-source filters for a more complete lookup.
                          </p>
                        )}
                        {message.web_search?.archive_warning && (
                          <p role="status">
                            {message.web_search.archive_warning}
                          </p>
                        )}
                        <ol>
                          {[
                            ...(message.web_search?.sources ?? []),
                            ...(message.source_context?.citations ?? []),
                          ].map((source, index) => {
                            const url = sourceURL(source.url);
                            const lookup =
                              index < (message.web_search?.sources.length ?? 0);
                            return (
                              <li key={index}>
                                {url ? (
                                  <a
                                    href={url}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                  >
                                    {source.title || source.url}
                                  </a>
                                ) : (
                                  source.title
                                )}
                                <div className="source-details">
                                  <span>
                                    Search excerpt ·{" "}
                                    {(source.stale ??
                                      (lookup && message.web_search?.stale)) &&
                                      "Older saved evidence · "}
                                    Retrieved{" "}
                                    {sourceDate(
                                      source.retrieved_at ??
                                        (lookup
                                          ? message.web_search?.at
                                          : null) ??
                                        null,
                                    )}
                                  </span>
                                  <span>
                                    {source.published_at
                                      ? `Published ${sourceDate(source.published_at)}`
                                      : "Publication date unknown"}
                                  </span>
                                  {source.modified_at && (
                                    <span>
                                      Updated {sourceDate(source.modified_at)}
                                    </span>
                                  )}
                                  {source.archive_status === "saved" &&
                                  source.capture_id ? (
                                    <button
                                      className="button subtle"
                                      type="button"
                                      onClick={() =>
                                        setSourceView({
                                          id: source.capture_id!,
                                          expectedHash: source.content_hash,
                                          expectedManifestHash:
                                            source.manifest_hash,
                                        })
                                      }
                                    >
                                      View saved source {index + 1}
                                    </button>
                                  ) : (
                                    <span>
                                      {source.archive_status === "not_saved"
                                        ? "Source was not saved"
                                        : "Not archived"}
                                    </span>
                                  )}
                                </div>
                              </li>
                            );
                          })}
                        </ol>
                      </div>
                    )}
                    {message.role === "assistant" &&
                      !message.demo &&
                      workspace.active_chat_id && (
                        <AnswerFeedback
                          key={`${workspace.active_chat_id}:${message.id}`}
                          feedback={message.feedback}
                          disabled={!!busy || feedbackSaving}
                          onSave={(rating, comment) =>
                            saveFeedback(
                              workspace.active_chat_id!,
                              message.id,
                              rating,
                              comment,
                            )
                          }
                        />
                      )}
                  </div>
                ))}
                <div ref={chatEnd} />
              </div>
            )}
            <div className="composer-area">
              <form className="composer" onSubmit={submitChat}>
                <textarea
                  aria-label="Message Maestro"
                  placeholder="Write a message…"
                  maxLength={32000}
                  rows={3}
                  value={chat}
                  onChange={(event) => setChat(event.target.value)}
                  onKeyDown={(event) => {
                    if (
                      event.key === "Enter" &&
                      !event.shiftKey &&
                      !event.nativeEvent.isComposing
                    )
                      void submitChat(event);
                  }}
                />
                <div className="composer-bottom">
                  <div className="composer-controls">
                    {local && (
                      <SearchOptions
                        status={workspace.web_search}
                        archive={workspace.context_archive}
                        mode={contextMode}
                        onMode={setContextMode}
                        memoryDerived={memoryDerived}
                        busy={!!busy}
                        onSettings={() => navigate("settings")}
                        onManageSources={() => {
                          navigate("settings");
                          window.requestAnimationFrame(() =>
                            document
                              .getElementById("saved-web-sources")
                              ?.scrollIntoView({ block: "start" }),
                          );
                        }}
                        onNewChat={() => void switchChat(api.createChat, true)}
                      />
                    )}
                    {local ? (
                      <div className="model-control">
                        <button
                          className="model-trigger"
                          type="button"
                          popoverTarget="chat-model-picker"
                          disabled={!!busy}
                          aria-label={`Choose model: ${selectedModel || "none"}`}
                        >
                          {selectedModel || "Choose a model"}
                          <ChevronDown size={14} />
                        </button>
                        <div
                          id="chat-model-picker"
                          className="model-picker"
                          popover="auto"
                          ref={modelPicker}
                        >
                          <p>Chat model</p>
                          {[
                            ...new Set([
                              defaultModel,
                              ...workspace.provider.models,
                            ]),
                          ]
                            .filter(Boolean)
                            .map((choice) => (
                              <button
                                key={choice}
                                type="button"
                                disabled={
                                  !!busy ||
                                  !workspace.provider.models.includes(choice)
                                }
                                aria-pressed={selectedModel === choice}
                                onClick={() => {
                                  setModel(
                                    choice === defaultModel ? "" : choice,
                                  );
                                  modelPicker.current?.hidePopover();
                                }}
                              >
                                <span>
                                  {choice}
                                  {choice === defaultModel && (
                                    <small>Default</small>
                                  )}
                                </span>
                                {selectedModel === choice && (
                                  <Check size={16} />
                                )}
                              </button>
                            ))}
                        </div>
                      </div>
                    ) : (
                      <small>{selectedModel || "No model selected"}</small>
                    )}
                  </div>
                  <button
                    className="send-button"
                    type="submit"
                    aria-label="Send message"
                    disabled={!chat.trim() || !!busy || !ready}
                  >
                    {busy === "chat" ? (
                      <LoaderCircle size={18} className="spin" />
                    ) : (
                      <ArrowRight size={19} />
                    )}
                  </button>
                </div>
              </form>
              {unavailableModel && (
                <p className="composer-note" role="status">
                  The chosen model is no longer installed. Please choose another
                  model before sending.
                </p>
              )}
              <p className="composer-note">
                Chat is saved locally and sent to your configured model provider
                when you send a message.
              </p>
            </div>
          </main>
        )}

        {page === "tasks" && (
          <main className="page-content">
            <div className="page-heading">
              <div>
                <h1>Tasks</h1>
                <p>
                  {openTasks.length} open ·{" "}
                  {workspace.tasks.length - openTasks.length} completed
                </p>
              </div>
              <button
                className="button primary"
                onClick={() => setPanel("task")}
              >
                <Plus size={16} /> New task
              </button>
            </div>
            <p className="page-note">
              Create and track tasks here. Maestro can suggest tasks during
              background reflection; tasks do not run automatically.
            </p>
            <div className="card task-board">
              {workspace.tasks.map((task) => (
                <TaskRow
                  key={task.id}
                  task={task}
                  busy={!!busy}
                  onAssign={(assignee) =>
                    void perform("assign-task", () =>
                      api.assignTask(task.id, assignee),
                    )
                  }
                  onToggle={() =>
                    void perform("toggle", () =>
                      api.toggleTask(task.id, !task.done),
                    )
                  }
                  onDelete={() =>
                    void perform(
                      "delete",
                      () => api.deleteTask(task.id),
                      "Task removed.",
                    )
                  }
                />
              ))}
              {workspace.tasks.length === 0 && (
                <div className="empty-state">
                  <CheckSquare size={30} />
                  <h2>No tasks yet</h2>
                  <p>Add a task to keep track of your work.</p>
                </div>
              )}
            </div>
          </main>
        )}

        <main className="page-content" hidden={page !== "settings"}>
          <div className="page-heading">
            <div>
              <h1>Settings</h1>
              <p>Configure your model, optional search and usage limits.</p>
            </div>
            <button
              className="button secondary"
              onClick={() => navigate("memory")}
            >
              <Network size={16} />
              Open memory
            </button>
          </div>
          <div className="settings-layout">
            <div className="card settings-card">
              <h2>Spending limits</h2>
              <LimitsForm
                limits={workspace.limits}
                onSave={(limits) => void saveLimits(limits)}
                busy={!!busy}
              />
              <button
                className="button secondary"
                onClick={() => setPanel("usage")}
              >
                Open usage
              </button>
            </div>
            <div className="settings-side">
              {workspace.work && (
                <WorkSetup
                  initialStatus={workspace.work}
                  provider={workspace.provider}
                  onSave={saveWorkConfig}
                />
              )}
              <ProviderSetup
                initialStatus={workspace.provider}
                onChange={refreshWorkspace}
              />
              <OllamaSearchSetup
                initialStatus={workspace.web_search}
                provider={workspace.provider}
                onChange={refreshWorkspace}
              />
              {workspace.context_archive && (
                <ContextArchiveSetup
                  initialStatus={workspace.context_archive}
                  onAction={archiveAction}
                  active={page === "settings"}
                  onOpenSource={setSourceView}
                  removedCaptureIds={removedCaptureIds}
                />
              )}
            </div>
          </div>
        </main>
        {reflectionVisible && (
          <main className="page-content" hidden={page !== "memory"}>
            <div className="page-heading">
              <div>
                <h1>Memory</h1>
                <p>
                  Explore private memories, their sources and reflection
                  history.
                </p>
              </div>
              <button
                className="button secondary"
                onClick={() => navigate("settings")}
              >
                <Settings2 size={16} />
                Memory settings
              </button>
            </div>
            <div className="settings-side">
              <MemorySetup
                memories={workspace.memories ?? []}
                candidates={workspace.work?.candidates ?? []}
                automatic={workspace.work?.config.auto_curate ?? false}
                chats={workspace.chats}
                chatId={workspace.active_chat_id}
                onChange={refreshWorkspace}
                onAction={reflectionAction}
              />
              {workspace.work && (
                <ReflectionJournal
                  status={workspace.work}
                  chats={workspace.chats}
                />
              )}
            </div>
          </main>
        )}
      </div>

      {sourceView && (
        <Modal title="Saved source" onClose={() => setSourceView(null)}>
          {removedCaptureIds.has(sourceView.id) ? (
            <p role="status">
              Saved source removed. Earlier answers keep their text; this saved
              source is now unavailable.
            </p>
          ) : (
            <SavedSource
              key={`${sourceView.id}:${sourceView.expectedHash ?? ""}:${sourceView.expectedManifestHash ?? ""}:${sourceViewEligibility}`}
              id={sourceView.id}
              expectedHash={sourceView.expectedHash}
              expectedManifestHash={sourceView.expectedManifestHash}
              onAction={archiveAction}
              onRemoved={(id) =>
                setRemovedCaptureIds((current) => new Set(current).add(id))
              }
            />
          )}
        </Modal>
      )}

      {panel === "task" && (
        <Modal title="New task" onClose={() => setPanel(null)}>
          <form
            className="entry-form"
            onSubmit={(event) => void createTask(event)}
          >
            <label>
              What would you like to do?
              <input autoFocus required maxLength={200} name="title" />
            </label>
            <label>
              Context & completion criteria
              <textarea rows={4} maxLength={16000} name="details" />
            </label>
            <label>
              Priority
              <select name="priority" defaultValue="normal">
                <option value="normal">Normal</option>
                <option value="high">High</option>
              </select>
            </label>
            <label>
              Suggested assignee
              <select name="suggested_assignee" defaultValue="user">
                <option value="user">You</option>
                <option value="maestro">Maestro</option>
              </select>
            </label>
            <p className="form-note">
              <ShieldCheck size={15} /> Saving a task makes no model calls.
            </p>
            <div className="form-actions">
              <button
                type="button"
                className="button secondary"
                onClick={() => setPanel(null)}
              >
                Cancel
              </button>
              <button
                type="submit"
                className="button primary"
                disabled={!!busy}
              >
                <Plus size={16} /> Save task
              </button>
            </div>
          </form>
        </Modal>
      )}
      {panel === "usage" && (
        <Modal title="Usage & limits" onClose={() => setPanel(null)}>
          <UsageDetails workspace={workspace} />
          {!!workspace.usage.local_requests?.length && error && (
            <p className="error-banner" role="alert">
              {error}
            </p>
          )}
          {(workspace.usage.local_requests ?? []).map((entry) => (
            <LocalRequestRecovery
              key={JSON.stringify([
                entry.id,
                entry.at,
                entry.model,
                entry.base_url,
              ])}
              entry={entry}
              busy={!!busy}
              onRecover={(id, originalURL) =>
                void perform(
                  "recover-local",
                  () => api.recoverLocalRequest(id, originalURL),
                  "Local request released.",
                )
              }
            />
          ))}
          <LimitsForm
            limits={workspace.limits}
            onSave={(limits) => void saveLimits(limits)}
            busy={!!busy}
          />
          {workspace.usage.uncertain.map((entry) => (
            <form
              className="entry-form charge-form"
              key={entry.id}
              onSubmit={(event) => {
                event.preventDefault();
                const data = new FormData(event.currentTarget);
                void perform("reconcile", () =>
                  api.reconcileProviderCharge(
                    entry.id,
                    Number(data.get("cost")),
                  ),
                );
              }}
            >
              <p>
                Unknown charge from {new Date(entry.at).toLocaleString()}:{" "}
                {money(entry.reserved_usd)} reserved. Confirm actual billed USD
                from your provider before retrying.
              </p>
              <label>
                Verified billed USD
                <input
                  name="cost"
                  type="number"
                  required
                  min="0"
                  max="100000"
                  step="any"
                />
              </label>
              <button className="button secondary" disabled={!!busy}>
                Reconcile charge
              </button>
            </form>
          ))}
        </Modal>
      )}
      {panel === "rename-chat" && activeChat && (
        <Modal title="Rename chat" onClose={() => setPanel(null)}>
          <form
            className="entry-form"
            onSubmit={async (event) => {
              event.preventDefault();
              const data = new FormData(event.currentTarget);
              if (
                await perform(
                  "rename-chat",
                  () =>
                    api.renameChat(activeChat.id, String(data.get("title"))),
                  undefined,
                  true,
                )
              )
                setPanel(null);
            }}
          >
            <label>
              Chat title
              <input
                autoFocus
                required
                maxLength={120}
                name="title"
                defaultValue={activeChat.title}
                pattern=".*\S.*"
              />
            </label>
            <div className="form-actions">
              <button className="button primary" disabled={!!busy}>
                Save title
              </button>
            </div>
          </form>
        </Modal>
      )}
      {panel === "delete-chat" && activeChat && (
        <Modal title="Delete chat" onClose={() => setPanel(null)}>
          <p className="delete-chat-note">
            Delete “{activeChat.title}” and its messages? Usage totals are
            retained.
          </p>
          <div className="form-actions">
            <button className="button secondary" onClick={() => setPanel(null)}>
              Cancel
            </button>
            <button
              className="button primary"
              disabled={!!busy}
              onClick={async () => {
                if (
                  await perform(
                    "delete-chat",
                    () => api.deleteChat(activeChat.id),
                    undefined,
                    true,
                  )
                ) {
                  setPanel(null);
                  setChat("");
                }
              }}
            >
              Delete chat
            </button>
          </div>
        </Modal>
      )}
      {toast && (
        <div className="toast" role="status">
          <Check size={16} />
          {toast}
        </div>
      )}
    </div>
  );
}
