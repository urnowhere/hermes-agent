import { useState, useEffect } from 'react';
import { loadTheme, saveTheme, ACCENT_PRESETS, ThemeMode, ThemeConfig } from '../../lib/theme';
import { apiClient } from '../../lib/api';
import { toastStore } from '../../store/toastStore';

export function ThemeSettings() {
  const [config, setConfig] = useState<ThemeConfig>(loadTheme());

  useEffect(() => {
    // Try to load from backend so themes roam
    apiClient.get<{ ok: boolean; settings: any }>('/settings')
      .then(res => {
        if (res.ok && res.settings?.display?.web_theme) {
          const remoteTheme = res.settings.display.web_theme;
          setConfig(remoteTheme);
          saveTheme(remoteTheme);
        }
      })
      .catch(console.error);
  }, []);

  const handleSaveToBackend = async (themeToSave: ThemeConfig) => {
    try {
      await apiClient.patch('/settings', { display: { web_theme: themeToSave } });
    } catch (err) {
      console.error('Failed to sync theme to backend', err);
    }
  };

  const handleModeChange = (mode: ThemeMode) => {
    const next = { ...config, mode };
    setConfig(next);
    saveTheme(next);
    handleSaveToBackend(next);
  };

  const handleAccentChange = (accentColors: ThemeConfig['accentColors']) => {
    const next = { ...config, accentColors };
    setConfig(next);
    saveTheme(next);
    handleSaveToBackend(next);
  };

  return (
    <div style={{
      background: 'var(--bg-surface)',
      border: '1px solid var(--border-light)',
      borderRadius: '16px',
      padding: '24px',
      marginBottom: '16px'
    }}>
      <div style={{ marginBottom: '24px' }}>
        <h2 style={{ fontSize: '1.2rem', marginBottom: '8px', color: 'var(--text-main)' }}>Web Console Theme</h2>
        <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem', margin: 0 }}>
          Customize the appearance of the web interface. This does not affect CLI skins.
        </p>
      </div>

      <div style={{ display: 'grid', gap: '24px' }}>
        {/* Mode Selection */}
        <div>
          <label style={{ display: 'block', fontSize: '0.9rem', fontWeight: 500, marginBottom: '12px', color: 'var(--text-main)' }}>
            Appearance Mode
          </label>
          <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
            {(['dark', 'light', 'system'] as ThemeMode[]).map(m => (
              <button
                key={m}
                type="button"
                onClick={() => handleModeChange(m)}
                style={{
                  background: config.mode === m ? 'rgba(129, 140, 248, 0.15)' : 'rgba(255, 255, 255, 0.05)',
                  border: `1px solid ${config.mode === m ? 'var(--accent-primary)' : 'var(--border-light)'}`,
                  color: config.mode === m ? 'var(--accent-primary)' : 'var(--text-muted)',
                  borderRadius: '8px',
                  padding: '8px 16px',
                  cursor: 'pointer',
                  textTransform: 'capitalize',
                  transition: 'all 0.2s',
                  fontWeight: config.mode === m ? 600 : 400
                }}
              >
                {m}
              </button>
            ))}
          </div>
        </div>

        {/* Accent Colors */}
        <div>
          <label style={{ display: 'block', fontSize: '0.9rem', fontWeight: 500, marginBottom: '12px', color: 'var(--text-main)' }}>
            Accent Color
          </label>
          <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap' }}>
            {ACCENT_PRESETS.map((preset) => {
              const active = config.accentColors.primary === preset.colors.primary;
              return (
                <button
                  key={preset.name}
                  onClick={() => handleAccentChange(preset.colors)}
                  title={preset.name}
                  style={{
                    width: '32px',
                    height: '32px',
                    borderRadius: '50%',
                    background: `linear-gradient(135deg, ${preset.colors.primary}, ${preset.colors.secondary})`,
                    border: `2px solid ${active ? 'var(--text-main)' : 'transparent'}`,
                    cursor: 'pointer',
                    padding: 0,
                    outlineOffset: '2px',
                    outline: active ? `2px solid ${preset.colors.primary}` : 'none',
                    transition: 'all 0.2s ease',
                    boxShadow: '0 2px 8px rgba(0,0,0,0.1)'
                  }}
                />
              )
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
