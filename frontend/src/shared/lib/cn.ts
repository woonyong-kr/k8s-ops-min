import { twMerge } from "tailwind-merge";

type ClassDictionary = Readonly<Record<string, unknown>>;
export type ClassValue =
  | string
  | number
  | bigint
  | boolean
  | null
  | undefined
  | readonly ClassValue[]
  | ClassDictionary
  | ((...args: never[]) => unknown);

export function cn(...inputs: ClassValue[]): string {
  return twMerge(...inputs.flatMap(classNames));
}

function classNames(value: ClassValue): string[] {
  if (typeof value === "string" || typeof value === "number" || typeof value === "bigint") {
    return value ? [String(value)] : [];
  }
  if (Array.isArray(value)) return value.flatMap(classNames);
  if (value !== null && typeof value === "object") {
    return Object.entries(value)
      .filter(([, enabled]) => Boolean(enabled))
      .map(([className]) => className);
  }
  return [];
}

