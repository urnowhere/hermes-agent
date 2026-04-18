import { useEffect, useState } from 'react';
import { apiClient } from '../../lib/api';
import { toastStore } from '../../store/toastStore';

/* ─── Types ────────────────────────────────────────────────────── */

interface ProviderEntry {
  name?: string;
  api?: string;
  api_key?: string;
  key_env?: string;
  default_model?: string;
  transport?: string;
}

interface SettingsResponse {
  ok: boolean;
  settings?: Record<string, any>;
}

const TRANSPORT_OPTIONS = [
  { value: 'openai_chat', label: 'OpenAI Chat Completions' },
  { value: 'anthropic_messages', label: 'Anthropic Messages' },
  { value: 'codex_responses', label: 'Codex Responses' },
];

const inputStyle: React.CSSProperties = {
  width: '100%',
  padding: '8px 12px',
  background: 'rgba(0,0,0,0.3)',
  border: '1px solid rgba(255,255,255,0.08)',
  color: '#f8fafc',
  borderRadius: '6px',
  fontSize: '0.85rem',
  fontFamily: "'JetBrains Mono', monospace",
  outline: 'none',
};

const labelStyle: React.CSSProperties = {
  display: 'block',
  marginBottom: '4px',
  fontSize: '0.75rem',
  fontWeight: 600,
  color: '#94a3b8',
};

/* ─── Component ────────────────────────────────────────────────── */

