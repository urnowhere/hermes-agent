import { useI18n } from "@/i18n/context";
import type { Locale } from "@/i18n/types";

/**
 * Compact language toggle — cycles through English → French → Chinese.
 * Persists choice to localStorage.
 */
const CYCLE: Record<Locale, Locale> = {
  en: "fr",
  fr: "zh",
  zh: "en",
};

const FLAGS: Record<Locale, string> = {
  en: "🇬🇧",
  fr: "🇫🇷",
  zh: "🇨🇳",
};

const LABELS: Record<Locale, string> = {
  en: "EN",
  fr: "FR",
  zh: "中文",
};

export function LanguageSwitcher() {
  const { locale, setLocale, t } = useI18n();

  const next = CYCLE[locale];
  const toggle = () => setLocale(next);

  return (
    <button
      type="button"
      onClick={toggle}
      className="group relative inline-flex items-center gap-1.5 px-2 py-1 text-xs text-muted-foreground hover:text-foreground transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
      title={t.language.switchTo}
      aria-label={t.language.switchTo}
    >
      {/* Show the *next* language's flag as the clickable target */}
      <span className="text-base leading-none">{FLAGS[next]}</span>
      <span className="hidden sm:inline font-display tracking-wide uppercase text-[0.65rem]">
        {LABELS[next]}
      </span>
    </button>
  );
}
