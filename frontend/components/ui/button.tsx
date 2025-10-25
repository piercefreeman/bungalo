"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "solid" | "outline";
}

const styles: Record<string, string> = {
  solid:
    "bg-foreground text-white hover:bg-foreground/90 focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-foreground/70",
  outline:
    "border border-foreground/30 text-foreground hover:border-foreground focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-foreground/30",
};

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = "solid", ...props }, ref) => {
    return (
      <button
        ref={ref}
        className={cn(
          "inline-flex h-10 items-center justify-center rounded-full px-5 text-sm font-medium transition focus-visible:outline-none disabled:opacity-50",
          styles[variant],
          className,
        )}
        {...props}
      />
    );
  },
);
Button.displayName = "Button";

export { Button };
