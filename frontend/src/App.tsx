import { useEffect, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import {
  ArrowRight,
  ArrowUpRight,
  BookOpen,
  Check,
  CheckCheck,
  CheckSquare,
  ChevronRight,
  CircleHelp,
  Coins,
  FileText,
  GitPullRequest,
  Layers3,
  ListTodo,
  LoaderCircle,
  Menu,
  MessageCircle,
  Play,
  Plug,
  Plus,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Target,
  Trash2,
  Users,
  Workflow,
  X,
  Zap,
} from "lucide-react";
import * as api from "./api";
import type { Limits, Run, Task, Workspace } from "./api";

type Page =
  | "workspace"
  | "tasks"
  | "runs"
  | "agents"
  | "memory"
  | "integrations"
  | "settings";
type Panel = "task" | "usage" | "memory" | null;
const money = (value: number) => `$${value.toFixed(2)}`;
const tokens = (value: number) =>
  value >= 1000 ? `${(value / 1000).toFixed(1)}k` : String(value);
const navigation = [
  { id: "workspace", name: "Workspace", icon: MessageCircle },
  { id: "tasks", name: "Tasks", icon: CheckSquare },
  { id: "runs", name: "Runs", icon: Workflow },
  { id: "agents", name: "Agents", icon: Users },
  { id: "memory", name: "Memory", icon: BookOpen },
  { id: "integrations", name: "Integrations", icon: Plug },
] as const;

function Mark({ small = false }: { small?: boolean }) {
  return (
    <div
      className={`maestro-mark ${small ? "mark-small" : ""}`}
      aria-hidden="true"
    >
      <i />
      <i />
      <i />
      <i />
    </div>
  );
}

function Modal({
  title,
  subtitle,
  children,
  onClose,
}: {
  title: string;
  subtitle: string;
  children: ReactNode;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    dialog.current?.showModal();
    return () => dialog.current?.close();
  }, []);
  return (
    <dialog
      ref={dialog}
      className="modal"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
      aria-labelledby="modal-title"
    >
      <div className="modal-heading">
        <div>
          <h2 id="modal-title">{title}</h2>
          <p>{subtitle}</p>
        </div>
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
  const [draft, setDraft] = useState(limits);
  useEffect(() => setDraft(limits), [limits]);
  const change = (key: keyof Limits, value: string) =>
    setDraft((previous) => ({ ...previous, [key]: Number(value) }));
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        onSave(draft);
      }}
      className="limits-form"
    >
      <div className="form-section-label">
        SPENDING LIMITS <span>USD</span>
      </div>
      <div className="budget-inputs">
        {(
          [
            { key: "run_usd", label: "Per run", max: 10000 },
            { key: "daily_usd", label: "Per day", max: 100000 },
            { key: "monthly_usd", label: "Per month", max: 1000000 },
          ] as const
        ).map((field) => (
          <label key={field.key}>
            {field.label}
            <div className="money-input">
              <span aria-hidden="true">$</span>
              <input
                aria-label={field.label}
                required
                type="number"
                min="0"
                max={field.max}
                step="0.01"
                value={draft[field.key]}
                onChange={(event) => change(field.key, event.target.value)}
              />
            </div>
          </label>
        ))}
      </div>
      <div className="form-section-label secondary-label">EXECUTION LIMITS</div>
      <div className="execution-inputs">
        <label>
          Tokens per run
          <input
            type="number"
            min="0"
            max="10000000"
            step="1"
            required
            value={draft.max_tokens}
            onChange={(event) => change("max_tokens", event.target.value)}
          />
        </label>
        <label>
          Refinements
          <input
            type="number"
            min="0"
            max="10"
            step="1"
            required
            value={draft.max_refinements}
            onChange={(event) => change("max_refinements", event.target.value)}
          />
        </label>
        <label>
          Minutes per run
          <input
            type="number"
            min="1"
            max="120"
            step="1"
            required
            value={draft.max_minutes}
            onChange={(event) => change("max_minutes", event.target.value)}
          />
        </label>
      </div>
      <p className="form-note">
        <ShieldCheck size={15} /> Limits are shared by all agents in a run.
      </p>
      <div className="form-actions">
        <span>Saved privately on this computer</span>
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
  const { usage, limits } = workspace;
  const percent =
    limits.daily_usd > 0
      ? Math.min(100, (usage.today_usd / limits.daily_usd) * 100)
      : usage.today_usd > 0
        ? 100
        : 0;
  return (
    <div className="usage-details">
      <div className="usage-total">
        <div>
          <span>Simulated spend today</span>
          <strong>
            {money(usage.today_usd)} <small>/ {money(limits.daily_usd)}</small>
          </strong>
        </div>
        <span className="badge lavender">Demo data</span>
      </div>
      <div className="progress-track">
        <div style={{ width: `${percent}%` }} />
      </div>
      <div className="usage-breakdown">
        <div>
          <span>Input tokens</span>
          <strong>{usage.input_tokens.toLocaleString()}</strong>
        </div>
        <div>
          <span>Output tokens</span>
          <strong>{usage.output_tokens.toLocaleString()}</strong>
        </div>
        <div>
          <span>Demo calls</span>
          <strong>{usage.calls}</strong>
        </div>
      </div>
      <div className="usage-month">
        <span>Simulated month to date</span>
        <strong>
          {money(usage.month_usd)} <span>/ {money(limits.monthly_usd)}</span>
        </strong>
      </div>
      <p className="preview-note">
        <CircleHelp size={15} /> These are example usage figures. This preview
        makes no paid API calls. Live usage and billing reconciliation come with
        the provider integration.
      </p>
    </div>
  );
}

