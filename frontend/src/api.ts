export type Task = {
  id: string;
  title: string;
  details: string;
  priority: "normal" | "high";
  done: boolean;
  agent: string;
  created_at: string;
};
export type Memory = { id: string; text: string; category: string };
export type Run = {
  id: string;
  title: string;
  agent: string;
  cost: number;
  input_tokens: number;
  output_tokens: number;
  created_at: string;
  steps: { title: string; detail: string }[];
};
export type Message = { id: string; role: "user" | "assistant"; text: string };
export type Limits = {
  run_usd: number;
  daily_usd: number;
  monthly_usd: number;
  max_tokens: number;
  max_refinements: number;
  max_minutes: number;
};
export type Workspace = {
  tasks: Task[];
  memories: Memory[];
  runs: Run[];
  messages: Message[];
  limits: Limits;
  usage: {
    today_usd: number;
    month_usd: number;
    input_tokens: number;
    output_tokens: number;
    calls: number;
    reserved_usd: number;
  };
  capabilities: {
    mode: "preview";
    live_ai: false;
    github_pr: false;
    secure_credentials: false;
  };
};

let csrf = "";
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
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export async function loadWorkspace() {
  const session = await request<{ csrf: string }>("/session");
  csrf = session.csrf;
  return request<Workspace>("/workspace");
}
export const addTask = (title: string, details: string, priority: string) =>
  request<Workspace>("/tasks", "POST", { title, details, priority });
export const toggleTask = (id: string, done: boolean) =>
  request<Workspace>(`/tasks/${id}`, "PATCH", { done });
export const deleteTask = (id: string) =>
  request<Workspace>(`/tasks/${id}`, "DELETE");
export const previewRun = (id: string) =>
  request<Workspace>(`/tasks/${id}/preview`, "POST", {});
export const sendMessage = (text: string) =>
  request<Workspace>("/chat", "POST", { text });
export const saveLimits = (limits: Limits) =>
  request<Workspace>("/limits", "PUT", limits);
export const addMemory = (text: string) =>
  request<Workspace>("/memory", "POST", { text });
export const deleteMemory = (id: string) =>
  request<Workspace>(`/memory/${id}`, "DELETE");

export type ReflectionConfig = {
  enabled: boolean;
  interval_minutes: number;
  cycle_usd: number;
  daily_usd: number;
  max_passes: number;
  model: string;
  input_usd_per_million: number;
  output_usd_per_million: number;
  pricing_verified: boolean;
};
export type ReflectionStatus = {
  config: ReflectionConfig;
  credentials_present: boolean;
  state: string;
  next_due: string | null;
  uncertain_charges: { id: string; at: string; reserved_usd: number }[];
  usage: {
    today_usd: number;
    month_usd: number;
    tokens: number;
    reserved_usd: number;
    unresolved: boolean;
  };
  evidence: { id: string; text: string; share: boolean; at: string }[];
  jobs: {
    id: string;
    status: string;
    reason: string;
    cost: number;
    tokens: number;
    at: string;
    events: { role: string; pass: number; cost: number }[];
    draft: {
      summary: string;
      lesson: string;
      critique: string;
      approved: boolean;
    } | null;
  }[];
};
export const loadReflection = () => request<ReflectionStatus>("/reflection");
export const saveReflection = (config: ReflectionConfig) =>
  request<ReflectionStatus>("/reflection/config", "PUT", config);
export const addFeedback = (text: string, share: boolean) =>
  request<ReflectionStatus>("/reflection/feedback", "POST", { text, share });
export const runReflection = () =>
  request<ReflectionStatus>("/reflection/run", "POST", {});
export const reviewReflection = (id: string, accept: boolean) =>
  request<ReflectionStatus>(`/reflection/jobs/${id}/review`, "POST", {
    accept,
  });
export const clearReflection = () =>
  request<ReflectionStatus>("/reflection/history", "DELETE");
export const reconcileCharge = (id: string, billed_usd: number) =>
  request<ReflectionStatus>(`/reflection/charges/${id}/reconcile`, "POST", {
    billed_usd,
  });
