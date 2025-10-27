"use client";

import { useCallback, useEffect, useState } from "react";

import {
  loadState,
  type AppState,
  type ServiceStatus,
  type SystemMetrics,
  type SystemMetricsError,
} from "@/lib/api";
import { TaskCard } from "@/components/task-card";
import { StatusBadge } from "@/components/status-badge";
import { ThemeToggle } from "@/components/theme-toggle";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

const POLL_INTERVAL = 5000;

function formatDateTime(value?: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return date.toLocaleString(undefined, {
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    month: "short",
    day: "numeric",
  });
}

function formatRelativeTime(value?: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  const now = new Date();
  const diffMs = date.getTime() - now.getTime();
  const diffMins = Math.round(diffMs / 60000);

  if (Math.abs(diffMs) < 60000) {
    return diffMs < 0 ? "just now" : "in under a minute";
  }
  
  if (diffMins < 0) {
    const absMins = Math.abs(diffMins);
    if (absMins < 60) return `${absMins} min ago`;
    const absHours = Math.floor(absMins / 60);
    if (absHours < 24) return `${absHours}h ago`;
    const absDays = Math.floor(absHours / 24);
    return `${absDays}d ago`;
  }
  
  if (diffMins < 60) return `in ${diffMins} min`;
  const hours = Math.floor(diffMins / 60);
  if (hours < 24) return `in ${hours}h`;
  const days = Math.floor(hours / 24);
  return `in ${days}d`;
}

