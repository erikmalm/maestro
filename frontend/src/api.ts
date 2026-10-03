export type Task = {
  id: string;
  title: string;
  details: string;
  priority: "normal" | "high";
  done: boolean;
  created_at: string;
};
export type MessageFeedback = {
  rating: "positive" | "negative";
  comment: string;
  updated_at: string;
};
export type Message = {
  id: string;
  role: "user" | "assistant";
  text: string;
  demo?: boolean;
  model?: string;
  kind?: "chat" | "orchestrator";
  memory_ids?: string[];
  feedback?: MessageFeedback | null;
  cost?: number;
  input_tokens?: number;
  output_tokens?: number;
  web_search?: {
    query: string;
    at: string;
    sources: { title: string; url: string }[];
  };
};
export type Limits = {
  run_usd: number;
  daily_usd: number;
  monthly_usd: number;
  max_tokens: number;
};
export type Chat = {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
};
export type Workspace = {
  tasks: Task[];
  chats: Chat[];
  active_chat_id: string | null;
  messages: Message[];
  memories?: Memory[];
  work?: WorkStatus;
  limits: Limits;
  usage: {
    today_usd: number;
    month_usd: number;
    input_tokens: number;
    output_tokens: number;
    calls: number;
    reserved_usd: number;
    uncertain: { id: string; at: string; reserved_usd: number }[];
  };
  provider: ProviderStatus;
  web_search: WebSearchStatus;
  capabilities: {
    mode: "local";
    live_ai: boolean;
    task_execution: false;
    delegation: false;
    github_pr: false;
    secure_credentials: boolean;
  };
};

let csrf = "";
class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}
async function request<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method,
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      ...(method === "GET" ? {} : { "X-Maestro-CSRF": csrf }),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (!response.ok) {
    let detail = "Could not reach your local workspace. Please try again.";
    try {
      const error = await response.json();
      if (typeof error.detail === "string") detail = error.detail;
    } catch {
      /* keep user-facing fallback */
    }
    throw new ApiError(detail, response.status);
  }
  return response.json() as Promise<T>;
}

export async function loadWorkspace(chatId?: string | null) {
  const session = await request<{ csrf: string }>("/session");
  csrf = session.csrf;
  const selected =
    chatId === undefined
      ? new URLSearchParams(window.location.search).get("chat")
      : chatId;
  try {
    return await request<Workspace>(
      `/workspace${selected ? `?chat_id=${encodeURIComponent(selected)}` : ""}`,
    );
  } catch (reason) {
    if (selected && reason instanceof ApiError && reason.status === 404) {
      return request<Workspace>("/workspace");
    }
    throw reason;
  }
}
export const createChat = () => request<Workspace>("/chats", "POST", {});
export const renameChat = (id: string, title: string) =>
  request<Workspace>(`/chats/${encodeURIComponent(id)}`, "PATCH", { title });
export const deleteChat = (id: string) =>
  request<Workspace>(`/chats/${encodeURIComponent(id)}`, "DELETE");
export const saveMessageFeedback = (
  chatId: string,
  messageId: string,
  rating: MessageFeedback["rating"] | null,
  comment: string,
) =>
  request<{
    message_id: string;
    feedback: MessageFeedback | null;
    work: WorkStatus;
    memories: Memory[];
  }>(
    `/chats/${encodeURIComponent(chatId)}/messages/${encodeURIComponent(messageId)}/feedback`,
    "PATCH",
    { rating, comment },
  );
export const addTask = (title: string, details: string, priority: string) =>
  request<Workspace>("/tasks", "POST", { title, details, priority });
export const toggleTask = (id: string, done: boolean) =>
  request<Workspace>(`/tasks/${id}`, "PATCH", { done });
export const deleteTask = (id: string) =>
  request<Workspace>(`/tasks/${id}`, "DELETE");
export const sendMessage = (
  text: string,
  chat_id?: string | null,
  model = "",
  role: "chat" | "orchestrator" = "chat",
) =>
  request<Workspace>("/chat", "POST", {
    text,
    role,
    ...(model ? { model } : {}),
    ...(chat_id ? { chat_id } : {}),
  });
export const saveLimits = (limits: Limits) =>
  request<Workspace>("/limits", "PUT", limits);

export type ProviderConfig = {
  base_url: string;
  protocol: "responses" | "chat_completions" | "ollama";
  model: string;
  orchestrator_model: string;
  input_usd_per_million: number;
  output_usd_per_million: number;
  pricing_verified: boolean;
  max_output_tokens: number;
  ollama_context_tokens: number;
  ollama_threads: number;
  ollama_keep_alive_minutes: number;
};
export type CredentialStatus = {
  credentials_present: boolean;
  credential_source: string;
  credential_error?: string;
  persist_supported?: boolean;
  managed_credentials?: boolean;
};
export type ProviderStatus = CredentialStatus & {
  config: ProviderConfig;
  credentials_required: boolean;
  ollama_base_url?: string;
  models: string[];
  tested_at: string | null;
};
export const saveProvider = (
  config: ProviderConfig,
  api_key: string,
  persist: boolean,
) => request<ProviderStatus>("/provider", "PUT", { config, api_key, persist });
export const testProvider = () =>
  request<ProviderStatus>("/provider/test", "POST", {});
