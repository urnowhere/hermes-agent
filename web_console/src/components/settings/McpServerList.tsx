import { useState, useEffect } from 'react';
import { apiClient } from '../../lib/api';
import { toastStore } from '../../store/toastStore';

interface McpServerInfo {
  name: string;
  transport: string;
  tools: number;
  connected: boolean;
}

interface McpServersResponse {
  servers: McpServerInfo[];
}

export function McpServerList() {
  const [servers, setServers] = useState<McpServerInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [reloading, setReloading] = useState(false);

  const fetchServers = async () => {
    try {
      const res = await apiClient.get<McpServersResponse>('/mcp/servers');
      if (res?.servers) {
        setServers(res.servers);
      } else {
        toastStore.error('Failed to load MCP servers');
      }
    } catch (e) {
      console.error('MCP server fetch error:', e);
      toastStore.error('Network error loading MCP servers');
    } finally {
      setLoading(false);
    }
  };

  const handleReload = async () => {
    setReloading(true);
    try {
      const res = await apiClient.post<any>('/mcp/reload');
      if (res?.success) {
        toastStore.success(`Reloaded MCP servers: ${res.tools_count} tools available.`);
        fetchServers();
      } else {
        toastStore.error(`Failed to reload MCP: ${res?.error || 'Unknown error'}`);
      }
    } catch (e) {
      console.error('Failed to reload MCP servers:', e);
      toastStore.error('Network error reloading MCP servers');
    } finally {
      setReloading(false);
    }
  };

  useEffect(() => {
    fetchServers();
  }, []);

  if (loading) {
    return <p style={{ color: '#94a3b8' }}>Loading MCP servers...</p>;
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
          <h2 style={{ margin: 0, color: '#e2e8f0', fontSize: '1.1rem' }}>🔌 MCP Servers</h2>
          <p style={{ margin: '4px 0 0', color: '#64748b', fontSize: '0.85rem' }}>
            Model Context Protocol servers providing external tools
          </p>
        </div>
        <button
          onClick={handleReload}
          disabled={reloading}
          style={{
            background: 'rgba(255, 255, 255, 0.05)',
            border: '1px solid rgba(255, 255, 255, 0.1)',
            color: '#e2e8f0',
            padding: '6px 12px',
            borderRadius: '8px',
            fontSize: '0.85rem',
            cursor: reloading ? 'not-allowed' : 'pointer',
            opacity: reloading ? 0.7 : 1,
            display: 'flex',
            alignItems: 'center',
            gap: '6px'
          }}
        >
          {reloading ? '↻ Reloading...' : '↻ Reload'}
        </button>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
        {servers.map(s => (
          <div key={s.name} style={{
            padding: '16px',
            borderRadius: '12px',
            background: 'rgba(0,0,0,0.2)',
            border: `1px solid ${s.connected ? 'rgba(34, 197, 94, 0.2)' : 'rgba(239, 68, 68, 0.2)'}`,
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center'
          }}>
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
                <strong style={{ color: '#e2e8f0', fontSize: '1.05rem' }}>
                  {s.name}
                </strong>
                <span style={{ 
                  fontSize: '0.7rem', padding: '2px 6px', borderRadius: '4px',
                  background: s.connected ? 'rgba(34, 197, 94, 0.1)' : 'rgba(239, 68, 68, 0.1)',
                  color: s.connected ? '#22c55e' : '#ef4444'
                }}>
                  {s.connected ? 'CONNECTED' : 'DISCONNECTED'}
                </span>
                <span style={{ fontSize: '0.7rem', padding: '2px 6px', background: 'rgba(255,255,255,0.1)', borderRadius: '4px', color: '#94a3b8' }}>
                  {s.transport.toUpperCase()}
                </span>
              </div>
              <div style={{ display: 'flex', gap: '12px', fontSize: '0.8rem', color: '#64748b', marginTop: '8px' }}>
                <span>🛠️ {s.tools} tools</span>
              </div>
            </div>
          </div>
        ))}
        {servers.length === 0 && (
          <p style={{ color: '#64748b', fontStyle: 'italic', margin: 0, padding: '16px', textAlign: 'center' }}>
            No MCP servers configured or active. Check configuration in hermes CLI.
          </p>
        )}
      </div>
    </section>
  );
}
