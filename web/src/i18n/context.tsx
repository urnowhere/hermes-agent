import { useState, useCallback, type ReactNode } from "react";
import type { Locale, Translations } from "./types";
import { I18nContext, type I18nContextValue } from "./i18n-context";
import { en } from "./en";
import { zh } from "./zh";
import { ko } from "./ko";

const LOCALES: readonly Locale[] = ["en", "zh", "ko"];
const TRANSLATIONS: Record<Locale, Translations> = { en, zh, ko };
const STORAGE_KEY = "hermes-locale";

function isLocale(value: unknown): value is Locale {
  return typeof value === "string" && (LOCALES as readonly string[]).includes(value);
}

function getBrowserLocale(): Locale | null {
  if (typeof navigator === "undefined") return null;

  const languages = [...(navigator.languages ?? [])];
  if (navigator.language) languages.push(navigator.language);

  for (const language of languages) {
    const normalized = language.toLowerCase();
    if (normalized.startsWith("ko")) return "ko";
    if (normalized.startsWith("zh")) return "zh";
    if (normalized.startsWith("en")) return "en";
  }

  return null;
}

function getInitialLocale(): Locale {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (isLocale(stored)) return stored;
  } catch {
    // SSR or privacy mode
  }
  return getBrowserLocale() ?? "en";
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(getInitialLocale);

  const setLocale = useCallback((l: Locale) => {
    setLocaleState(l);
    try {
      localStorage.setItem(STORAGE_KEY, l);
    } catch {
      // ignore
    }
  }, []);

  const value: I18nContextValue = {
    locale,
    setLocale,
    t: TRANSLATIONS[locale],
  };

  return (
    <I18nContext.Provider value={value}>
      {children}
    </I18nContext.Provider>
  );
}
