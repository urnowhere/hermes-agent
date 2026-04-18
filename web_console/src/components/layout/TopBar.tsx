import { useEffect, useState, useRef, useCallback } from 'react';
import { apiClient } from '../../lib/api';
import { toastStore } from '../../store/toastStore';
import type { NavItem, PrimaryRoute } from '../../lib/types';

interface TopBarProps {
  title: string;
  navItems: readonly NavItem[];
  activeRoute: PrimaryRoute;
  onNavigate(route: PrimaryRoute): void;
  onToggleSettings(): void;
  onToggleInspector(): void;
  onToggleDrawer(): void;
  voiceMode: boolean;
  onToggleVoiceMode(): void;
  onOpenCommandPalette(): void;
}

interface VersionResponse {
  ok: boolean;
  version?: string;
  release_date?: string;
}

interface UpdateResponse {
  ok: boolean;
  current_version: string;
  latest_version: string;
  has_update: boolean;
  project_url: string;
}

interface ActiveModelResponse {
  ok: boolean;
  model: string;
  provider: string;
  provider_label: string;
  context_window?: number;
  cost?: string;
  capabilities?: string;
}

interface SwitchResponse {
  ok: boolean;
  new_model: string;
  provider: string;
  provider_label: string;
  context_window?: number;
  cost?: string;
  error?: string;
}

const NAV_ICONS: Record<string, string> = {
  chat: '💬',
  sessions: '📋',
  workspace: '📂',
  usage: '📊',
  jobs: '⚡',
  skills: '✨',
  memory: '🧠',
  missions: '🎯',
  commands: '⌘'
};

const QUICK_MODELS = [
  { alias: 'sonnet', label: 'Claude Sonnet', icon: '🟣' },
  { alias: 'opus', label: 'Claude Opus', icon: '🟣' },
  { alias: 'haiku', label: 'Claude Haiku', icon: '🟣' },
  { alias: 'gpt', label: 'GPT (Latest)', icon: '🟢' },
  { alias: 'gemini', label: 'Gemini', icon: '🔵' },
  { alias: 'deepseek', label: 'DeepSeek', icon: '🔷' },
  { alias: 'grok', label: 'Grok', icon: '⚫' },
];

