import { Button } from "@nous-research/ui/ui/components/button";
import { Typography } from "@/components/NouiTypography";
import { useI18n, type Locale } from "@/i18n";

const LANGUAGES: Array<{
  locale: Locale;
  flag: string;
  label: string;
  shortLabel: string;
}> = [
  { locale: "en", flag: "🇬🇧", label: "English", shortLabel: "EN" },
  { locale: "zh", flag: "🇨🇳", label: "中文", shortLabel: "中文" },
  { locale: "ko", flag: "🇰🇷", label: "한국어", shortLabel: "KO" },
];

/**
 * Compact language toggle — shows the active language and cycles through all
 * available dashboard locales. Persists choice to localStorage.
 */
export function LanguageSwitcher() {
  const { locale, setLocale, t } = useI18n();

  const currentIndex = Math.max(
    0,
    LANGUAGES.findIndex((language) => language.locale === locale),
  );
  const current = LANGUAGES[currentIndex];
  const next = LANGUAGES[(currentIndex + 1) % LANGUAGES.length];
  const switchTitle = t.language.switchTo.replace("{language}", next.label);
  const toggle = () => setLocale(next.locale);

  return (
    <Button
      ghost
      onClick={toggle}
      title={switchTitle}
      aria-label={switchTitle}
      className="px-2 py-1 normal-case tracking-normal font-normal text-xs text-muted-foreground hover:text-foreground"
    >
      <span className="inline-flex items-center gap-1.5">
        <span className="text-base leading-none">
          {current.flag}
        </span>

        <Typography
          mondwest
          className="hidden sm:inline tracking-wide uppercase text-[0.65rem]"
        >
          {current.shortLabel}
        </Typography>
      </span>
    </Button>
  );
}