function TaskRow({
  task,
  busy,
  onToggle,
  onPreview,
  onDelete,
  compact = false,
}: {
  task: Task;
  busy: boolean;
  onToggle: () => void;
  onPreview: () => void;
  onDelete?: () => void;
  compact?: boolean;
}) {
  return (
    <div
      className={`task-row ${task.done ? "task-done" : ""} ${compact ? "task-compact" : ""}`}
    >
      <button
        className={`task-checkbox ${task.done ? "checked" : ""}`}
        disabled={busy}
        onClick={onToggle}
        aria-label={`${task.done ? "Reopen" : "Complete"} ${task.title}`}
        aria-pressed={task.done}
      >
        {task.done && <Check size={13} />}
      </button>
      <div className="task-copy">
        <span className="task-title">{task.title}</span>
        {!compact && task.details && <p>{task.details}</p>}
        <div className="task-meta">
          <span
            className={`agent-dot ${task.agent === "Work organizer" ? "green-dot" : ""}`}
          />
          {task.agent}
          {task.priority === "high" && (
            <span className="priority-tag">Priority</span>
          )}
        </div>
      </div>
      {!compact && (
        <div className="task-actions">
          <button
            className="button subtle small"
            disabled={busy || task.done}
            onClick={onPreview}
          >
            <Play size={13} /> Preview run
          </button>
          {onDelete && (
            <button
              className="icon-button"
              disabled={busy}
              onClick={onDelete}
              aria-label={`Delete ${task.title}`}
            >
              <Trash2 size={15} />
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function RunDetail({ run }: { run: Run }) {
  return (
    <div className="run-detail">
      <div className="run-detail-top">
        <span className="badge green">
          <CheckCheck size={13} /> Simulation finished
        </span>
        <span>
          {money(run.cost)} · {tokens(run.input_tokens + run.output_tokens)}{" "}
          tokens
        </span>
      </div>
      <h3>{run.title}</h3>
      <div className="timeline">
        {run.steps.map((step, index) => (
          <div className="timeline-step" key={step.title}>
            <div className="timeline-icon">
              {index === 0 ? (
                <Layers3 size={16} />
              ) : index === 1 ? (
                <Users size={16} />
              ) : (
                <CheckCheck size={16} />
              )}
            </div>
            <div>
              <h4>{step.title}</h4>
              <p>{step.detail}</p>
            </div>
          </div>
        ))}
      </div>
      <div className="run-footnote">
        <ShieldCheck size={15} /> All steps and usage are simulated. No external
        tools were called.
      </div>
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
  const [taskTitle, setTaskTitle] = useState("");
  const [taskDetails, setTaskDetails] = useState("");
  const [priority, setPriority] = useState("normal");
  const [memoryText, setMemoryText] = useState("");
  const [selectedRun, setSelectedRun] = useState<string | null>(null);
  const [mobileNav, setMobileNav] = useState(false);
  const chatEnd = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api
      .loadWorkspace()
      .then(setWorkspace)
      .catch((reason: Error) => setError(reason.message));
  }, []);
  useEffect(() => {
    if (toast) {
      const timer = window.setTimeout(() => setToast(""), 3500);
      return () => window.clearTimeout(timer);
    }
  }, [toast]);
  useEffect(() => {
    chatEnd.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [workspace?.messages.length]);

  async function perform(
    key: string,
    action: () => Promise<Workspace>,
    success?: string,
  ) {
    setBusy(key);
    setError("");
    try {
      const next = await action();
      setWorkspace(next);
      if (success) setToast(success);
      return true;
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Something went wrong. Please try again.",
      );
      return false;
    } finally {
      setBusy("");
    }
  }
  const navigate = (next: Page) => {
    setPage(next);
    setMobileNav(false);
  };
  const openTask = () => {
    setTaskTitle("");
    setTaskDetails("");
    setPriority("normal");
    setPanel("task");
  };
  async function createTask(event: FormEvent) {
    event.preventDefault();
    if (
      await perform(
        "task",
        () => api.addTask(taskTitle, taskDetails, priority),
        "Task saved to your local workspace.",
      )
    )
      setPanel(null);
  }
  async function submitChat(event: FormEvent) {
    event.preventDefault();
    if (!chat.trim() || busy) return;
    if (await perform("chat", () => api.sendMessage(chat))) setChat("");
  }
  async function simulate(task: Task) {
    if (
      await perform(
        `run-${task.id}`,
        () => api.previewRun(task.id),
        "Preview finished. No API charges.",
      )
    ) {
      setSelectedRun(null);
      setPage("runs");
    }
  }
  async function save(limits: Limits) {
    if (
      await perform(
        "limits",
        () => api.saveLimits(limits),
        "Your limits are saved.",
      )
    )
      setPanel(null);
  }

  if (!workspace)
    return (
      <div className="loading-screen">
        <Mark />
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
            <LoaderCircle size={15} className="spin" /> Opening your local
            workspace…
          </p>
        )}
      </div>
    );
  const openTasks = workspace.tasks.filter((task) => !task.done);
  const percent =
    workspace.limits.daily_usd > 0
      ? Math.min(
          100,
          (workspace.usage.today_usd / workspace.limits.daily_usd) * 100,
        )
      : 100;
  const currentRun =
    workspace.runs.find((run) => run.id === selectedRun) ?? workspace.runs[0];
  const pageLabel =
    page === "workspace"
      ? "Overview"
      : page.charAt(0).toUpperCase() + page.slice(1);

  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobileNav ? "sidebar-open" : ""}`}>
        <div className="brand">
          <Mark />
          <div>
            <strong>
              maestro<span>.</span>
            </strong>
            <small>Your personal workspace</small>
          </div>
          <button
            className="icon-button mobile-close"
            onClick={() => setMobileNav(false)}
            aria-label="Close navigation"
          >
            <X size={18} />
          </button>
        </div>
        <div className="nav-caption">WORKSPACE</div>
        <nav aria-label="Main navigation">
          {navigation.map((item) => (
            <button
              className={`nav-item ${page === item.id ? "active" : ""}`}
              key={item.id}
              onClick={() => navigate(item.id)}
              aria-current={page === item.id ? "page" : undefined}
            >
              <item.icon size={18} />
              <span>{item.name}</span>
              {item.id === "tasks" && <small>{openTasks.length}</small>}
            </button>
          ))}
        </nav>
        <div className="sidebar-spacer" />
        <div className="sidebar-budget">
          <div>
            <span className="tiny-spark">
              <Sparkles size={14} />
            </span>
            <strong>Your limits, your pace</strong>
          </div>
          <p>A little visibility goes a long way.</p>
          <div className="sidebar-budget-values">
            <span>Demo today</span>
            <strong>
              {money(workspace.usage.today_usd)}{" "}
              <span>/ {money(workspace.limits.daily_usd)}</span>
            </strong>
          </div>
          <div className="progress-track">
            <div style={{ width: `${percent}%` }} />
          </div>
          <button onClick={() => setPanel("usage")}>
            Manage usage <ArrowUpRight size={14} />
          </button>
        </div>
        <button
          className={`nav-item settings-nav ${page === "settings" ? "active" : ""}`}
          onClick={() => navigate("settings")}
        >
          <Settings2 size={18} />
          <span>Settings</span>
        </button>
        <div className="local-profile">
          <div className="profile-avatar">Y</div>
          <div>
            <strong>Your workspace</strong>
            <span>
              <i /> Local & private
            </span>
          </div>
          <ShieldCheck size={17} />
        </div>
      </aside>
      {mobileNav && (
        <button
          className="nav-backdrop"
          aria-label="Close navigation"
          onClick={() => setMobileNav(false)}
        />
      )}

      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumbs">
            <button
              className="icon-button mobile-menu"
              aria-label="Open navigation"
              onClick={() => setMobileNav(true)}
            >
              <Menu size={19} />
            </button>
            <span>Workspace</span>
            <ChevronRight size={13} />
            <strong>{pageLabel}</strong>
          </div>
          <div className="topbar-right">
            <span className="preview-badge">
              <i /> Local preview
            </span>
            <button
              className="usage-strip"
              onClick={() => setPanel("usage")}
              aria-label="View usage and manage budgets"
            >
              <span>
                <Coins size={14} />
                <strong>{money(workspace.usage.today_usd)}</strong>
                <small>demo today</small>
              </span>
              <span className="usage-divider" />
              <span>
                <Zap size={14} />
                <strong>
                  {tokens(
                    workspace.usage.input_tokens +
                      workspace.usage.output_tokens,
                  )}
                </strong>
                <small>tokens</small>
              </span>
              <SlidersHorizontal size={15} className="usage-settings-icon" />
            </button>
          </div>
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

        {page === "workspace" ? (
          <main className="workspace-layout">
            <section className="conversation-pane">
              <div className="welcome-heading">
                <div className="eyebrow">
                  <span /> A SPACE FOR YOUR NEXT GOOD IDEA
                </div>
                <h1>What’s on your mind?</h1>
                <p>Think it through. Make a plan. Bring in the right agent.</p>
              </div>
              {workspace.messages.length === 0 ? (
                <div className="conversation-intro">
                  <div className="intro-mark">
                    <Mark small />
                  </div>
                  <h2>
                    A thoughtful partner.
                    <br />A capable team.
                  </h2>
                  <p>
                    I’ll help you bring a little order to your work.
                    <br />
                    Start with a thought, a question, or something to do.
                  </p>
                  <div className="suggestion-list">
                    <button
                      onClick={() =>
                        setChat("Help me plan my priorities for the week.")
                      }
                    >
                      <ListTodo size={16} />
                      <span>Help me plan my week</span>
                      <ArrowUpRight size={14} />
                    </button>
                    <button
                      onClick={() =>
                        setChat("Help me review a set of financial documents.")
                      }
                    >
                      <FileText size={16} />
                      <span>Review some documents</span>
                      <ArrowUpRight size={14} />
                    </button>
                    <button
                      onClick={() =>
                        setChat("Help me turn an idea into a practical plan.")
                      }
                    >
                      <Sparkles size={16} />
                      <span>Think through an idea</span>
                      <ArrowUpRight size={14} />
                    </button>
                  </div>
                </div>
              ) : (
                <div className="chat-history" aria-live="polite">
                  {workspace.messages.map((message) => (
                    <div
                      className={`chat-message ${message.role}`}
                      key={message.id}
                    >
                      {message.role === "assistant" && <Mark small />}
                      <div>
                        <span className="message-author">
                          {message.role === "assistant"
                            ? "Maestro · preview"
                            : "You"}
                        </span>
                        <p>{message.text}</p>
                      </div>
                    </div>
                  ))}
                  <div ref={chatEnd} />
                </div>
              )}
              <div className="composer-area">
                <form className="composer" onSubmit={submitChat}>
                  <textarea
                    aria-label="Message Maestro"
                    placeholder="Ask, explore, or give Maestro something to do…"
                    value={chat}
                    maxLength={4000}
                    rows={2}
                    onChange={(event) => setChat(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" && !event.shiftKey) {
                        event.preventDefault();
                        void submitChat(event);
                      }
                    }}
                  />
                  <div className="composer-bottom">
                    <span>
                      <span className="agent-dot" /> Coordinator{" "}
                      <ChevronRight size={12} />
                      <small>Preview mode</small>
                    </span>
                    <button
                      type="submit"
                      className="send-button"
                      disabled={!chat.trim() || !!busy}
                      aria-label="Send message"
                    >
                      {busy === "chat" ? (
                        <LoaderCircle size={17} className="spin" />
                      ) : (
                        <ArrowRight size={19} />
                      )}
                    </button>
                  </div>
                </form>
                <p className="composer-note">
                  <ShieldCheck size={12} /> Saved locally. Demo replies. No paid
                  API calls.
                </p>
              </div>
            </section>
            <aside className="focus-pane">
              <div className="focus-header">
                <div>
                  <span className="eyebrow">A LITTLE FORWARD MOMENTUM</span>
                  <h2>In focus</h2>
                </div>
                <button
                  className="icon-button add-task-icon"
                  onClick={openTask}
                  aria-label="New task"
                >
                  <Plus size={19} />
                </button>
              </div>
              <div className="focus-task-list">
                {openTasks.slice(0, 3).map((task) => (
                  <TaskRow
                    key={task.id}
                    task={task}
                    compact
                    busy={!!busy}
                    onToggle={() =>
                      void perform("toggle", () =>
                        api.toggleTask(task.id, true),
                      )
                    }
                    onPreview={() => void simulate(task)}
                  />
                ))}
                {openTasks.length === 0 && (
                  <div className="small-empty">
                    <CheckCheck size={22} />
                    <p>
                      A little breathing room.
                      <br />
                      Add your next task when you’re ready.
                    </p>
                  </div>
                )}
              </div>
              <button
                className="text-button all-tasks"
                onClick={() => navigate("tasks")}
              >
                View all tasks <ArrowRight size={14} />
              </button>
              <div className="rail-divider" />
              <div className="section-heading">
                <h3>Recent activity</h3>
                <span className="quiet-label">Demo</span>
              </div>
              <div className="recent-runs">
                {workspace.runs.slice(0, 2).map((run) => (
                  <button
                    key={run.id}
                    className="recent-run"
                    onClick={() => {
                      setSelectedRun(run.id);
                      navigate("runs");
                    }}
                  >
                    <div className="activity-icon">
                      <CheckCheck size={17} />
                    </div>
                    <div>
                      <strong>{run.title}</strong>
                      <span>
                        {run.agent} <i>·</i> {money(run.cost)}
                      </span>
                    </div>
                    <ChevronRight size={14} />
                  </button>
                ))}
              </div>
              <div className="memory-peek">
                <div className="memory-peek-title">
                  <BookOpen size={17} />
                  <strong>A little context helps</strong>
                </div>
                <p>
                  Your preferences and project context stay close, so each
                  conversation can start a little further ahead.
                </p>
                <button
                  className="text-button"
                  onClick={() => navigate("memory")}
                >
                  Explore your memory <ArrowUpRight size={13} />
                </button>
              </div>
              <div className="rail-footer">
                <span className="status-dot" /> Your workspace stays on this
                computer.
              </div>
            </aside>
          </main>
        ) : (
          <main className="page-content">
            <div className="page-heading">
              <div>
                <span className="eyebrow">YOUR WORKSPACE</span>
                <h1>{pageLabel}</h1>
                <p>
                  {
                    (
                      {
                        tasks: "A clear next step is a good place to start.",
                        runs: "See how your team moves a task forward.",
                        agents:
                          "The right perspective for each part of the work.",
                        memory:
                          "Useful context, kept close and under your control.",
                        integrations:
                          "Connect the places your work already lives.",
                        settings: "Make Maestro work at your pace.",
                      } as Record<string, string>
                    )[page]
                  }
                </p>
              </div>
              {page === "tasks" && (
                <button className="button primary" onClick={openTask}>
                  <Plus size={16} /> New task
                </button>
              )}
              {page === "memory" && (
                <button
                  className="button primary"
                  onClick={() => {
                    setMemoryText("");
                    setPanel("memory");
                  }}
                >
                  <Plus size={16} /> Add memory
                </button>
              )}
            </div>

            {page === "tasks" && (
              <>
                <div className="section-tabs">
                  <span className="selected">
                    All tasks <b>{workspace.tasks.length}</b>
                  </span>
                  <span>{openTasks.length} open</span>
                  <span>
                    {workspace.tasks.length - openTasks.length} completed
                  </span>
                </div>
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
                      onPreview={() => void simulate(task)}
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
                      <Target size={30} />
                      <h3>Make room for your next idea</h3>
                      <p>Add a task to start organizing your work.</p>
                      <button className="button primary" onClick={openTask}>
                        <Plus size={16} /> New task
                      </button>
                    </div>
                  )}
                </div>
                <p className="page-footnote">
                  <CircleHelp size={14} /> Preview run simulates delegation and
                  usage. It does not perform or complete your real task.
                </p>
              </>
            )}

            {page === "runs" && (
              <>
                <div className="runs-layout">
                  <div className="card run-list">
                    {workspace.runs.map((run) => (
                      <button
                        key={run.id}
                        className={`run-list-item ${currentRun?.id === run.id ? "selected" : ""}`}
                        onClick={() => setSelectedRun(run.id)}
                      >
                        <div className="run-list-icon">
                          <Workflow size={18} />
                        </div>
                        <div>
                          <strong>{run.title}</strong>
                          <span>{run.agent}</span>
                          <small>
                            {money(run.cost)} <i>·</i>{" "}
                            {tokens(run.input_tokens + run.output_tokens)}{" "}
                            tokens
                          </small>
                        </div>
                        <Check size={15} />
                      </button>
                    ))}
                  </div>
                  <div className="card">
                    {currentRun ? (
                      <RunDetail run={currentRun} />
                    ) : (
                      <div className="empty-state">
                        <Workflow size={30} />
                        <h3>No runs yet</h3>
                        <p>Preview a task to inspect its delegation flow.</p>
                      </div>
                    )}
                  </div>
                </div>
                <p className="page-footnote">
                  <CircleHelp size={14} /> Example run history. Live execution,
                  cancellation, and retries arrive with the agent runner.
                </p>
              </>
            )}

            {page === "agents" && (
              <>
                <div className="agent-grid">
                  {[
                    {
                      name: "Coordinator",
                      icon: Layers3,
                      color: "purple",
                      role: "The big picture",
                      text: "Plans the work, chooses specialists, and brings the result back to you.",
                      tools: "Task planning · Delegation · Synthesis",
                    },
                    {
                      name: "Work organizer",
                      icon: ListTodo,
                      color: "mint",
                      role: "A practical next step",
                      text: "Turns ideas into focused tasks, useful priorities, and achievable plans.",
                      tools: "Task breakdown · Prioritization",
                    },
                    {
                      name: "Document analyst",
                      icon: FileText,
                      color: "peach",
                      role: "Clarity from evidence",
                      text: "Reviews permitted documents and connects each finding to its source.",
                      tools: "Document retrieval · Source references",
                    },
                    {
                      name: "Reviewer",
                      icon: CheckCheck,
                      color: "blue",
                      role: "A fresh pair of eyes",
                      text: "Checks completion criteria and suggests focused improvements.",
                      tools: "Evaluation · Refinement feedback",
                    },
                  ].map((agent) => (
                    <div className="card agent-card" key={agent.name}>
                      <div className={`agent-card-icon ${agent.color}`}>
                        <agent.icon size={23} />
                      </div>
                      <span className="quiet-label">{agent.role}</span>
                      <h2>{agent.name}</h2>
                      <p>{agent.text}</p>
                      <div className="agent-tools">{agent.tools}</div>
                      <span className="badge neutral">Planned role</span>
                    </div>
                  ))}
                </div>
                <div className="orchestration-note">
                  <Workflow size={23} />
                  <div>
                    <h3>One coordinator. A shared allowance.</h3>
                    <p>
                      Plan → delegate → review → improve or stop. Every
                      specialist inherits the same permissions and remaining
                      budget.
                    </p>
                  </div>
                  <button
                    className="button subtle"
                    onClick={() => setPanel("usage")}
                  >
                    <SlidersHorizontal size={15} /> Manage limits
                  </button>
                </div>
              </>
            )}

            {page === "memory" && (
              <>
                <div className="memory-info">
                  <ShieldCheck size={18} />
                  <p>
                    These entries are saved outside the repository. This preview
                    supports manual memory; AI recall and learning come next.
                  </p>
                </div>
                <div className="memory-grid">
                  {workspace.memories.map((memory) => (
                    <div className="card memory-card" key={memory.id}>
                      <div>
                        <span className="badge neutral">{memory.category}</span>
                        <button
                          className="icon-button"
                          disabled={!!busy}
                          aria-label={`Forget ${memory.text}`}
                          onClick={() =>
                            void perform(
                              "memory-delete",
                              () => api.deleteMemory(memory.id),
                              "Memory removed from the preview workspace.",
                            )
                          }
                        >
                          <Trash2 size={15} />
                        </button>
                      </div>
                      <p>{memory.text}</p>
                      <span className="memory-local">
                        <ShieldCheck size={13} /> Local only
                      </span>
                    </div>
                  ))}
                </div>
                {workspace.memories.length === 0 && (
                  <div className="card empty-state">
                    <BookOpen size={30} />
                    <h3>A fresh start</h3>
                    <p>Save a preference or a piece of project context.</p>
                  </div>
                )}
              </>
            )}

            {page === "integrations" && (
              <div className="integration-grid">
                <div className="card integration-card">
                  <div className="integration-logo github">
                    <GitPullRequest size={25} />
                  </div>
                  <span className="badge neutral">Planned</span>
                  <h2>GitHub</h2>
                  <p>
                    Let authorized coding tasks become reviewable draft pull
                    requests in your selected repositories.
                  </p>
                  <ul>
                    <li>
                      <Check size={14} /> Repository allowlist
                    </li>
                    <li>
                      <Check size={14} /> Isolated branches and worktrees
                    </li>
                    <li>
                      <Check size={14} /> Secret scan before publishing
                    </li>
                    <li>
                      <Check size={14} /> You control merging
                    </li>
                  </ul>
                  <div className="integration-footer">
                    <ShieldCheck size={14} /> Credential setup arrives with the
                    connector.
                  </div>
                </div>
                <div className="card integration-card">
                  <div className="integration-logo marketpulse">
                    <FileText size={25} />
                  </div>
                  <span className="badge neutral">Planned</span>
                  <h2>MarketPulse</h2>
                  <p>
                    Bring financial documentation into your research with scoped
                    access and clear source references.
                  </p>
                  <ul>
                    <li>
                      <Check size={14} /> Read-only document access
                    </li>
                    <li>
                      <Check size={14} /> Selected endpoints or folders
                    </li>
                    <li>
                      <Check size={14} /> References for each finding
                    </li>
                    <li>
                      <Check size={14} /> Local-only sharing controls
                    </li>
                  </ul>
                  <div className="integration-footer">
                    <ShieldCheck size={14} /> Source details stay outside
                    GitHub.
                  </div>
                </div>
              </div>
            )}

            {page === "settings" && (
              <div className="settings-layout">
                <div className="card settings-card">
                  <div className="settings-card-heading">
                    <div className="setting-icon">
                      <SlidersHorizontal size={21} />
                    </div>
                    <div>
                      <h2>Usage & limits</h2>
                      <p>Always visible. Always in your control.</p>
                    </div>
                  </div>
                  <UsageDetails workspace={workspace} />
                  <LimitsForm
                    limits={workspace.limits}
                    onSave={(limits) => void save(limits)}
                    busy={busy === "limits"}
                  />
                </div>
                <div className="settings-side">
                  <div className="card credential-card">
                    <div className="setting-icon">
                      <ShieldCheck size={22} />
                    </div>
                    <h3>Provider credentials</h3>
                    <p>
                      Secure key setup is the next milestone. Keys will be
                      stored in the OS credential store.
                    </p>
                    <label>
                      API key
                      <input
                        type="password"
                        disabled
                        placeholder="Not available in this preview"
                        aria-label="API key setup not yet available"
                      />
                    </label>
                    <span className="badge neutral">No provider connected</span>
                  </div>
                  <div className="settings-hint">
                    <BookOpen size={18} />
                    <h3>A private workspace</h3>
                    <p>
                      Tasks, conversations, memories, and settings are saved in
                      your per-user application data folder, outside this
                      repository.
                    </p>
                  </div>
                </div>
              </div>
            )}
          </main>
        )}
      </div>

      {panel === "task" && (
        <Modal
          title="A new next step"
          subtitle="Save a task now. Decide when to run it."
          onClose={() => setPanel(null)}
        >
          <form
            onSubmit={(event) => void createTask(event)}
            className="entry-form"
          >
            <label>
              What would you like to do?
              <input
                autoFocus
                required
                maxLength={200}
                placeholder="Give your task a clear name"
                value={taskTitle}
                onChange={(event) => setTaskTitle(event.target.value)}
              />
            </label>
            <label>
              Context & completion criteria
              <textarea
                rows={4}
                maxLength={3000}
                placeholder="What matters? What would a good result look like?"
                value={taskDetails}
                onChange={(event) => setTaskDetails(event.target.value)}
              />
            </label>
            <label>
              Priority
              <select
                value={priority}
                onChange={(event) => setPriority(event.target.value)}
              >
                <option value="normal">Normal</option>
                <option value="high">High</option>
              </select>
            </label>
            <p className="form-note">
              <ShieldCheck size={15} /> Saving a task makes no API calls.
            </p>
            <div className="form-actions">
              <button
                type="button"
                className="button subtle"
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
        <Modal
          title="Your usage, at a glance"
          subtitle="Understand the activity. Set your own pace."
          onClose={() => setPanel(null)}
        >
          <UsageDetails workspace={workspace} />
          <LimitsForm
            limits={workspace.limits}
            onSave={(limits) => void save(limits)}
            busy={busy === "limits"}
          />
        </Modal>
      )}
      {panel === "memory" && (
        <Modal
          title="Keep a little context"
          subtitle="Save a preference or something useful for later."
          onClose={() => setPanel(null)}
        >
          <form
            className="entry-form"
            onSubmit={async (event) => {
              event.preventDefault();
              if (
                await perform(
                  "memory-add",
                  () => api.addMemory(memoryText),
                  "Memory saved locally.",
                )
              )
                setPanel(null);
            }}
          >
            <label>
              What should Maestro remember?
              <textarea
                autoFocus
                rows={4}
                required
                maxLength={4000}
                value={memoryText}
                onChange={(event) => setMemoryText(event.target.value)}
                placeholder="A preference, decision, or piece of project context…"
              />
            </label>
            <p className="form-note">
              <ShieldCheck size={15} /> Stored locally. Do not enter API keys
              here.
            </p>
            <div className="form-actions">
              <button
                className="button primary"
                type="submit"
                disabled={!!busy}
              >
                <BookOpen size={16} /> Save memory
              </button>
            </div>
          </form>
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