export function TopBar({ title, navItems, activeRoute, onNavigate, onToggleSettings, onToggleDrawer, onToggleInspector, voiceMode, onToggleVoiceMode, onOpenCommandPalette }: TopBarProps) {
  const [version, setVersion] = useState<string | null>(null);
  const [activeProfile, setActiveProfile] = useState<string | null>(null);
  const [checkingUpdate, setCheckingUpdate] = useState(false);
  const [updateInfo, setUpdateInfo] = useState<UpdateResponse | null>(null);
  const [activeModel, setActiveModel] = useState<ActiveModelResponse | null>(null);
  const [quickSwitchOpen, setQuickSwitchOpen] = useState(false);
  const [quickSwitching, setQuickSwitching] = useState<string | null>(null);
  const [usageTokens, setUsageTokens] = useState<{ prompt_tokens?: number; completion_tokens?: number; total_tokens?: number }>({});
  const dropdownRef = useRef<HTMLDivElement>(null);

  const fetchActiveModel = useCallback(async () => {
    try {
      const res = await apiClient.get<ActiveModelResponse>('/models/active');
      if (res.ok) setActiveModel(res);
    } catch { /* silent */ }
  }, []);

  useEffect(() => {
    apiClient.get<VersionResponse>('/version')
      .then(res => {
        if (res.ok && res.version) {
          setVersion(`v${res.version}`);
        }
      })
      .catch(() => {});

    apiClient.get<any>('/profiles')
      .then(res => {
        if (res.ok && res.active_profile) {
          setActiveProfile(res.active_profile);
        }
      })
      .catch(() => {});

    fetchActiveModel();
  }, [fetchActiveModel]);

  // Close dropdown on outside click
  useEffect(() => {
    if (!quickSwitchOpen) return;
    const handler = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setQuickSwitchOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [quickSwitchOpen]);

  // Listen for usage sync events from ChatPage
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      if (detail?.usage) {
        setUsageTokens(detail.usage);
      }
    };
    window.addEventListener('hermes-usage-sync', handler);
    return () => window.removeEventListener('hermes-usage-sync', handler);
  }, []);

  const handleCheckUpdate = async () => {
    setCheckingUpdate(true);
    try {
      const res = await apiClient.get<UpdateResponse>('/version/check');
      if (res.ok) {
        setUpdateInfo(res);
        if (res.has_update) {
          toastStore.info('Update Available!', `Version v${res.latest_version} is available. Visit ${res.project_url} for details.`);
        } else {
          toastStore.success('Up to date!', `You are running the latest version (v${res.current_version}).`);
        }
      }
    } catch (err) {
      toastStore.error('Update Check Failed', err instanceof Error ? err.message : String(err));
    } finally {
      setCheckingUpdate(false);
    }
  };

  const handleQuickSwitch = async (alias: string) => {
    setQuickSwitching(alias);
    try {
      const res = await apiClient.post<SwitchResponse>('/models/switch', { model: alias, global: true });
      if (res.ok) {
        toastStore.success('Model Switched', `Now using ${res.new_model} on ${res.provider_label}`);
        await fetchActiveModel();
        setQuickSwitchOpen(false);
      } else {
        toastStore.error('Switch Failed', res.error || 'Unknown error');
      }
    } catch (err) {
      toastStore.error('Switch Failed', err instanceof Error ? err.message : String(err));
    } finally {
      setQuickSwitching(null);
    }
  };

  /* Compact model name for badge display */
  const displayModel = activeModel?.model
    ? activeModel.model.length > 32
      ? activeModel.model.split('/').pop() || activeModel.model
      : activeModel.model
    : null;

  return (
    <header className="topbar">
      {/* Left: Brand + version */}
      <div className="topbar-brand">
        <span className="topbar-logo">⚕</span>
        <h1>{title}</h1>
        {version && (
          <span className="topbar-version">
            {version}
            <button
              onClick={handleCheckUpdate}
              disabled={checkingUpdate}
              title="Check for updates"
              className="topbar-update-btn"
              style={{
                opacity: checkingUpdate ? 0.5 : 1,
                color: updateInfo?.has_update ? '#38bdf8' : undefined,
              }}
            >
              {checkingUpdate ? '⏳' : updateInfo?.has_update ? '⬆️' : '🔄'}
            </button>
          </span>
        )}
        {activeProfile && activeProfile !== 'default' && (
          <span className="topbar-profile-badge">
            {activeProfile}
          </span>
        )}

        {/* Model Indicator */}
        {displayModel && (
          <div ref={dropdownRef} style={{ position: 'relative' }}>
            <button
              onClick={() => setQuickSwitchOpen(!quickSwitchOpen)}
              title={`Active: ${activeModel?.model} on ${activeModel?.provider_label}${activeModel?.cost ? `\nCost: ${activeModel.cost}` : ''}${activeModel?.capabilities ? `\nCapabilities: ${activeModel.capabilities}` : ''}\nClick for quick switch`}
              style={{
                display: 'flex', alignItems: 'center', gap: '6px',
                padding: '3px 10px',
                background: quickSwitchOpen ? 'rgba(99,102,241,0.15)' : 'rgba(255,255,255,0.06)',
                border: `1px solid ${quickSwitchOpen ? 'rgba(99,102,241,0.3)' : 'rgba(255,255,255,0.08)'}`,
                borderRadius: '14px',
                color: '#94a3b8',
                fontSize: '0.72rem',
                cursor: 'pointer',
                transition: 'all 0.2s ease',
                fontFamily: "'JetBrains Mono', monospace",
                whiteSpace: 'nowrap',
              }}
            >
              <span style={{ width: '5px', height: '5px', borderRadius: '50%', background: '#22c55e', flexShrink: 0 }} />
              {displayModel}
              {activeModel?.cost && (
                <span style={{ fontSize: '0.62rem', color: '#64748b', borderLeft: '1px solid rgba(255,255,255,0.1)', paddingLeft: '6px', marginLeft: '2px' }}>
                  {activeModel.cost.split(',')[0]}
                </span>
              )}
              <span style={{ fontSize: '0.6rem', opacity: 0.6 }}>▼</span>
            </button>

            {/* Quick Switch Dropdown */}
            {quickSwitchOpen && (
              <div style={{
                position: 'absolute',
                top: 'calc(100% + 6px)',
                left: 0,
                minWidth: '240px',
                background: '#1e1e2e',
                border: '1px solid rgba(255,255,255,0.1)',
                borderRadius: '10px',
                boxShadow: '0 12px 40px rgba(0,0,0,0.5)',
                zIndex: 1000,
                overflow: 'hidden',
                animation: 'fadeInDown 0.15s ease-out',
              }}>
                <div style={{ padding: '10px 14px 4px' }}>
                  <div style={{
                    fontSize: '0.7rem',
                    fontWeight: 600,
                    color: '#64748b',
                    textTransform: 'uppercase',
                    letterSpacing: '0.06em',
                  }}>
                    Quick Switch
                  </div>
                  {activeModel?.cost && (
                    <div style={{
                      fontSize: '0.65rem',
                      color: '#475569',
                      marginTop: '3px',
                      fontFamily: "'JetBrains Mono', monospace",
                    }}>
                      💰 {activeModel.cost}
                    </div>
                  )}
                </div>
                {QUICK_MODELS.map(qm => (
                  <button
                    key={qm.alias}
                    onClick={() => handleQuickSwitch(qm.alias)}
                    disabled={quickSwitching !== null}
                    style={{
                      display: 'flex', alignItems: 'center', gap: '10px',
                      width: '100%',
                      padding: '10px 14px',
                      background: quickSwitching === qm.alias ? 'rgba(99,102,241,0.1)' : 'transparent',
                      border: 'none',
                      color: '#e2e8f0',
                      fontSize: '0.85rem',
                      cursor: quickSwitching ? 'wait' : 'pointer',
                      textAlign: 'left',
                      transition: 'background 0.1s',
                      opacity: quickSwitching && quickSwitching !== qm.alias ? 0.4 : 1,
                    }}
                    onMouseEnter={e => { if (!quickSwitching) (e.target as HTMLElement).style.background = 'rgba(255,255,255,0.05)'; }}
                    onMouseLeave={e => { if (!quickSwitching) (e.target as HTMLElement).style.background = 'transparent'; }}
                  >
                    <span>{qm.icon}</span>
                    <span style={{ flex: 1 }}>{qm.label}</span>
                    {quickSwitching === qm.alias && <span style={{ fontSize: '0.75rem' }}>⏳</span>}
                  </button>
                ))}
                <div style={{
                  borderTop: '1px solid rgba(255,255,255,0.06)',
                  padding: '8px 14px',
                  fontSize: '0.7rem',
                  color: '#475569',
                  textAlign: 'center',
                }}>
                  Open Settings for full catalog
                </div>
              </div>
            )}
          </div>
        )}

        {/* Context Window Usage Meter */}
        {activeModel?.context_window && usageTokens.prompt_tokens != null && usageTokens.prompt_tokens > 0 && (() => {
          const used = usageTokens.prompt_tokens || 0;
          const max = activeModel.context_window;
          const ratio = Math.min(used / max, 1);
          const pct = (ratio * 100).toFixed(1);
          const color = ratio < 0.5 ? '#22c55e' : ratio < 0.8 ? '#eab308' : '#ef4444';
          const usedK = used >= 1000 ? `${(used / 1000).toFixed(1)}k` : String(used);
          const maxK = max >= 1000 ? `${(max / 1000).toFixed(0)}k` : String(max);

          return (
            <div
              title={`Context usage: ${usedK} / ${maxK} tokens (${pct}%)`}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
                padding: '3px 10px',
                background: 'rgba(255,255,255,0.04)',
                border: '1px solid rgba(255,255,255,0.06)',
                borderRadius: '14px',
                fontSize: '0.68rem',
                fontFamily: "'JetBrains Mono', monospace",
                color: '#94a3b8',
                whiteSpace: 'nowrap',
              }}
            >
              <span style={{ fontSize: '0.75rem' }}>📊</span>
              <div style={{
                width: '60px',
                height: '6px',
                borderRadius: '3px',
                background: 'rgba(255,255,255,0.08)',
                overflow: 'hidden',
                position: 'relative',
              }}>
                <div style={{
                  width: `${ratio * 100}%`,
                  height: '100%',
                  borderRadius: '3px',
                  background: color,
                  transition: 'width 0.5s ease, background 0.3s ease',
                }} />
              </div>
              <span style={{ color, fontWeight: 600 }}>{pct}%</span>
              <span style={{ color: '#64748b' }}>{usedK}/{maxK}</span>
            </div>
          );
        })()}
      </div>

      {/* Center: Navigation tabs */}
      <nav className="topbar-nav" aria-label="Primary navigation">
        {navItems.map((item) => (
          <button
            key={item.id}
            type="button"
            className={`topbar-nav-item ${activeRoute === item.id ? 'topbar-nav-item--active' : ''}`}
            onClick={() => onNavigate(item.id)}
            title={item.description}
          >
            <span className="topbar-nav-icon">{NAV_ICONS[item.id] || '📄'}</span>
            <span className="topbar-nav-label">{item.label}</span>
          </button>
        ))}
      </nav>

      {/* Right: Action buttons */}
      <div className="topbar-actions">
        <button type="button" className="topbar-action-btn" onClick={onOpenCommandPalette} title="Command Palette (Ctrl/Cmd+K)">
          ⌘
        </button>
        <button 
          type="button" 
          className="topbar-action-btn" 
          onClick={onToggleVoiceMode} 
          title="Voice Mode (Text-to-Speech)"
          style={voiceMode ? { color: '#fcd34d', background: 'rgba(252, 211, 77, 0.15)' } : {}}
        >
          {voiceMode ? '🔊' : '🔈'}
        </button>
        <button type="button" className="topbar-action-btn" onClick={onToggleSettings} title="Control Center">
          ⚙️
        </button>
        <button type="button" className="topbar-action-btn" onClick={onToggleInspector} title="Inspector Panel">
          🖥️
        </button>
        <button type="button" className="topbar-action-btn" onClick={onToggleDrawer} title="Terminal Drawer">
          ⌨️
        </button>
      </div>
    </header>
  );
}
