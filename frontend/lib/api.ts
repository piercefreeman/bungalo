export interface ServiceStatus {
  name: string;
  state: string;
  detail?: string | null;
  updated_at: string;
  next_run_at?: string | null;
  last_run_at?: string | null;
}

export interface TaskState {
  id: string;
  title: string;
  prompt: string;
  status: string;
  value?: string | null;
  error?: string | null;
  metadata?: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  url: string;
}

export interface AppState {
  started_at: string;
  services: ServiceStatus[];
  tasks: TaskState[];
}

function resolveApiBase(): string {
  const configured = process.env.NEXT_PUBLIC_API_BASE?.replace(/\/$/, "");
  if (configured) {
    return configured;
  }

  if (typeof window !== "undefined") {
    const { protocol, hostname } = window.location;
    const host = hostname.includes(":") ? `[${hostname}]` : hostname;
    return `${protocol}//${host}:5006`;
  }

  return "http://127.0.0.1:5006";
}

const API_BASE = resolveApiBase();

export async function loadState(): Promise<AppState> {
  const res = await fetch(`${API_BASE}/api/state`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error("Failed to load application state");
  }
  return res.json();
}

export async function loadTask(taskId: string): Promise<TaskState> {
  const res = await fetch(`${API_BASE}/api/tasks/${taskId}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error("Task not found");
  }
  return res.json();
}

export async function submitTask(taskId: string, value: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/tasks/${taskId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ value }),
  });
  if (!res.ok) {
    throw new Error("Failed to submit task");
  }
}
