"use client";

import { useState } from "react";

import { submitTask, type TaskState } from "@/lib/api";
import { StatusBadge } from "@/components/status-badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

interface TaskCardProps {
  task: TaskState;
  onSubmitted?: () => void;
}

export function TaskCard({ task, onSubmitted }: TaskCardProps) {
  const [value, setValue] = useState("");
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const isEditable = task.status === "pending";

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!value.trim()) {
      setStatusMessage("Please enter a value before submitting.");
      return;
    }
    try {
      setSubmitting(true);
      setStatusMessage(null);
      await submitTask(task.id, value.trim());
      setStatusMessage("Code submitted. Awaiting confirmation...");
      setValue("");
      onSubmitted?.();
    } catch (error) {
      setStatusMessage(
        error instanceof Error ? error.message : "Unable to send task response.",
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Card className="h-full">
      <CardHeader className="space-y-3">
        <div className="flex items-center justify-between gap-4">
          <CardTitle>{task.title}</CardTitle>
          <StatusBadge state={task.status} />
        </div>
        <CardDescription>
          {task.prompt}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {task.error ? (
          <div className="rounded-lg border border-destructive/50 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {task.error}
          </div>
        ) : null}

        {isEditable ? (
          <form onSubmit={handleSubmit} className="space-y-4">
            <Input
              autoFocus
              placeholder="Enter response…"
              value={value}
              onChange={(event) => setValue(event.target.value)}
              maxLength={64}
              disabled={submitting}
            />
            <Button type="submit" size="lg" className="w-full" disabled={submitting}>
              {submitting ? "Submitting…" : "Submit"}
            </Button>
          </form>
        ) : (
          <div className="rounded-lg border border-border bg-muted/30 px-5 py-4">
            {task.status === "completed" ? (
              <p className="text-sm font-medium">Thank you! This task is complete.</p>
            ) : (
              <p className="text-sm text-foreground/60">
                Awaiting processing. You&apos;ll be prompted again if more
                information is needed.
              </p>
            )}
          </div>
        )}

        {statusMessage ? (
          <p className="text-xs uppercase tracking-wider text-foreground/50 font-medium">
            {statusMessage}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
