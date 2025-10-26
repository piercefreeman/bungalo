"use client";

import { Badge } from "@/components/ui/badge";

const statusVariant: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  running: "default",
  idle: "secondary",
  completed: "default",
  success: "default",
  error: "destructive",
  failed: "destructive",
  warning: "outline",
  pending: "outline",
  submitted: "secondary",
};

export function StatusBadge({ state }: { state: string }) {
  const variant = statusVariant[state.toLowerCase()] ?? "secondary";
  const label = state.replace(/_/g, " ");
  return <Badge variant={variant} className="uppercase">{label}</Badge>;
}
