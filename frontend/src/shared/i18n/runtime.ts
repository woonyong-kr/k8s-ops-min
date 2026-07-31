import { en } from "./en";
import { ko } from "./ko";
import type {
  MessageKey,
  SupportedLocale,
  TranslationParameters,
} from "./types";

const localeTags = {
  en: "en-US",
  ko: "ko-KR",
} satisfies Record<SupportedLocale, string>;

const catalogs = { en, ko } satisfies Record<
  SupportedLocale,
  Readonly<Record<MessageKey, string>>
>;

const placeholderPattern = /\{([A-Za-z][A-Za-z0-9_]*)\}/g;

export function translate(
  locale: SupportedLocale,
  key: MessageKey,
  params: TranslationParameters = {},
): string {
  const template = catalogs[locale][key];
  return template.replace(placeholderPattern, (placeholder, name: string) => {
    if (!Object.prototype.hasOwnProperty.call(params, name)) return placeholder;
    return String(params[name]);
  });
}

export function formatNumberForLocale(
  locale: SupportedLocale,
  value: number | bigint,
  options?: Intl.NumberFormatOptions,
): string {
  return new Intl.NumberFormat(localeTags[locale], options).format(value);
}

export function formatDateForLocale(
  locale: SupportedLocale,
  value: Date | number,
  options?: Intl.DateTimeFormatOptions,
): string {
  return new Intl.DateTimeFormat(localeTags[locale], options).format(value);
}
