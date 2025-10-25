"use client";

import { Badge } from "@/components/ui/badge";

const statusTone: Record<string, "neutral" | "success" | "danger" | "warning"> = {
  running: "neutral",
  idle: "neutral",
  completed: "success",
  success: "success",
  error: "danger",
  failed: "danger",
  warning: "warning",
  pending: "warning",
  submitted: "neutral",
};

export function StatusBadge({ state }: { state: string }) {
  const tone = statusTone[state.toLowerCase()] ?? "neutral";
  const label = state.replace(/_/g, " ");
  return <Badge tone={tone}>{label}</Badge>;
}
