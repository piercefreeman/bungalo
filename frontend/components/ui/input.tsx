"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

const Input = React.forwardRef<
  HTMLInputElement,
  React.InputHTMLAttributes<HTMLInputElement>
>(({ className, ...props }, ref) => {
  return (
    <input
      ref={ref}
      className={cn(
        "flex h-11 w-full rounded-full border border-foreground/20 bg-white px-4 text-sm shadow-sm transition focus:border-foreground/60 focus:outline-none focus:ring-2 focus:ring-foreground/20",
        className,
      )}
      {...props}
    />
  );
});
Input.displayName = "Input";

export { Input };
