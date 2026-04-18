interface PlatformCard {
  id: string;
  name: string;
  status: string;
  detail: string;
}

interface PlatformCardsProps {
  platforms: PlatformCard[];
  onConfigure?: (platformId: string) => void;
  onToggle?: (platformId: string, action: 'start' | 'stop') => void;
}

const STATUS_STYLES: Record<string, { bg: string; color: string; border: string; label: string }> = {
  connected: { bg: 'rgba(34, 197, 94, 0.15)', color: '#86efac', border: 'rgba(34, 197, 94, 0.3)', label: '● Connected' },
  enabled: { bg: 'rgba(56, 189, 248, 0.15)', color: '#7dd3fc', border: 'rgba(56, 189, 248, 0.3)', label: '● Enabled' },
  active: { bg: 'rgba(34, 197, 94, 0.15)', color: '#86efac', border: 'rgba(34, 197, 94, 0.3)', label: '● Active' },
  configured: { bg: 'rgba(251, 191, 36, 0.15)', color: '#fde68a', border: 'rgba(251, 191, 36, 0.3)', label: '○ Configured' },
  disabled: { bg: 'rgba(100, 116, 139, 0.15)', color: '#94a3b8', border: 'rgba(100, 116, 139, 0.3)', label: '○ Disabled' },
};

const PLATFORM_ICONS: Record<string, string> = {
  telegram: '✈️',
  discord: '🎮',
  slack: '💬',
  whatsapp: '📱',
  matrix: '🔗',
  mattermost: '🟣',
  signal: '🔒',
  email: '📧',
  feishu: '🐦',
  wecom: '🏢',
  'api-server': '🌐',
};

export function PlatformCards({ platforms, onConfigure, onToggle }: PlatformCardsProps) {
  return (
    <section style={{
      background: 'rgba(255, 255, 255, 0.03)',
      border: '1px solid rgba(255, 255, 255, 0.06)',
      borderRadius: '16px',
      padding: '20px',
    }} aria-label="Gateway platforms">
      <div style={{ marginBottom: '16px' }}>
        <h2 style={{ margin: 0, color: '#e2e8f0', fontSize: '1.1rem' }}>🌐 Gateway Platforms</h2>
        <p style={{ margin: '4px 0 0', color: '#64748b', fontSize: '0.85rem' }}>Connected messaging and API platforms.</p>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))', gap: '12px' }}>
        {platforms.map((platform) => {
          const style = STATUS_STYLES[platform.status] || STATUS_STYLES.disabled;
          const icon = PLATFORM_ICONS[(platform.id || platform.name || '').toLowerCase()] || '🔌';
          return (
            <div key={platform.id} style={{
              background: style.bg,
              border: `1px solid ${style.border}`,
              borderRadius: '14px',
              padding: '16px',
              display: 'flex',
              flexDirection: 'column',
              gap: '8px',
              transition: 'all 0.2s',
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontSize: '1.3rem' }}>{icon}</span>
                <span style={{
                  fontSize: '0.75rem',
                  color: style.color,
                  padding: '2px 8px',
                  borderRadius: '20px',
                  background: 'rgba(0, 0, 0, 0.2)',
                }}>
                  {style.label}
                </span>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '2px', flex: 1 }}>
                <strong style={{ color: '#e2e8f0', fontSize: '0.95rem', textTransform: 'capitalize' }}>
                  {platform.name}
                </strong>
                <span style={{ color: '#94a3b8', fontSize: '0.8rem' }}>
                  {platform.detail}
                </span>
              </div>
              
              <div style={{ display: 'flex', gap: '8px', marginTop: 'auto' }}>
                {onConfigure && (
                  <button 
                    type="button" 
                    onClick={() => onConfigure(platform.id)}
                    style={{ flex: 1, padding: '6px', borderRadius: '8px', background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.1)', color: '#cbd5e1', cursor: 'pointer', fontSize: '0.8rem' }}
                  >
                    Configure
                  </button>
                )}
                {onToggle && (
                  <button 
                    type="button" 
                    onClick={() => onToggle(platform.id, platform.status === 'disabled' ? 'start' : 'stop')}
                    style={{ flex: 1, padding: '6px', borderRadius: '8px', background: platform.status === 'disabled' ? 'rgba(34,197,94,0.1)' : 'rgba(239,68,68,0.1)', border: `1px solid ${platform.status === 'disabled' ? 'rgba(34,197,94,0.2)' : 'rgba(239,68,68,0.2)'}`, color: platform.status === 'disabled' ? '#86efac' : '#fca5a5', cursor: 'pointer', fontSize: '0.8rem' }}
                  >
                    {platform.status === 'disabled' ? 'Start' : 'Stop'}
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {platforms.length === 0 && (
        <p style={{ color: '#475569', textAlign: 'center', padding: '24px', fontSize: '0.9rem' }}>
          No gateway platforms detected. Start the gateway with platform adapters enabled.
        </p>
      )}
    </section>
  );
}
