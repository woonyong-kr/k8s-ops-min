import type { ComponentProps } from "react";
import { cn } from "@/shared/lib/cn";

type LabelProps = ComponentProps<"label"> & { "data-slot"?: never };

function Label({ className, ...props }: LabelProps) {
  return (
    <label
      {...props}
      className={cn(
        "flex w-fit items-center gap-2 text-sm font-medium leading-snug peer-disabled:cursor-not-allowed peer-disabled:text-disabled-foreground peer-disabled:opacity-100",
        className,
      )}
      data-slot="label"
    />
  );
}

export { Label };
