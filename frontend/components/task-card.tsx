"use client";

import { useState } from "react";

import { submitTask, type TaskState } from "@/lib/api";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { StatusBadge } from "@/components/status-badge";

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
        <div className="flex items-center justify-between">
          <CardTitle className="text-2xl">{task.title}</CardTitle>
          <StatusBadge state={task.status} />
        </div>
        <CardDescription className="leading-relaxed">
          {task.prompt}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        {task.error ? (
          <p className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-red-700">
            {task.error}
          </p>
        ) : null}

        {isEditable ? (
          <form onSubmit={handleSubmit} className="space-y-3">
            <Input
              autoFocus
              placeholder="Enter response…"
              value={value}
              onChange={(event) => setValue(event.target.value)}
              maxLength={64}
              disabled={submitting}
            />
            <Button type="submit" className="w-full" disabled={submitting}>
              {submitting ? "Submitting…" : "Submit"}
            </Button>
          </form>
        ) : (
          <div className="rounded-xl border border-foreground/10 bg-muted px-4 py-3">
            {task.status === "completed" ? (
              <p className="font-medium">Thank you! This task is complete.</p>
            ) : (
              <p className="text-foreground/70">
                Awaiting processing. You&apos;ll be prompted again if more
                information is needed.
              </p>
            )}
          </div>
        )}

        {statusMessage ? (
          <p className="text-xs uppercase tracking-wide text-foreground/60">
            {statusMessage}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
