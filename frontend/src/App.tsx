import { useEffect, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import Reply from "./Reply";
import MemorySetup from "./MemorySetup";
import WorkSetup from "./WorkSetup";
import {
  ArrowRight,
  Check,
  ChevronDown,
  CheckSquare,
  Coins,
  LoaderCircle,
  Menu,
  MessageCircle,
  Pencil,
  Plus,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Trash2,
  X,
} from "lucide-react";
import * as api from "./api";
import type { Limits, Task, Workspace } from "./api";
import ProviderSetup from "./ProviderSetup";
import OllamaSearchSetup from "./OllamaSearchSetup";

type Page = "workspace" | "tasks" | "settings";
type Panel = "task" | "usage" | "rename-chat" | "delete-chat" | null;
const money = (value: number) =>
  `$${value.toFixed(value > 0 && value < 0.01 ? 4 : 2)}`;
const tokens = (value: number) =>
  value >= 1000 ? `${(value / 1000).toFixed(1)}k` : String(value);
const navigation = [
  { id: "workspace", name: "Workspace", icon: MessageCircle },
  { id: "tasks", name: "Tasks", icon: CheckSquare },
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
    element?.showModal();
    return () => element?.close();
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

function TaskRow({
  task,
  busy,
  onToggle,
  onDelete,
}: {
  task: Task;
  busy: boolean;
  onToggle: () => void;
  onDelete: () => void;
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
  const [model, setModel] = useState("");
  const modelPicker = useRef<HTMLDivElement>(null);
  const [mobileNav, setMobileNav] = useState(false);
  const navigationDrawer = useRef<HTMLElement>(null);
  const navigationMenu = useRef<HTMLButtonElement>(null);
  const chatEnd = useRef<HTMLDivElement>(null);
  const workspaceRevision = useRef(0);

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
  function refreshWorkspace() {
    const revision = ++workspaceRevision.current;
    void api
      .loadWorkspace()
      .then((next) => {
        if (workspaceRevision.current === revision) applyWorkspace(next);
      })
      .catch((reason: Error) => {
        if (workspaceRevision.current === revision) setError(reason.message);
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
  async function switchChat(action: () => Promise<Workspace>) {
    if (await perform("select-chat", action, undefined, true)) {
      setReplyId("");
      setChat("");
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
        const next = await api.sendMessage(submitted, id, requestedModel);
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
  const statusRevision = workspaceRevision.current;
  const requestedModel = local ? model : "";
  const defaultModel = workspace.provider.config.model;
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
                    {message.web_search && (
                      <div className="message-sources">
                        <p>Search query: {message.web_search.query}</p>
                        <ol>
                          {message.web_search.sources.map((source, index) => {
                            const url = sourceURL(source.url);
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
                              </li>
                            );
                          })}
                        </ol>
                      </div>
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
                  maxLength={4000}
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
                                setModel(choice === defaultModel ? "" : choice);
                                modelPicker.current?.hidePopover();
                              }}
                            >
                              <span>
                                {choice}
                                {choice === defaultModel && (
                                  <small>Default</small>
                                )}
                              </span>
                              {selectedModel === choice && <Check size={16} />}
                            </button>
                          ))}
                      </div>
                    </div>
                  ) : (
                    <small>{selectedModel || "No model selected"}</small>
                  )}
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
              {workspace.provider.config.protocol === "ollama" &&
                workspace.web_search.config.enabled && (
                  <p className="composer-note">
                    Automatic web search · one search maximum per message ·{" "}
                    {workspace.web_search.searches_today} /{" "}
                    {workspace.web_search.config.daily_limit} today. Queries go
                    to Ollama.com.
                  </p>
                )}
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
            <p className="page-note">Save and track tasks manually.</p>
            <div className="card task-board">
              {workspace.tasks.map((task) => (
                <TaskRow
                  key={task.id}
                  task={task}
                  busy={!!busy}
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

        {page === "settings" && (
          <main className="page-content">
            <div className="page-heading">
              <div>
                <h1>Settings</h1>
                <p>Configure your model, optional search and usage limits.</p>
              </div>
            </div>
            <div className="settings-layout">
              <div className="card settings-card">
                <h2>Usage & limits</h2>
                <UsageDetails workspace={workspace} />
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
                      {money(entry.reserved_usd)} reserved. Confirm actual
                      billed USD from your provider before retrying.
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
              </div>
              <div className="settings-side">
                {workspace.work && (
                  <WorkSetup
                    initialStatus={workspace.work}
                    provider={workspace.provider}
                    onChange={(work) => {
                      if (workspaceRevision.current === statusRevision)
                        setWorkspace(
                          (current) => current && { ...current, work },
                        );
                    }}
                  />
                )}
                <MemorySetup
                  memories={workspace.memories ?? []}
                  candidates={workspace.work?.candidates ?? []}
                  chats={workspace.chats}
                  chatId={workspace.active_chat_id}
                  onChange={refreshWorkspace}
                />
                <ProviderSetup
                  initialStatus={workspace.provider}
                  onChange={refreshWorkspace}
                />
                <OllamaSearchSetup
                  initialStatus={workspace.web_search}
                  provider={workspace.provider}
                  onChange={refreshWorkspace}
                />
              </div>
            </div>
          </main>
        )}
      </div>

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
              <textarea rows={4} maxLength={3000} name="details" />
            </label>
            <label>
              Priority
              <select name="priority" defaultValue="normal">
                <option value="normal">Normal</option>
                <option value="high">High</option>
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
          <LimitsForm
            limits={workspace.limits}
            onSave={(limits) => void saveLimits(limits)}
            busy={!!busy}
          />
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