export const deleteProviderKey = () =>
  request<ProviderStatus>("/provider/key", "DELETE");
export const reconcileProviderCharge = (id: string, billed_usd: number) =>
  request<Workspace>(`/provider/charges/${id}/reconcile`, "POST", {
    billed_usd,
  });

export type WebSearchConfig = {
  enabled: boolean;
  daily_limit: number;
  max_results: number;
};
export type WebSearchStatus = CredentialStatus & {
  config: WebSearchConfig;
  searches_today: number;
  remaining_today: number;
  paused_until: string | null;
  tested_at: string | null;
};
export const loadWebSearch = () => request<WebSearchStatus>("/web-search");
export const saveWebSearch = (
  config: WebSearchConfig,
  api_key: string,
  persist: boolean,
) =>
  request<WebSearchStatus>("/web-search", "PUT", { config, api_key, persist });
export const testWebSearch = () =>
  request<WebSearchStatus>("/web-search/test", "POST", {});
export const deleteWebSearchKey = () =>
  request<WebSearchStatus>("/web-search/key", "DELETE");

export type Memory = {
  id: string;
  content: string;
  scope: "workspace" | "conversation";
  chat_id: string | null;
  source_message_id: string | null;
  origin: "explicit" | "curated" | "reflective";
  kind?: "fact" | "preference" | "identity" | "lesson";
  pinned?: boolean;
  evidence?: string | null;
  provenance?: ((
    { chat_id: string; message_id: string } | { memory_id: string }
  ) & { hash?: string })[];
  created_at: string;
  updated_at: string;
};
export const remember = (
  content: string,
  scope: Memory["scope"],
  chat_id: string | null,
) =>
  request<Memory>("/memory", "POST", {
    content,
    scope,
    chat_id: scope === "conversation" ? chat_id : null,
  });
export const correctMemory = (id: string, content: string) =>
  request<Memory>(`/memory/${encodeURIComponent(id)}`, "PATCH", { content });
export const forgetMemory = (id: string) =>
  request<{ deleted: boolean }>(`/memory/${encodeURIComponent(id)}`, "DELETE");

export type WorkConfig = {
  reflection_model: string;
  memory_model: string;
  coding_model: string;
  enabled: boolean;
  auto_curate: boolean;
  periodic_reflection: boolean;
  reflection_interval_minutes: number;
  background_context_tokens: number;
  reflection_exchange_count: number;
  reflection_context_characters: number;
  debounce_seconds: number;
  idle_seconds: number;
  max_output_tokens: number;
  max_jobs_per_day: number;
  max_tokens_per_day: number;
  timeout_seconds: number;
  memory_recall_count: number;
  memory_recall_characters: number;
};
export type WorkStatus = {
  config: WorkConfig;
  worker_available: boolean;
  waiting_for_ollama?: boolean;
  queued: number;
  running: boolean;
  last_stop_reason: string | null;
  candidates: MemoryCandidate[];
  today_jobs: number;
  today_tokens: number;
  memories?: Memory[];
  journal?: ReflectionJournalEntry[];
  next_reflection_at?: string | null;
};
export type ReflectionJournalEntry = {
  id: string;
  created_at: string;
  kind: "curation" | "reflection";
  summary: string;
  outcome?: string;
  changes: {
    operation: "add" | "update" | "remove";
    memory_id: string;
    kind: NonNullable<Memory["kind"]>;
    scope?: Memory["scope"];
    content: string;
  }[];
  sources: { chat_id: string; message_id: string }[];
  models: string[];
};
export type MemoryCandidate = {
  id: string;
  content: string;
  chat_id: string;
  source_message_id: string;
  created_at: string;
  evidence: string;
};
export const loadReflection = () => request<WorkStatus>("/reflection");
export const saveWorkConfig = (config: WorkConfig) =>
  request<WorkStatus>("/work-config", "PUT", config);
export const acceptMemoryCandidate = (id: string, scope: Memory["scope"]) =>
  request<Memory>(
    `/reflection/candidates/${encodeURIComponent(id)}/accept`,
    "POST",
    {
      scope,
    },
  );
export const rejectMemoryCandidate = (id: string) =>
  request<{ deleted: boolean }>(
    `/reflection/candidates/${encodeURIComponent(id)}`,
    "DELETE",
  );
