import { useState, useEffect } from 'react';
import { apiClient } from '../../lib/api';
import { toastStore } from '../../store/toastStore';

interface PluginInfo {
  name: string;
  version: string;
  description: string;
  source: string;
  enabled: boolean;
  tools: number;
  hooks: number;
  error?: string;
}

interface PluginsResponse {
  ok: boolean;
  plugins: PluginInfo[];
}

export function PluginList() {
  const [plugins, setPlugins] = useState<PluginInfo[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchPlugins = async () => {
    try {
      const res = await apiClient.get<PluginsResponse>('/plugins');
      if (res?.ok) {
        setPlugins(res.plugins || []);
      } else {
        toastStore.error('Failed to load plugins');
      }
    } catch (e) {
      console.error('Plugin fetch error:', e);
      toastStore.error('Network error loading plugins');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchPlugins();
  }, []);

  if (loading) {
    return <p style={{ color: '#94a3b8' }}>Loading plugins...</p>;
  }

  return (
    <section style={{
      background: 'rgba(30, 41, 59, 0.4)',
      border: '1px solid rgba(255, 255, 255, 0.05)',
      borderRadius: '16px',
      padding: '20px',
      marginBottom: '24px'
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '16px' }}>
        <div>
          <h2 style={{ margin: 0, color: '#e2e8f0', fontSize: '1.1rem' }}>🔌 Plugins</h2>
          <p style={{ margin: '4px 0 0', color: '#64748b', fontSize: '0.85rem' }}>
            Extending Hermes functionality with tools and hooks
          </p>
        </div>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
        {plugins.map(p => (
          <div key={p.name} style={{
            padding: '16px',
            borderRadius: '12px',
            background: 'rgba(0,0,0,0.2)',
            border: `1px solid ${p.enabled ? 'rgba(56, 189, 248, 0.2)' : 'rgba(239, 68, 68, 0.2)'}`,
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center'
          }}>
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
                <strong style={{ color: '#e2e8f0', fontSize: '1.05rem' }}>
                  {p.name}
                </strong>
                {p.version && <span style={{ fontSize: '0.7rem', color: '#94a3b8' }}>v{p.version}</span>}
                <span style={{ 
                  fontSize: '0.7rem', padding: '2px 6px', borderRadius: '4px',
                  background: p.enabled ? 'rgba(56, 189, 248, 0.1)' : 'rgba(239, 68, 68, 0.1)',
                  color: p.enabled ? '#38bdf8' : '#ef4444'
                }}>
                  {p.enabled ? 'ACTIVE' : 'INACTIVE'}
                </span>
                <span style={{ fontSize: '0.7rem', padding: '2px 6px', background: 'rgba(255,255,255,0.1)', borderRadius: '4px', color: '#94a3b8' }}>{p.source.toUpperCase()}</span>
              </div>
              <p style={{ margin: '4px 0 8px', color: '#94a3b8', fontSize: '0.85rem' }}>
                {p.description || <em>No description provided</em>}
              </p>
              <div style={{ display: 'flex', gap: '12px', fontSize: '0.8rem', color: '#64748b' }}>
                <span>🛠️ {p.tools} tools</span>
                <span>🪝 {p.hooks} hooks</span>
              </div>
              {p.error && (
                <div style={{ marginTop: '8px', padding: '6px 12px', background: 'rgba(239, 68, 68, 0.1)', color: '#ef4444', borderRadius: '6px', fontSize: '0.8rem' }}>
                  <strong>Error:</strong> {p.error}
                </div>
              )}
            </div>
          </div>
        ))}
        {plugins.length === 0 && (
          <p style={{ color: '#64748b', fontStyle: 'italic', margin: 0, padding: '16px', textAlign: 'center' }}>
            No plugins installed.
          </p>
        )}
      </div>
    </section>
  );
}