export function ProviderManager() {
  const [providers, setProviders] = useState<Record<string, ProviderEntry>>({});
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editingKey, setEditingKey] = useState<string | null>(null);

  // Form state
  const [formSlug, setFormSlug] = useState('');
  const [formName, setFormName] = useState('');
  const [formApi, setFormApi] = useState('');
  const [formKeyEnv, setFormKeyEnv] = useState('');
  const [formDefaultModel, setFormDefaultModel] = useState('');
  const [formTransport, setFormTransport] = useState('openai_chat');

  const fetchProviders = async () => {
    try {
      const res = await apiClient.get<SettingsResponse>('/settings');
      if (res.ok && res.settings) {
        const provs = res.settings.providers;
        if (provs && typeof provs === 'object') {
          setProviders(provs);
        }
      }
    } catch {
      /* silent */
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchProviders(); }, []);

  const resetForm = () => {
    setFormSlug('');
    setFormName('');
    setFormApi('');
    setFormKeyEnv('');
    setFormDefaultModel('');
    setFormTransport('openai_chat');
    setEditingKey(null);
    setShowForm(false);
  };

  const startEdit = (key: string) => {
    const entry = providers[key];
    if (!entry) return;
    setEditingKey(key);
    setFormSlug(key);
    setFormName(entry.name || '');
    setFormApi(entry.api || '');
    setFormKeyEnv(entry.key_env || '');
    setFormDefaultModel(entry.default_model || '');
    setFormTransport(entry.transport || 'openai_chat');
    setShowForm(true);
  };

  const handleSave = async () => {
    const slug = formSlug.trim().toLowerCase().replace(/\s+/g, '-');
    if (!slug) {
      toastStore.error('Missing Slug', 'Provider slug (identifier) is required');
      return;
    }
    if (!formApi.trim()) {
      toastStore.error('Missing API URL', 'Base API URL is required');
      return;
    }

    const entry: ProviderEntry = {
      api: formApi.trim(),
    };
    if (formName.trim()) entry.name = formName.trim();
    if (formKeyEnv.trim()) entry.key_env = formKeyEnv.trim();
    if (formDefaultModel.trim()) entry.default_model = formDefaultModel.trim();
    if (formTransport !== 'openai_chat') entry.transport = formTransport;

    const updated = { ...providers };
    // If renaming, remove old key
    if (editingKey && editingKey !== slug) {
      delete updated[editingKey];
    }
    updated[slug] = entry;

    try {
      await apiClient.patch('/settings', { providers: updated });
      setProviders(updated);
      toastStore.success('Provider Saved', `${formName || slug} → ${formApi.trim()}`);
      resetForm();
    } catch (err) {
      toastStore.error('Save Failed', err instanceof Error ? err.message : String(err));
    }
  };

  const handleDelete = async (key: string) => {
    const updated = { ...providers };
    delete updated[key];
    try {
      await apiClient.patch('/settings', { providers: updated });
      setProviders(updated);
      toastStore.success('Provider Removed', key);
    } catch (err) {
      toastStore.error('Delete Failed', err instanceof Error ? err.message : String(err));
    }
  };

  if (loading) {
    return <div style={{ padding: '24px', color: '#94a3b8' }}>Loading providers…</div>;
  }

  const entries = Object.entries(providers).filter(
    ([, v]) => typeof v === 'object' && v !== null
  );

  return (
    <div style={{
      background: 'rgba(255,255,255,0.03)',
      border: '1px solid rgba(255,255,255,0.08)',
      borderRadius: '12px',
      padding: '24px',
      marginBottom: '32px',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '16px' }}>
        <h2 style={{ margin: 0, fontSize: '1.25rem', color: '#f8fafc', display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span>🔌</span> Custom Providers
        </h2>
        <button
          onClick={() => { resetForm(); setShowForm(true); }}
          style={{
            padding: '6px 14px',
            background: 'linear-gradient(135deg, #6366f1, #8b5cf6)',
            color: '#fff',
            border: 'none',
            borderRadius: '6px',
            fontSize: '0.8rem',
            fontWeight: 600,
            cursor: 'pointer',
          }}
        >
          + Add Provider
        </button>
      </div>

      <p style={{ margin: '0 0 16px 0', fontSize: '0.8rem', color: '#64748b', lineHeight: 1.5 }}>
        Define custom OpenAI-compatible endpoints (Ollama, LM Studio, vLLM, etc.). 
        These are saved in the <code style={{ background: 'rgba(0,0,0,0.3)', padding: '2px 4px', borderRadius: '4px', fontSize: '0.75rem' }}>providers:</code> section of config.yaml.
      </p>

      {/* Existing Providers */}
      {entries.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginBottom: showForm ? '20px' : 0 }}>
          {entries.map(([key, entry]) => (
            <div
              key={key}
              style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                padding: '12px 16px',
                background: 'rgba(0,0,0,0.15)',
                border: '1px solid rgba(255,255,255,0.05)',
                borderRadius: '8px',
              }}
            >
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '2px' }}>
                  <span style={{ fontWeight: 600, color: '#e2e8f0', fontSize: '0.9rem' }}>
                    {entry.name || key}
                  </span>
                  <code style={{
                    fontSize: '0.7rem', padding: '2px 6px', borderRadius: '4px',
                    background: 'rgba(168,85,247,0.1)', color: '#c084fc',
                  }}>
                    --provider {key}
                  </code>
                </div>
                <div style={{ fontSize: '0.8rem', color: '#64748b', fontFamily: "'JetBrains Mono', monospace" }}>
                  {entry.api || '—'}
                  {entry.default_model && (
                    <span style={{ marginLeft: '8px', color: '#94a3b8' }}>
                      • {entry.default_model}
                    </span>
                  )}
                </div>
              </div>
              <div style={{ display: 'flex', gap: '6px' }}>
                <button
                  onClick={() => startEdit(key)}
                  style={{
                    background: 'none', border: 'none', color: '#38bdf8',
                    cursor: 'pointer', fontSize: '0.8rem', padding: '4px 8px',
                  }}
                >
                  Edit
                </button>
                <button
                  onClick={() => handleDelete(key)}
                  style={{
                    background: 'none', border: 'none', color: '#ef4444',
                    cursor: 'pointer', fontSize: '0.8rem', padding: '4px 8px',
                  }}
                >
                  ✖
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {entries.length === 0 && !showForm && (
        <div style={{
          padding: '20px', textAlign: 'center', color: '#475569',
          fontSize: '0.85rem', fontStyle: 'italic',
          background: 'rgba(0,0,0,0.1)', borderRadius: '8px',
        }}>
          No custom providers configured. Click "Add Provider" to get started.
        </div>
      )}

      {/* Add/Edit Form */}
      {showForm && (
        <div style={{
          background: 'rgba(0,0,0,0.2)',
          border: '1px solid rgba(99,102,241,0.2)',
          borderRadius: '10px',
          padding: '20px',
        }}>
          <h3 style={{ margin: '0 0 16px 0', fontSize: '0.95rem', color: '#e2e8f0' }}>
            {editingKey ? `Edit: ${editingKey}` : 'New Provider'}
          </h3>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '14px' }}>
            <div>
              <label style={labelStyle}>Slug (CLI identifier) *</label>
              <input
                type="text"
                value={formSlug}
                onChange={e => setFormSlug(e.target.value)}
                placeholder="my-ollama"
                style={inputStyle}
                disabled={!!editingKey}
              />
            </div>
            <div>
              <label style={labelStyle}>Display Name</label>
              <input
                type="text"
                value={formName}
                onChange={e => setFormName(e.target.value)}
                placeholder="My Local Ollama"
                style={inputStyle}
              />
            </div>
            <div style={{ gridColumn: '1 / -1' }}>
              <label style={labelStyle}>Base API URL *</label>
              <input
                type="text"
                value={formApi}
                onChange={e => setFormApi(e.target.value)}
                placeholder="http://localhost:11434/v1"
                style={inputStyle}
              />
            </div>
            <div>
              <label style={labelStyle}>API Key Env Var</label>
              <input
                type="text"
                value={formKeyEnv}
                onChange={e => setFormKeyEnv(e.target.value)}
                placeholder="MY_API_KEY"
                style={inputStyle}
              />
            </div>
            <div>
              <label style={labelStyle}>Default Model</label>
              <input
                type="text"
                value={formDefaultModel}
                onChange={e => setFormDefaultModel(e.target.value)}
                placeholder="llama3.1"
                style={inputStyle}
              />
            </div>
            <div>
              <label style={labelStyle}>Transport Protocol</label>
              <select
                value={formTransport}
                onChange={e => setFormTransport(e.target.value)}
                style={{ ...inputStyle, fontFamily: 'inherit' }}
              >
                {TRANSPORT_OPTIONS.map(o => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </div>
          </div>

          <div style={{ display: 'flex', gap: '10px', marginTop: '18px', justifyContent: 'flex-end' }}>
            <button
              onClick={resetForm}
              style={{
                padding: '8px 16px', background: 'rgba(255,255,255,0.05)',
                border: '1px solid rgba(255,255,255,0.1)', color: '#94a3b8',
                borderRadius: '6px', cursor: 'pointer', fontSize: '0.85rem',
              }}
            >
              Cancel
            </button>
            <button
              onClick={handleSave}
              style={{
                padding: '8px 20px',
                background: 'linear-gradient(135deg, #6366f1, #8b5cf6)',
                color: '#fff', border: 'none', borderRadius: '6px',
                cursor: 'pointer', fontSize: '0.85rem', fontWeight: 600,
              }}
            >
              {editingKey ? 'Update Provider' : 'Add Provider'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
