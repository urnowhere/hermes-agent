import { useEffect, useState } from 'react';
import { apiClient } from '../../lib/api';

interface BrowserStatus {
  connected?: boolean;
  cdp_url?: string;
  mode?: string;
}

interface BrowserStatusResponse {
  ok: boolean;
  browser?: BrowserStatus;
}

export function BrowserControlPanel() {
  const [status, setStatus] = useState<BrowserStatus>({});
  const [cdpUrl, setCdpUrl] = useState('');
  const [loading, setLoading] = useState(false);

  const refreshStatus = async () => {
    try {
      const res = await apiClient.get<BrowserStatusResponse>('/browser/status');
      if (res.ok && res.browser) setStatus(res.browser);
    } catch { /* ignore */ }
  };

  useEffect(() => {
    refreshStatus();
    const pollId = setInterval(refreshStatus, 5000);
    return () => clearInterval(pollId);
  }, []);

  const handleConnect = async () => {
    setLoading(true);
    try {
      const body = cdpUrl.trim() ? { cdp_url: cdpUrl.trim() } : {};
      await apiClient.post('/browser/connect', body);
      await refreshStatus();
    } finally {
      setLoading(false);
    }
  };

  const handleDisconnect = async () => {
    setLoading(true);
    try {
      await apiClient.post('/browser/disconnect', {});
      await refreshStatus();
    } finally {
      setLoading(false);
    }
  };

  const isConnected = status.connected === true;

  const btnStyle: React.CSSProperties = {
    padding: '8px 16px', borderRadius: '10px', cursor: 'pointer', fontSize: '0.85rem',
    border: '1px solid rgba(255,255,255,0.1)', background: 'rgba(255,255,255,0.06)',
    transition: 'all 0.2s',
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', padding: '4px' }}>
      <h3 style={{ margin: 0, color: '#e2e8f0', fontSize: '1rem' }}>🌐 Browser</h3>

      {/* Status indicator */}
      <div style={{
        padding: '12px 14px', borderRadius: '12px',
        background: isConnected ? 'rgba(34, 197, 94, 0.08)' : 'rgba(239, 68, 68, 0.08)',
        border: `1px solid ${isConnected ? 'rgba(34, 197, 94, 0.2)' : 'rgba(239, 68, 68, 0.2)'}`,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span style={{ fontSize: '1.2rem' }}>{isConnected ? '🟢' : '🔴'}</span>
          <span style={{ color: isConnected ? '#86efac' : '#fca5a5', fontWeight: 600, fontSize: '0.9rem' }}>
            {isConnected ? 'Connected' : 'Disconnected'}
          </span>
        </div>
        {status.mode && (
          <div style={{ marginTop: '6px', fontSize: '0.8rem', color: '#64748b' }}>
            Mode: <span style={{ color: '#94a3b8' }}>{status.mode}</span>
          </div>
        )}
        {status.cdp_url && (
          <div style={{ marginTop: '2px', fontSize: '0.8rem', color: '#64748b' }}>
            CDP: <code style={{ color: '#a5b4fc', fontSize: '0.75rem' }}>{status.cdp_url}</code>
          </div>
        )}
      </div>

      {/* Connect / Disconnect */}
      {!isConnected ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          <input
            type="text"
            value={cdpUrl}
            onChange={(e) => setCdpUrl(e.target.value)}
            placeholder="CDP URL (optional)"
            style={{
              padding: '8px 12px', borderRadius: '10px',
              border: '1px solid rgba(129,140,248,0.2)',
              background: 'rgba(0,0,0,0.2)', color: 'white', fontSize: '0.85rem',
            }}
          />
          <button
            type="button" onClick={handleConnect} disabled={loading}
            style={{ ...btnStyle, color: '#86efac', borderColor: 'rgba(34,197,94,0.3)', background: 'rgba(34,197,94,0.1)' }}
          >
            {loading ? 'Connecting…' : '🔌 Connect'}
          </button>
        </div>
      ) : (
        <button
          type="button" onClick={handleDisconnect} disabled={loading}
          style={{ ...btnStyle, color: '#fca5a5', borderColor: 'rgba(239,68,68,0.3)', background: 'rgba(239,68,68,0.1)' }}
        >
          {loading ? 'Disconnecting…' : '⏏ Disconnect'}
        </button>
      )}
    </div>
  );
}
