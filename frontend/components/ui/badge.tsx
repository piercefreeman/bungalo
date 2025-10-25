"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

const badgeStyles: Record<string, string> = {
  neutral: "bg-foreground/5 text-foreground border border-foreground/10",
  success: "bg-foreground text-white border border-foreground",
  danger: "bg-red-600 text-white border border-red-700",
  warning: "bg-amber-200 text-amber-900 border border-amber-300",
};

export interface BadgeProps extends React.HTMLAttributes<HTMLDivElement> {
  tone?: keyof typeof badgeStyles;
}

const Badge = React.forwardRef<HTMLDivElement, BadgeProps>(
  ({ className, tone = "neutral", ...props }, ref) => (
    <div
      ref={ref}
      className={cn(
        "inline-flex items-center rounded-full px-3 py-1 text-xs font-semibold uppercase tracking-wide",
        badgeStyles[tone],
        className,
      )}
      {...props}
    />
  ),
);
Badge.displayName = "Badge";

export { Badge };
