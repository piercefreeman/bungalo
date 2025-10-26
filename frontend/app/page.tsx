"use client";

import { useCallback, useEffect, useState } from "react";

import { loadState, type AppState, type ServiceStatus } from "@/lib/api";
import { TaskCard } from "@/components/task-card";
import { StatusBadge } from "@/components/status-badge";
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
              <TableHead>Next Run</TableHead>
              <TableHead>Last Run</TableHead>
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
                  {formatDateTime(service.next_run_at)}
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {formatDateTime(service.last_run_at)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>
    </div>
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
    <main className="mx-auto flex min-h-screen w-full flex-col">
      <div className="mx-auto w-full max-w-7xl px-6 py-16 space-y-16">
        <header className="space-y-6">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
            <div className="space-y-3">
              <h1 className="text-5xl font-bold tracking-tighter text-foreground">
                Bungalo Operations
              </h1>
              <p className="max-w-2xl text-lg text-foreground/60 leading-relaxed">
                Stay in sync with the systems that keep your library healthy. Every
                task, service, and sync at a glance.
              </p>
            </div>
            {data?.started_at ? (
              <p className="text-xs uppercase tracking-widest text-foreground/40 mt-2">
                App manager started {formatDateTime(data.started_at)}
              </p>
            ) : null}
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

        <section className="space-y-8">
          <Card>
            <CardHeader>
              <CardTitle>Active Sync</CardTitle>
              <CardDescription>
                Live view of syncing processes currently underway.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {activeServices.length ? (
                activeServices.map((service) => (
                  <div
                    key={service.name}
                    className="flex flex-col gap-2 rounded-lg border border-border bg-muted/20 px-6 py-5 transition-shadow hover:shadow-sm"
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-xl font-semibold tracking-tight">
                        {titleCase(service.name)}
                      </span>
                      <StatusBadge state={service.state} />
                    </div>
                    <span className="text-sm text-foreground/60 leading-relaxed">
                      {service.detail}
                    </span>
                  </div>
                ))
              ) : (
                <div className="rounded-lg border border-dashed border-border bg-muted/10 px-6 py-12 text-center">
                  <p className="text-foreground/50">
                    No active syncs right now. Everything is calm.
                  </p>
                </div>
              )}
            </CardContent>
          </Card>
        </section>
      </div>

      <div className="w-full border-t border-border bg-card/50 backdrop-blur-sm">
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
