import type { ComponentProps } from "react";
import { cn } from "@/shared/lib/cn";

type SkeletonProps = Omit<ComponentProps<"div">, "aria-hidden">;

function Skeleton({ className, ...props }: SkeletonProps) {
  return (
    <div
      {...props}
      aria-hidden="true"
      data-slot="skeleton"
      className={cn(
        "motion-safe:animate-pulse motion-reduce:animate-none rounded-md bg-muted",
        className,
      )}
    />
  );
}

export { Skeleton };
