import { useEffect, useState, useCallback } from 'react';
import { apiClient } from '../../lib/api';
import { toastStore } from '../../store/toastStore';

interface ConfigVar {
  key: string;
  description: string;
  default?: string;
  prompt?: string;
  skill?: string;
  value?: string;
}

interface ConfigResponse {
  ok: boolean;
  config_vars: ConfigVar[];
  count: number;
}

export function SkillConfigPanel() {
  const [vars, setVars] = useState<ConfigVar[]>([]);
  const [loading, setLoading] = useState(true);
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [editValue, setEditValue] = useState('');
  const [saving, setSaving] = useState(false);

  const fetchConfig = useCallback(async () => {
    try {
      const res = await apiClient.get<ConfigResponse>('/skills/config');
      if (res.ok) {
        setVars(res.config_vars);
      }
    } catch { /* silent */ }
    setLoading(false);
  }, []);

  useEffect(() => {
    fetchConfig();
  }, [fetchConfig]);

  const handleEdit = (v: ConfigVar) => {
    setEditingKey(v.key);
    setEditValue(String(v.value ?? v.default ?? ''));
  };

  const handleSave = async (key: string) => {
    setSaving(true);
    try {
      const res = await apiClient.post<{ ok: boolean; error?: { message: string } }>('/skills/config', { key, value: editValue });
      if (res.ok) {
        toastStore.success('Saved', `Config "${key}" updated.`);
        setEditingKey(null);
        await fetchConfig();
      } else {
        toastStore.error('Save Failed', (res as any).error?.message || 'Unknown error');
      }
    } catch (err) {
      toastStore.error('Save Failed', err instanceof Error ? err.message : String(err));
    }
    setSaving(false);
  };

  const handleCancel = () => {
    setEditingKey(null);
    setEditValue('');
  };

  if (loading) {
    return (
      <div style={{ padding: '40px', textAlign: 'center', color: '#64748b' }}>
        Loading skill configuration…
      </div>
    );
  }

  if (vars.length === 0) {
    return (
      <section style={{
        background: 'rgba(255, 255, 255, 0.03)',
        border: '1px solid rgba(255, 255, 255, 0.06)',
        borderRadius: '16px',
        padding: '40px 20px',
        textAlign: 'center',
      }}>
        <div style={{ fontSize: '2rem', marginBottom: '12px' }}>⚙️</div>
        <h3 style={{ color: '#94a3b8', fontWeight: 500, margin: '0 0 8px' }}>No Config Variables</h3>
        <p style={{ color: '#475569', fontSize: '0.85rem', margin: 0 }}>
          None of your installed skills declare configuration variables.
        </p>
      </section>
    );
  }

  // Group by skill name
  const grouped = vars.reduce<Record<string, ConfigVar[]>>((acc, v) => {
    const skill = v.skill || 'Unknown';
    if (!acc[skill]) acc[skill] = [];
    acc[skill].push(v);
    return acc;
  }, {});

  return (
    <section style={{
      display: 'flex', flexDirection: 'column', gap: '16px',
    }}>
      <div style={{ marginBottom: '4px' }}>
        <h2 style={{ margin: 0, color: '#e2e8f0', fontSize: '1.1rem' }}>⚙️ Skill Configuration</h2>
        <p style={{ margin: '4px 0 0', color: '#64748b', fontSize: '0.85rem' }}>
          Configure settings required by your installed skills. Changes are saved to config.yaml.
        </p>
      </div>

      {Object.entries(grouped).map(([skillName, configVars]) => (
        <div key={skillName} style={{
          background: 'rgba(255, 255, 255, 0.03)',
          border: '1px solid rgba(255, 255, 255, 0.06)',
          borderRadius: '14px',
          overflow: 'hidden',
        }}>
          {/* Skill header */}
          <div style={{
            padding: '10px 16px',
            background: 'rgba(255, 255, 255, 0.02)',
            borderBottom: '1px solid rgba(255, 255, 255, 0.04)',
            display: 'flex', alignItems: 'center', gap: '8px',
          }}>
            <span style={{ fontSize: '0.8rem' }}>🧩</span>
            <span style={{ color: '#a5b4fc', fontWeight: 600, fontSize: '0.9rem' }}>{skillName}</span>
            <span style={{
              fontSize: '0.65rem', color: '#475569',
              background: 'rgba(0,0,0,0.2)', padding: '1px 6px', borderRadius: '4px',
            }}>
              {configVars.length} var{configVars.length !== 1 ? 's' : ''}
            </span>
          </div>

          {/* Config vars */}
          {configVars.map((v) => {
            const isEditing = editingKey === v.key;
            const hasValue = v.value != null && v.value !== '';
            const displayValue = v.value ?? v.default ?? '';

            return (
              <div key={v.key} style={{
                padding: '12px 16px',
                borderBottom: '1px solid rgba(255, 255, 255, 0.03)',
                transition: 'background 0.15s',
              }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '12px' }}>
                  <div style={{ flex: 1 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <code style={{
                        color: '#e2e8f0',
                        fontSize: '0.82rem',
                        fontFamily: "'JetBrains Mono', monospace",
                      }}>
                        {v.key}
                      </code>
                      {!hasValue && v.default && (
                        <span style={{
                          fontSize: '0.62rem', color: '#fbbf24',
                          background: 'rgba(251, 191, 36, 0.1)',
                          padding: '1px 5px', borderRadius: '3px',
                        }}>
                          using default
                        </span>
                      )}
                    </div>
                    <p style={{ margin: '3px 0 0', color: '#64748b', fontSize: '0.78rem', lineHeight: 1.4 }}>
                      {v.description}
                    </p>
                  </div>

                  {!isEditing ? (
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexShrink: 0 }}>
                      <span style={{
                        color: hasValue ? '#94a3b8' : '#475569',
                        fontSize: '0.78rem',
                        fontFamily: "'JetBrains Mono', monospace",
                        maxWidth: '200px',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}>
                        {displayValue || '—'}
                      </span>
                      <button
                        onClick={() => handleEdit(v)}
                        style={{
                          background: 'rgba(255, 255, 255, 0.06)',
                          border: '1px solid rgba(255, 255, 255, 0.1)',
                          color: '#a5b4fc',
                          padding: '3px 10px',
                          borderRadius: '6px',
                          cursor: 'pointer',
                          fontSize: '0.75rem',
                          transition: 'all 0.15s',
                        }}
                      >
                        Edit
                      </button>
                    </div>
                  ) : (
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexShrink: 0 }}>
                      <input
                        type="text"
                        value={editValue}
                        onChange={(e) => setEditValue(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') handleSave(v.key);
                          if (e.key === 'Escape') handleCancel();
                        }}
                        autoFocus
                        style={{
                          background: 'rgba(0, 0, 0, 0.3)',
                          border: '1px solid rgba(99, 102, 241, 0.4)',
                          color: '#e2e8f0',
                          padding: '4px 10px',
                          borderRadius: '6px',
                          fontSize: '0.78rem',
                          fontFamily: "'JetBrains Mono', monospace",
                          width: '200px',
                          outline: 'none',
                        }}
                      />
                      <button
                        onClick={() => handleSave(v.key)}
                        disabled={saving}
                        style={{
                          background: 'rgba(34, 197, 94, 0.15)',
                          border: '1px solid rgba(34, 197, 94, 0.3)',
                          color: '#86efac',
                          padding: '3px 10px',
                          borderRadius: '6px',
                          cursor: saving ? 'wait' : 'pointer',
                          fontSize: '0.75rem',
                        }}
                      >
                        {saving ? '…' : 'Save'}
                      </button>
                      <button
                        onClick={handleCancel}
                        style={{
                          background: 'rgba(255, 255, 255, 0.06)',
                          border: '1px solid rgba(255, 255, 255, 0.1)',
                          color: '#94a3b8',
                          padding: '3px 10px',
                          borderRadius: '6px',
                          cursor: 'pointer',
                          fontSize: '0.75rem',
                        }}
                      >
                        ✕
                      </button>
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      ))}
    </section>
  );
}
