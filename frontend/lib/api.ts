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

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE?.replace(/\/$/, "") || "http://localhost:5006";

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
