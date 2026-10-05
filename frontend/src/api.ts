export type Task = {
  id: string;
  title: string;
  details: string;
  priority: "normal" | "high";
  done: boolean;
  created_at: string;
  initiated_by?: "user" | "maestro";
  suggested_assignee?: "user" | "maestro";
};
export type MessageFeedback = {
  rating: "positive" | "negative";
  comment: string;
  updated_at: string;
};
export type ContextMode = "prefer_saved" | "refresh" | "saved_only";
export type CitationSource = {
  title: string;
  url: string;
  capture_id?: string;
  content_kind?: string;
  retrieved_at?: string;
  published_at?: string | null;
  modified_at?: string | null;
  content_hash?: string;
  manifest_hash?: string;
  stale?: boolean;
  archive_status?: "saved" | "not_saved" | "skipped";
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
  source_context?: { citations?: CitationSource[] };
  web_search?: {
    query: string;
    at: string | null;
    from_cache?: boolean;
    stale?: boolean;
    retrieval?: "exact_query" | "keyword" | "web_search";
    filters?: {
      domain?: string;
      retrieved_from?: string;
      retrieved_to?: string;
    };
    search_limited?: boolean;
    archive_warning?: string;
    sources: CitationSource[];
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
export type LocalRequest = {
  id: string;
  at: string;
  model: string;
  base_url: string | null;
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
    local_requests?: LocalRequest[];
  };
  provider: ProviderStatus;
  web_search: WebSearchStatus;
  context_archive?: ContextArchiveStatus;
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
export const addTask = (
  title: string,
  details: string,
  priority: string,
  suggested_assignee: NonNullable<Task["suggested_assignee"]> = "user",
) =>
  request<Workspace>("/tasks", "POST", {
    title,
    details,
    priority,
    suggested_assignee,
  });
export const assignTask = (
  id: string,
  suggested_assignee: NonNullable<Task["suggested_assignee"]>,
) =>
  request<Workspace>(`/tasks/${encodeURIComponent(id)}`, "PATCH", {
    suggested_assignee,
  });
export const toggleTask = (id: string, done: boolean) =>
  request<Workspace>(`/tasks/${encodeURIComponent(id)}`, "PATCH", { done });
export const deleteTask = (id: string) =>
  request<Workspace>(`/tasks/${encodeURIComponent(id)}`, "DELETE");
export const sendMessage = (
  text: string,
  chat_id?: string | null,
  model = "",
  context_mode: ContextMode = "prefer_saved",
) =>
  request<Workspace>("/chat", "POST", {
    text,
    role: "chat",
    context_mode,
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
  chat_routing?: "direct" | "orchestrator";
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
export const recoverLocalRequest = (id: string, expected_base_url: string) =>
  request<Workspace>(
    `/provider/local-requests/${encodeURIComponent(id)}/recover`,
    "POST",
    { restart_confirmed: true, expected_base_url },
  );

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
  ready: boolean;
  unavailable_reason: string | null;
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

export type ContextArchiveConfig = {
  enabled: boolean;
  capture_policy?: "approved_sources" | "all_public";
  public_sources: string[];
  reuse_hours: number;
  max_bytes: number;
  max_items: number;
};
export type ContextArchiveStatus = {
  configured: boolean;
  available: boolean;
  path: string | null;
  config: ContextArchiveConfig;
  indexed_count: number;
  missing_count: number;
  corrupt_count: number;
  archive_bytes: number;
  archive_items: number;
  last_error: string | null;
  last_indexed_at: string | null;
  rebuild_progress?: {
    id: string;
    processed: number;
    total: number;
    complete: boolean;
  } | null;
  last_capture?: {
    retrieved_at: string;
    sources_received: number;
    sources_saved: number;
    excerpt_bytes: number;
    manifest_bytes: number;
    object_bytes: number;
    new_bytes: number;
  } | null;
};
export type ContextCapture = CitationSource & {
  capture_id: string;
  content: string;
  content_hash: string;
  manifest_hash: string;
  content_kind: string;
  retrieved_at: string;
  published_at: string | null;
  modified_at: string | null;
  completeness: { maestro_truncated: boolean; full_page: false };
  archive_status: "saved";
};
export type ContextSearchInput = {
  query: string;
  limit?: number;
  domain?: string;
  retrieved_from?: string;
  retrieved_to?: string;
  allow_stale?: boolean;
};
export type ContextSearchResult = {
  query: string;
  at: string | null;
  sources: ContextCapture[];
  from_cache: true;
  stale: boolean;
  retrieval: "keyword";
  search_limited?: boolean;
};
export const searchContextArchive = (input: ContextSearchInput) =>
  request<ContextSearchResult>("/context/search", "POST", input);
export const loadContextArchive = () =>
  request<ContextArchiveStatus>("/context");
export const saveContextArchive = (config: ContextArchiveConfig) =>
  request<ContextArchiveStatus>("/context", "PUT", config);
export async function rebuildContextArchive(
  onProgress?: (status: ContextArchiveStatus) => boolean | void,
) {
  let previous:
    NonNullable<ContextArchiveStatus["rebuild_progress"]> | undefined;
  for (;;) {
    const status = await request<ContextArchiveStatus>(
      "/context/rebuild",
      "POST",
      {
        limit: 1000,
        ...(previous ? { continuation: previous.id } : {}),
      },
    );
    const progress = status.rebuild_progress;
    if (!progress) {
      if (previous)
        throw new Error(
          "Source index rebuild progress changed. Refresh the archive status and retry.",
        );
      onProgress?.(status);
      return status;
    }
    if (
      typeof progress.id !== "string" ||
      !/^[a-f0-9]{32}$/.test(progress.id) ||
      !Number.isInteger(progress.processed) ||
      !Number.isInteger(progress.total) ||
      progress.processed < 0 ||
      progress.total < 0 ||
      progress.total > 200000 ||
      progress.processed > progress.total ||
      typeof progress.complete !== "boolean" ||
      progress.complete !== (progress.processed === progress.total) ||
      (!progress.complete && progress.processed === 0) ||
      (previous &&
        (progress.id !== previous.id ||
          progress.total !== previous.total ||
          progress.processed <= previous.processed))
    )
      throw new Error(
        "Source index rebuild did not make valid progress. Refresh the archive status and retry.",
      );
    const proceed = onProgress?.(status);
    if (progress.complete || proceed === false) return status;
    previous = progress;
  }
}
export const loadContextCapture = (
  id: string,
  expectedHash?: string,
  expectedManifestHash?: string,
) => {
  const parameters = new URLSearchParams();
  if (expectedHash !== undefined) parameters.set("expected_hash", expectedHash);
  if (expectedManifestHash !== undefined)
    parameters.set("expected_manifest_hash", expectedManifestHash);
  const query = parameters.toString();
  return request<ContextCapture>(
    `/context/sources/${encodeURIComponent(id)}${query ? `?${query}` : ""}`,
  );
};
export const deleteContextCapture = (id: string) =>
  request<ContextArchiveStatus>(
    `/context/sources/${encodeURIComponent(id)}`,
    "DELETE",
  );

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
  auto_create_tasks?: boolean;
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
  tasks?: Task[];
  usage?: Workspace["usage"];
  next_reflection_at?: string | null;
};
export type ReflectionJournalEntry = {
  id: string;
  created_at: string;
  kind: "curation" | "reflection";
  assessment_basis?: "capabilities_and_practices";
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
  tasks_created?: {
    id: string;
    title: string;
    suggested_assignee: "user" | "maestro";
  }[];
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
