import { Loader2Icon } from "lucide-react";
import type { ComponentProps } from "react";
import { DEFAULT_LOCALE, translate, useOptionalI18n } from "../../i18n";
import { cn } from "@/shared/lib/cn";

type SpinnerProps = Omit<ComponentProps<"svg">, "aria-hidden"> & {
  decorative?: boolean;
};

function Spinner({
  "aria-label": ariaLabel,
  className,
  decorative = false,
  role,
  ...props
}: SpinnerProps) {
  const i18n = useOptionalI18n();
  const defaultLabel = i18n?.t("loading.default") ?? translate(DEFAULT_LOCALE, "loading.default");
  return (
    <Loader2Icon
      aria-hidden={decorative || undefined}
      aria-label={decorative ? undefined : (ariaLabel?.trim() || defaultLabel)}
      className={cn("size-4 motion-safe:animate-spin motion-reduce:animate-none", className)}
      data-slot="spinner"
      role={decorative ? undefined : (role ?? "status")}
      {...props}
    />
  );
}

export { Spinner };
