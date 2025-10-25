"use client";

import { useCallback, useEffect, useState } from "react";

import { loadState, type AppState, type ServiceStatus } from "@/lib/api";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { TaskCard } from "@/components/task-card";
import { StatusBadge } from "@/components/status-badge";

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

function titleCase(name: string) {
  return name
    .replace(/_/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
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
    <Card>
      <CardHeader className="pb-3">
        <CardTitle>Services</CardTitle>
        <CardDescription>
          Monitor the subsystems that keep Bungalo running.
        </CardDescription>
      </CardHeader>
      <CardContent className="px-0">
        <Table>
          <TableHead>
            <TableRow>
              <TableHeader className="px-6">Service</TableHeader>
              <TableHeader>Status</TableHeader>
              <TableHeader>Detail</TableHeader>
              <TableHeader>Next Run</TableHeader>
              <TableHeader>Last Run</TableHeader>
            </TableRow>
          </TableHead>
          <TableBody>
            {services.map((service) => (
              <TableRow key={service.name}>
                <TableCell className="px-6 font-medium">
                  {titleCase(service.name)}
                </TableCell>
                <TableCell>
                  <StatusBadge state={service.state} />
                </TableCell>
                <TableCell className="max-w-md text-foreground/70">
                  {service.detail || "—"}
                </TableCell>
                <TableCell>{formatDateTime(service.next_run_at)}</TableCell>
                <TableCell>{formatDateTime(service.last_run_at)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

export default function DashboardPage() {
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

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-6xl flex-col gap-10 px-6 py-12">
      <header className="space-y-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-baseline sm:justify-between">
          <div>
            <p className="text-2xl font-semibold tracking-tight text-foreground">
              Bungalo Operations
            </p>
            <p className="max-w-2xl text-base text-foreground/70">
              Stay in sync with the systems that keep your library healthy. Every
              task, service, and sync at a glance.
            </p>
          </div>
          {data?.started_at ? (
            <p className="text-sm uppercase tracking-widest text-foreground/40">
              App manager started {formatDateTime(data.started_at)}
            </p>
          ) : null}
        </div>
        {error ? (
          <p className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {error}
          </p>
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

      <section className="grid gap-6 md:grid-cols-2">
        <Card className="col-span-1 md:col-span-2">
          <CardHeader className="pb-3">
            <CardTitle>Active Sync</CardTitle>
            <CardDescription>
              Live view of syncing processes currently underway.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4 text-sm">
            {activeServices.length ? (
              activeServices.map((service) => (
                <div
                  key={service.name}
                  className="flex flex-col gap-1 rounded-xl border border-foreground/10 bg-white/70 px-5 py-4 shadow-sm"
                >
                  <div className="flex items-center justify-between">
                    <span className="text-lg font-semibold">
                      {titleCase(service.name)}
                    </span>
                    <StatusBadge state={service.state} />
                  </div>
                  <span className="text-foreground/70">{service.detail}</span>
                </div>
              ))
            ) : (
              <p className="rounded-xl border border-dashed border-foreground/20 px-5 py-6 text-center text-foreground/60">
                No active syncs right now. Everything is calm.
              </p>
            )}
          </CardContent>
        </Card>

        <ServicesTable services={data?.services ?? []} />
      </section>

      <section className="space-y-6">
        <div className="flex flex-col gap-2">
          <h2 className="text-xl font-semibold tracking-tight">Tasks</h2>
          <p className="text-sm text-foreground/70">
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
          <div className="grid gap-4">
            <h3 className="text-sm uppercase tracking-wide text-foreground/40">
              Recently completed
            </h3>
            <div className="grid gap-4 md:grid-cols-2">
              {completedTasks.map((task) => (
                <Card key={task.id} className="border-foreground/10 bg-white/80">
                  <CardHeader className="space-y-2">
                    <CardTitle className="text-lg">{task.title}</CardTitle>
                    <CardDescription>{task.prompt}</CardDescription>
                  </CardHeader>
                  <CardContent className="text-sm text-foreground/60">
                    Completed {formatDateTime(task.updated_at)}
                  </CardContent>
                </Card>
              ))}
            </div>
          </div>
        ) : null}
      </section>
    </main>
  );
}