function titleCase(name: string) {
  return name
    .replace(/_/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function formatBytes(value: number) {
  if (!Number.isFinite(value) || value < 0) return "—";
  const units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"];
  let size = value;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  const precision = unitIndex === 0 ? 0 : 1;
  return `${size.toFixed(precision)} ${units[unitIndex]}`;
}

function isMetricsError(
  metrics: SystemMetrics | SystemMetricsError,
): metrics is SystemMetricsError {
  return "error" in metrics;
}

function ServicesTable({ services }: { services: ServiceStatus[] }) {
  if (!services.length) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Services</CardTitle>
          <CardDescription>No services have reported status yet.</CardDescription>
        </CardHeader>
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h2 className="text-4xl font-bold tracking-tighter">Services</h2>
        <p className="text-base text-foreground/60">
          Monitor the subsystems that keep Bungalo running.
        </p>
      </div>
      <Card className="overflow-hidden">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Service</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Detail</TableHead>
              <TableHead>Last Run</TableHead>
              <TableHead>Next Run</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {services.map((service) => (
              <TableRow key={service.name}>
                <TableCell className="font-semibold">
                  {titleCase(service.name)}
                </TableCell>
                <TableCell>
                  <StatusBadge state={service.state} />
                </TableCell>
                <TableCell className="max-w-md text-muted-foreground">
                  {service.detail || "—"}
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {formatDateTime(service.last_run_at)}
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {formatRelativeTime(service.next_run_at)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>
    </div>
  );
}

function SystemMetricsCard({
  metrics,
}: {
  metrics?: SystemMetrics | SystemMetricsError | null;
}) {
  if (!metrics) return null;
  if (isMetricsError(metrics)) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>System Health</CardTitle>
          <CardDescription>Unable to load current metrics.</CardDescription>
        </CardHeader>
        <CardContent className="text-sm text-destructive">
          {metrics.error}
        </CardContent>
      </Card>
    );
  }

  const memoryUsedPercent = Math.min(metrics.memory.percent, 100);
  const swapUsedPercent =
    metrics.swap.total > 0 ? Math.min(metrics.swap.percent, 100) : 0;

  return (
    <Card className="overflow-hidden">
      <CardHeader>
        <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
          <div>
            <CardTitle>System Health</CardTitle>
            <CardDescription>
              Live snapshot of CPU, memory, and active processes.
            </CardDescription>
          </div>
          <p className="text-xs uppercase tracking-wider text-foreground/40">
            Updated {formatDateTime(metrics.collected_at)}
          </p>
        </div>
      </CardHeader>
      <CardContent>
        <div className="grid gap-6 md:grid-cols-3">
          <div className="space-y-4">
            <div>
              <p className="text-sm font-medium uppercase tracking-wide text-muted-foreground">
                CPU
              </p>
              <p className="text-3xl font-semibold text-foreground">
                {metrics.cpu.average_percent.toFixed(1)}%
              </p>
              {metrics.cpu.load_average ? (
                <p className="text-xs text-muted-foreground">
                  Load avg {metrics.cpu.load_average["1m"].toFixed(2)} /{" "}
                  {metrics.cpu.load_average["5m"].toFixed(2)} /{" "}
                  {metrics.cpu.load_average["15m"].toFixed(2)}
                </p>
              ) : null}
            </div>
            <div className="space-y-2">
              {metrics.cpu.cores.map((core) => (
                <div key={core.index} className="space-y-1">
                  <div className="flex items-center justify-between text-xs text-muted-foreground">
                    <span>Core {core.index + 1}</span>
                    <span>{core.percent.toFixed(0)}%</span>
                  </div>
                  <div className="h-2 w-full rounded-full bg-muted">
                    <div
                      className="h-2 rounded-full bg-primary transition-all"
                      style={{ width: `${Math.min(core.percent, 100)}%` }}
                    />
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="space-y-5">
            <div className="space-y-2">
              <p className="text-sm font-medium uppercase tracking-wide text-muted-foreground">
                Memory
              </p>
              <p className="text-lg font-semibold text-foreground">
                {formatBytes(metrics.memory.used)} /{" "}
                {formatBytes(metrics.memory.total)}
              </p>
              <p className="text-xs text-muted-foreground">
                {memoryUsedPercent.toFixed(1)}% used •{" "}
                {formatBytes(metrics.memory.available)} available
              </p>
              <div className="h-2 w-full rounded-full bg-muted">
                <div
                  className="h-2 rounded-full bg-emerald-500 transition-all"
                  style={{ width: `${memoryUsedPercent}%` }}
                />
              </div>
            </div>

            <div className="space-y-2">
              <p className="text-sm font-medium uppercase tracking-wide text-muted-foreground">
                Swap
              </p>
              {metrics.swap.total > 0 ? (
                <>
                  <p className="text-lg font-semibold text-foreground">
                    {formatBytes(metrics.swap.used)} /{" "}
                    {formatBytes(metrics.swap.total)}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {swapUsedPercent.toFixed(1)}% used •{" "}
                    {formatBytes(metrics.swap.free)} free
                  </p>
                  <div className="h-2 w-full rounded-full bg-muted">
                    <div
                      className="h-2 rounded-full bg-orange-500 transition-all"
                      style={{ width: `${swapUsedPercent}%` }}
                    />
                  </div>
                </>
              ) : (
                <p className="text-xs text-muted-foreground">
                  Swap not configured.
                </p>
              )}
            </div>
          </div>

          <div className="space-y-3">
            <p className="text-sm font-medium uppercase tracking-wide text-muted-foreground">
              Top Processes
            </p>
            {metrics.processes.length ? (
              <div className="space-y-3">
                {metrics.processes.map((process) => (
                  <div
                    key={process.pid}
                    className="space-y-1 rounded-lg border border-border/40 p-3"
                  >
                    <div className="flex items-center justify-between text-sm font-medium text-foreground">
                      <span className="truncate pr-2">
                        {process.name || `PID ${process.pid}`}
                      </span>
                      <span>{process.cpu_percent.toFixed(1)}%</span>
                    </div>
                    <p className="text-xs text-muted-foreground">
                      PID {process.pid} • {process.memory_percent.toFixed(1)}% RAM
                    </p>
                    {process.command ? (
                      <p className="text-xs text-muted-foreground truncate">
                        {process.command}
                      </p>
                    ) : null}
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">
                No active processes to display.
              </p>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

export function DashboardClient({ host }: { host: string }) {
  const [data, setData] = useState<AppState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const next = await loadState();
      setData(next);
      setError(null);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to contact Bungalo backend.",
      );
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, POLL_INTERVAL);
    return () => clearInterval(interval);
  }, [refresh]);

  const pendingTasks = data?.tasks.filter((task) =>
    ["pending", "submitted"].includes(task.status),
  );
  const completedTasks = data?.tasks.filter((task) => task.status === "completed");
  const activeServices =
    data?.services.filter((service) => service.state.toLowerCase() === "running") ?? [];

  // Extract hostname from host (remove port if present)
  const hostname = host.split(':')[0];

  return (
    <main className="mx-auto flex min-h-screen w-full flex-col">
      <div className="mx-auto w-full max-w-7xl px-6 py-16 space-y-16">
        <header className="space-y-6">
          <div className="flex items-start justify-between gap-4">
            <div className="flex-1 space-y-3">
              <h1 className="text-5xl font-bold tracking-tighter text-foreground">
                Bungalo Operations
              </h1>
              <p className="max-w-2xl text-lg text-foreground/60 leading-relaxed">
                Stay in sync with the systems that keep your library healthy. Every
                task, service, and sync at a glance.
              </p>
            </div>
            <div className="flex flex-col items-end gap-3">
              <ThemeToggle />
              {data?.started_at ? (
                <p className="text-xs uppercase tracking-widest text-foreground/40">
                  App manager started {formatDateTime(data.started_at)}
                </p>
              ) : null}
            </div>
          </div>
          {error ? (
            <div className="rounded-lg border border-destructive/50 bg-destructive/10 px-6 py-4 text-sm text-destructive">
              {error}
            </div>
          ) : null}
        </header>

        {isLoading ? (
          <Card>
            <CardHeader>
              <CardTitle>Loading status…</CardTitle>
              <CardDescription>Breathe in, breathe out.</CardDescription>
            </CardHeader>
          </Card>
        ) : null}

        <section className="grid gap-4 md:grid-cols-2">
          <Card className="transition-all hover:shadow-lg cursor-pointer">
            <a href="/" className="block">
              <CardHeader>
                <div className="flex items-center justify-between">
                  <CardTitle className="text-xl">Bungalo Service</CardTitle>
                  <StatusBadge state="running" />
                </div>
                <CardDescription>
                  Dashboard and operations control
                </CardDescription>
              </CardHeader>
            </a>
          </Card>

          <Card className="transition-all hover:shadow-lg cursor-pointer">
            <a 
              href={`http://${hostname}:8096`}
              target="_blank"
              rel="noopener noreferrer"
              className="block"
            >
              <CardHeader>
                <div className="flex items-center justify-between">
                  <CardTitle className="text-xl">Media Server</CardTitle>
                  <svg className="h-4 w-4 text-muted-foreground" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                  </svg>
                </div>
                <CardDescription>
                  Access your media library
                </CardDescription>
              </CardHeader>
            </a>
          </Card>
        </section>

        <SystemMetricsCard metrics={data?.system ?? null} />
      </div>

      <div className="w-full border-t border-border backdrop-blur-sm">
        <div className="mx-auto w-full max-w-7xl px-6 py-12">
          <ServicesTable services={data?.services ?? []} />
        </div>
      </div>

      <div className="mx-auto w-full max-w-7xl px-6 py-16 space-y-12">
        <section className="space-y-8">
          <div className="flex flex-col gap-2">
            <h2 className="text-4xl font-bold tracking-tighter">Tasks</h2>
            <p className="text-base text-foreground/60">
              We&apos;ll ask for input here when Bungalo needs a human touch.
            </p>
          </div>

          {pendingTasks && pendingTasks.length === 0 ? (
            <Card>
              <CardHeader>
                <CardTitle>No tasks on deck</CardTitle>
                <CardDescription>
                  We&apos;ll nudge you on Slack when something needs attention.
                </CardDescription>
              </CardHeader>
            </Card>
          ) : (
            <div className="grid gap-6 md:grid-cols-2">
              {pendingTasks?.map((task) => (
                <TaskCard key={task.id} task={task} onSubmitted={refresh} />
              ))}
            </div>
          )}

          {completedTasks && completedTasks.length ? (
            <div className="space-y-6">
              <h3 className="text-xs uppercase tracking-wider text-foreground/40 font-medium">
                Recently completed
              </h3>
              <div className="grid gap-6 md:grid-cols-2">
                {completedTasks.map((task) => (
                  <Card key={task.id} className="bg-muted/30">
                    <CardHeader>
                      <CardTitle className="text-xl">{task.title}</CardTitle>
                      <CardDescription>{task.prompt}</CardDescription>
                    </CardHeader>
                    <CardContent className="text-sm text-foreground/50">
                      Completed {formatDateTime(task.updated_at)}
                    </CardContent>
                  </Card>
                ))}
              </div>
            </div>
          ) : null}
        </section>
      </div>
    </main>
  );
}

