export type ThemeMode = 'dark' | 'light' | 'system';

export interface ThemeConfig {
  mode: ThemeMode;
  accentColors: { primary: string; secondary: string };
}

export const DEFAULT_THEME: ThemeConfig = {
  mode: 'dark',
  accentColors: { primary: '#818cf8', secondary: '#c084fc' }
};

export const ACCENT_PRESETS = [
  { name: 'Default Indigo', colors: { primary: '#818cf8', secondary: '#c084fc' } },
  { name: 'Crimson', colors: { primary: '#fb7185', secondary: '#f43f5e' } },
  { name: 'Emerald', colors: { primary: '#34d399', secondary: '#10b981' } },
  { name: 'Amber', colors: { primary: '#fbbf24', secondary: '#f59e0b' } },
  { name: 'Sky', colors: { primary: '#38bdf8', secondary: '#0ea5e9' } },
  { name: 'Monochrome', colors: { primary: '#94a3b8', secondary: '#475569' } },
];

const THEME_STORAGE_KEY = 'hermes_web_theme';

export function loadTheme(): ThemeConfig {
  try {
    const raw = localStorage.getItem(THEME_STORAGE_KEY);
    if (raw) return { ...DEFAULT_THEME, ...JSON.parse(raw) };
  } catch (e) {
    // fallback
  }
  return DEFAULT_THEME;
}

export function saveTheme(config: ThemeConfig) {
  localStorage.setItem(THEME_STORAGE_KEY, JSON.stringify(config));
  applyTheme(config);
}

export function applyTheme(config: ThemeConfig) {
  const root = document.documentElement;
  
  // Decide actual light/dark based on system preference if requested
  let effectiveMode = config.mode;
  if (config.mode === 'system') {
    effectiveMode = window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  }

  if (effectiveMode === 'light') {
    root.setAttribute('data-theme', 'light');
  } else {
    root.setAttribute('data-theme', 'dark');
  }

  // Set accents dynamically
  root.style.setProperty('--accent-primary', config.accentColors.primary);
  root.style.setProperty('--accent-secondary', config.accentColors.secondary);
}

// Subscribe to system theme changes if we are on 'system' mode
if (typeof window !== 'undefined' && typeof window.matchMedia === 'function') {
  window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', () => {
    const current = loadTheme();
    if (current.mode === 'system') {
      applyTheme(current);
    }
  });
}
