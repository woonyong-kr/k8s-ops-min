import type { SupportedLocale } from "./types";

export const PRODUCT_LOCALE_STORAGE_KEY = "opsia.locale";
export const DEFAULT_LOCALE: SupportedLocale = "en";
export const SUPPORTED_LOCALES = ["en", "ko"] as const satisfies readonly SupportedLocale[];

export interface LocaleStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export interface ResolveLocaleOptions {
  storage?: LocaleStorage | null;
  navigatorLanguage?: string | null;
}

export function isSupportedLocale(value: unknown): value is SupportedLocale {
  return value === "en" || value === "ko";
}

export function detectNavigatorLocale(language: string | null | undefined): SupportedLocale {
  if (typeof language !== "string") return DEFAULT_LOCALE;
  const normalized = language.trim().toLowerCase().replace(/_/g, "-");
  return normalized === "ko" || normalized.startsWith("ko-") ? "ko" : DEFAULT_LOCALE;
}

export function readPersistedLocale(storage: LocaleStorage | null | undefined): SupportedLocale | null {
  if (storage === null || storage === undefined) return null;
  try {
    const value = storage.getItem(PRODUCT_LOCALE_STORAGE_KEY);
    return isSupportedLocale(value) ? value : null;
  } catch {
    return null;
  }
}

export function persistLocale(
  locale: SupportedLocale,
  storage: LocaleStorage | null | undefined,
): void {
  if (storage === null || storage === undefined) return;
  try {
    storage.setItem(PRODUCT_LOCALE_STORAGE_KEY, locale);
  } catch {
    // Storage can be unavailable in private/sandboxed browser contexts.
  }
}

export function resolveInitialLocale(options: ResolveLocaleOptions = {}): SupportedLocale {
  return readPersistedLocale(options.storage) ??
    detectNavigatorLocale(options.navigatorLanguage);
}

export function browserLocaleStorage(): LocaleStorage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function browserNavigatorLanguage(): string | null {
  return typeof navigator === "undefined" ? null : navigator.language;
}
