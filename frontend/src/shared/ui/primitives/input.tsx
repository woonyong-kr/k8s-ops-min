import { forwardRef, type ComponentProps } from "react";
import { cn } from "@/shared/lib/cn";

export type InputProps = ComponentProps<"input"> & { "data-slot"?: never };

const Input = forwardRef<HTMLInputElement, InputProps>(function Input({
  autoCapitalize = "off",
  autoCorrect = "off",
  className,
  type,
  ...props
}, ref) {
  return (
    <input
      {...props}
      autoCapitalize={autoCapitalize}
      autoCorrect={autoCorrect}
      className={cn(
        "h-8 w-full min-w-0 rounded-lg border border-input bg-transparent px-2.5 py-1 text-base outline-none transition-colors placeholder:text-muted-foreground focus-visible:border-action focus-visible:ring-3 focus-visible:ring-focus-ring disabled:pointer-events-none disabled:cursor-not-allowed disabled:border-border disabled:bg-disabled-background disabled:text-disabled-foreground disabled:opacity-100 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 motion-reduce:transition-none md:text-sm dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40",
        className,
      )}
      data-slot="input"
      ref={ref}
      type={type}
    />
  );
});

export { Input };
