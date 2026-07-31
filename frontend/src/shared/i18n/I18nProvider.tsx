import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  browserLocaleStorage,
  browserNavigatorLanguage,
  persistLocale,
  resolveInitialLocale,
  type LocaleStorage,
} from "./locale";
import {
  formatDateForLocale,
  formatNumberForLocale,
  translate,
} from "./runtime";
import type {
  SupportedLocale,
  TranslationFunction,
} from "./types";

export interface I18nController {
  locale: SupportedLocale;
  setLocale(locale: SupportedLocale): void;
  t: TranslationFunction;
  formatNumber(value: number | bigint, options?: Intl.NumberFormatOptions): string;
  formatDate(value: Date | number, options?: Intl.DateTimeFormatOptions): string;
}

export interface I18nProviderProps {
  children: ReactNode;
  storage?: LocaleStorage | null;
  navigatorLanguage?: string | null;
}

const I18nContext = createContext<I18nController | null>(null);

export function I18nProvider({
  children,
  storage,
  navigatorLanguage,
}: I18nProviderProps) {
  const resolvedStorage = storage === undefined ? browserLocaleStorage() : storage;
  const resolvedNavigatorLanguage = navigatorLanguage === undefined
    ? browserNavigatorLanguage()
    : navigatorLanguage;
  const [locale, setLocaleState] = useState<SupportedLocale>(() =>
    resolveInitialLocale({
      storage: resolvedStorage,
      navigatorLanguage: resolvedNavigatorLanguage,
    }));

  const setLocale = useCallback((nextLocale: SupportedLocale) => {
    setLocaleState(nextLocale);
    persistLocale(nextLocale, resolvedStorage);
  }, [resolvedStorage]);
  const t = useCallback<TranslationFunction>(
    (key, params) => translate(locale, key, params),
    [locale],
  );
  const formatNumber = useCallback(
    (value: number | bigint, options?: Intl.NumberFormatOptions) =>
      formatNumberForLocale(locale, value, options),
    [locale],
  );
  const formatDate = useCallback(
    (value: Date | number, options?: Intl.DateTimeFormatOptions) =>
      formatDateForLocale(locale, value, options),
    [locale],
  );

  useEffect(() => {
    if (typeof document !== "undefined") document.documentElement.lang = locale;
  }, [locale]);

  const controller = useMemo<I18nController>(() => ({
    locale,
    setLocale,
    t,
    formatNumber,
    formatDate,
  }), [formatDate, formatNumber, locale, setLocale, t]);

  return <I18nContext.Provider value={controller}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nController {
  const context = useContext(I18nContext);
  if (context === null) throw new Error("useI18n must be used within I18nProvider");
  return context;
}

export function useOptionalI18n(): I18nController | null {
  return useContext(I18nContext);
}
