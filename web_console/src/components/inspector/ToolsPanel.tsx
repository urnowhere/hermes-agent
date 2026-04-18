import { useEffect, useState } from 'react';
import { apiClient } from '../../lib/api';
import { ApiKeyConfigModal } from '../settings/ApiKeyConfigModal';

interface ToolDef {
  name: string;
  toolset: string;
  description: string;
  is_available: boolean;
  requires_env: string[];
}

export function ToolsPanel() {
  const [tools, setTools] = useState<ToolDef[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [showKeyConfig, setShowKeyConfig] = useState(false);

  const fetchTools = async () => {
    try {
      const res = await apiClient.get<{ ok: boolean; tools: ToolDef[] }>('/tools');
      if (res.ok) {
        setTools(res.tools || []);
      }
    } catch (err) {
      console.error('Failed to load tools:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchTools();
  }, []);

  const filtered = tools.filter(t => t.name.toLowerCase().includes(search.toLowerCase()) || t.description.toLowerCase().includes(search.toLowerCase()));
  
  // Group by toolset
  const groups: Record<string, ToolDef[]> = {};
  for (const t of filtered) {
    if (!groups[t.toolset]) groups[t.toolset] = [];
    groups[t.toolset].push(t);
  }

  if (loading) {
    return <div style={{ padding: '24px', color: '#94a3b8', textAlign: 'center' }}>Loading tools...</div>;
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', color: '#f8fafc' }}>
      <div style={{ padding: '16px', borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
        <input 
          type="text" 
          placeholder="Search tools..." 
          value={search}
          onChange={e => setSearch(e.target.value)}
          style={{ width: '100%', padding: '8px 12px', background: 'rgba(0,0,0,0.2)', border: '1px solid rgba(255,255,255,0.1)', color: '#fff', borderRadius: '4px', fontSize: '0.875rem' }}
        />
      </div>
      <div style={{ flex: 1, overflowY: 'auto', padding: '16px' }}>
        {Object.entries(groups).map(([toolset, groupTools]) => (
          <div key={toolset} style={{ marginBottom: '24px' }}>
            <h4 style={{ margin: '0 0 12px 0', fontSize: '0.8rem', textTransform: 'uppercase', letterSpacing: '0.5px', color: '#818cf8' }}>
              {toolset}
            </h4>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              {groupTools.map(t => (
                <div key={t.name} style={{ background: 'rgba(255,255,255,0.03)', padding: '12px', borderRadius: '6px', border: '1px solid rgba(255,255,255,0.05)' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '4px' }}>
                    <span style={{ fontWeight: 600, fontSize: '0.9rem', color: t.is_available ? '#e2e8f0' : '#94a3b8' }}>
                      {t.name}
                    </span>
                    <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                      {!t.is_available && t.requires_env && t.requires_env.length > 0 && (
                        <button
                          onClick={() => setShowKeyConfig(true)}
                          style={{ fontSize: '0.75rem', padding: '2px 8px', borderRadius: '4px', background: 'rgba(255,255,255,0.1)', border: '1px solid rgba(255,255,255,0.2)', color: '#e2e8f0', cursor: 'pointer' }}
                        >
                          Add Keys
                        </button>
                      )}
                      <span style={{ fontSize: '0.75rem', padding: '2px 6px', borderRadius: '4px', background: t.is_available ? 'rgba(34, 197, 94, 0.1)' : 'rgba(239, 68, 68, 0.1)', color: t.is_available ? '#4ade80' : '#f87171' }}>
                        {t.is_available ? 'Ready' : 'Env Missing'}
                      </span>
                    </div>
                  </div>
                  <div style={{ fontSize: '0.8rem', color: '#94a3b8', lineHeight: 1.4 }}>
                    {t.description.split('.')[0] + '.'} {/* show short desc */}
                  </div>
                  {!t.is_available && t.requires_env && t.requires_env.length > 0 && (
                    <div style={{ marginTop: '8px', fontSize: '0.75rem', color: '#fca5a5' }}>
                      Missing: {t.requires_env.join(', ')}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        ))}
        {filtered.length === 0 && (
          <div style={{ textAlign: 'center', color: '#64748b', fontSize: '0.875rem', marginTop: '24px' }}>
            No tools found.
          </div>
        )}
      </div>

      {showKeyConfig && (
        <ApiKeyConfigModal
          categories={['tool', 'provider']}
          onClose={() => setShowKeyConfig(false)}
          onSaved={() => {
            setShowKeyConfig(false);
            fetchTools();
          }}
        />
      )}
    </div>
  );
}
