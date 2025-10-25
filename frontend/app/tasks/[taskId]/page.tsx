"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { loadTask, type TaskState } from "@/lib/api";
import { TaskCard } from "@/components/task-card";
import { Button } from "@/components/ui/button";

export default function TaskPage({
  params,
}: {
  params: { taskId: string };
}) {
  const router = useRouter();
  const [task, setTask] = useState<TaskState | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const doLoad = async () => {
      try {
        const next = await loadTask(params.taskId);
        setTask(next);
        setError(null);
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "We couldn't load this task.",
        );
      }
    };

    doLoad();
  }, [params.taskId]);

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-3xl flex-col gap-8 px-6 py-12">
      <div className="flex items-center justify-between">
        <Button variant="outline" onClick={() => router.back()}>
          Back
        </Button>
        <Link
          href="/"
          className="text-sm uppercase tracking-widest text-foreground/40"
        >
          Bungalo Dashboard
        </Link>
      </div>

      {error ? (
        <div className="rounded-2xl border border-red-200 bg-red-50 px-5 py-6 text-red-700">
          {error}
        </div>
      ) : null}

      {task ? (
        <TaskCard task={task} onSubmitted={async () => setTask(await loadTask(task.id))} />
      ) : null}
    </main>
  );
}
