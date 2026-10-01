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
