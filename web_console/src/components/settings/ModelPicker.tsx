import { useEffect, useState, useCallback } from 'react';
import { apiClient } from '../../lib/api';
import { toastStore } from '../../store/toastStore';

/* ─── Types ────────────────────────────────────────────────────── */

interface ProviderCard {
  slug: string;
  name: string;
  is_current: boolean;
  is_user_defined: boolean;
  models: string[];
  total_models: number;
  source: string;
  api_url?: string;
}

interface CatalogResponse {
  ok: boolean;
  current_model: string;
  current_provider: string;
  current_provider_label: string;
  providers: ProviderCard[];
}

interface SwitchResponse {
  ok: boolean;
  new_model: string;
  provider: string;
  provider_label: string;
  provider_changed: boolean;
  is_global: boolean;
  context_window?: number;
  max_output?: number;
  cost?: string;
  capabilities?: string;
  cache_enabled?: boolean;
  warning?: string;
  resolved_via_alias?: string;
  error?: string;
}

/* ─── Component ────────────────────────────────────────────────── */

export function ModelPicker() {
  const [providers, setProviders] = useState<ProviderCard[]>([]);
  const [currentModel, setCurrentModel] = useState('');
  const [currentProvider, setCurrentProvider] = useState('');
  const [currentProviderLabel, setCurrentProviderLabel] = useState('');
  const [selectedProvider, setSelectedProvider] = useState<string | null>(null);
  const [customModel, setCustomModel] = useState('');
  const [persistGlobal, setPersistGlobal] = useState(true);
  const [switching, setSwitching] = useState(false);
  const [loading, setLoading] = useState(true);

  const fetchCatalog = useCallback(async () => {
    try {
      const res = await apiClient.get<CatalogResponse>('/models/catalog');
      if (res.ok) {
        setProviders(res.providers || []);
        setCurrentModel(res.current_model || '');
        setCurrentProvider(res.current_provider || '');
        setCurrentProviderLabel(res.current_provider_label || '');
      }
    } catch (err) {
      toastStore.error('Catalog Load Failed', err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchCatalog(); }, [fetchCatalog]);

  const handleSwitch = async (model: string, provider?: string) => {
    if (!model && !provider) return;
    setSwitching(true);
    try {
      const res = await apiClient.post<SwitchResponse>('/models/switch', {
        model: model || '',
        provider: provider || '',
        global: persistGlobal,
      });
      if (res.ok) {
        setCurrentModel(res.new_model);
        setCurrentProvider(res.provider);
        setCurrentProviderLabel(res.provider_label);

        const parts = [`Switched to ${res.new_model}`];
        if (res.provider_label) parts.push(`on ${res.provider_label}`);
        if (res.context_window) parts.push(`• ${(res.context_window / 1000).toFixed(0)}K context`);
        if (res.cost) parts.push(`• ${res.cost}`);
        if (res.cache_enabled) parts.push('• Prompt caching enabled');
        if (res.is_global) parts.push('• Saved to config');

        toastStore.success('Model Switched', parts.join('\n'));
        await fetchCatalog(); // refresh cards
      } else {
        toastStore.error('Switch Failed', res.error || 'Unknown error');
      }
    } catch (err) {
      toastStore.error('Switch Failed', err instanceof Error ? err.message : String(err));
    } finally {
      setSwitching(false);
    }
  };

  if (loading) {
    return <div style={{ padding: '24px', color: '#94a3b8' }}>Loading model catalog…</div>;
  }

  const activeProviderData = selectedProvider
    ? providers.find(p => p.slug === selectedProvider)
    : providers.find(p => p.is_current) || providers[0];

  return (
    <div style={{
      background: 'rgba(255,255,255,0.03)',
      border: '1px solid rgba(255,255,255,0.08)',
      borderRadius: '12px',
      padding: '24px',
      marginBottom: '32px',
    }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '20px' }}>
        <h2 style={{ margin: 0, fontSize: '1.25rem', color: '#f8fafc', display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span>🧠</span> Model & Provider
        </h2>
        <div style={{
          display: 'flex', alignItems: 'center', gap: '8px',
          padding: '6px 14px',
          background: 'rgba(56,189,248,0.1)',
          border: '1px solid rgba(56,189,248,0.2)',
          borderRadius: '20px',
          fontSize: '0.8rem',
          color: '#38bdf8',
        }}>
          <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: '#22c55e' }} />
          {currentModel || 'No model'} — {currentProviderLabel}
        </div>
      </div>

      {/* Provider Cards */}
      <label style={{ display: 'block', marginBottom: '10px', fontSize: '0.8rem', fontWeight: 600, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
        Authenticated Providers ({providers.length})
      </label>
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))',
        gap: '10px',
        marginBottom: '24px',
      }}>
        {providers.map(p => {
          const isSelected = activeProviderData?.slug === p.slug;
          return (
            <button
              key={p.slug}
              onClick={() => setSelectedProvider(p.slug)}
              style={{
                background: isSelected
                  ? 'rgba(56,189,248,0.12)'
                  : 'rgba(255,255,255,0.03)',
                border: `1px solid ${isSelected ? 'rgba(56,189,248,0.4)' : 'rgba(255,255,255,0.06)'}`,
                borderRadius: '10px',
                padding: '14px 16px',
                cursor: 'pointer',
                textAlign: 'left',
                transition: 'all 0.2s ease',
                outline: 'none',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '6px' }}>
                <span style={{ fontSize: '0.95rem', fontWeight: 600, color: isSelected ? '#38bdf8' : '#e2e8f0' }}>
                  {p.name}
                </span>
              </div>
              <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                {p.is_current && (
                  <span style={{
                    fontSize: '0.65rem', padding: '2px 6px', borderRadius: '4px',
                    background: 'rgba(34,197,94,0.15)', color: '#4ade80', fontWeight: 600,
                  }}>ACTIVE</span>
                )}
                {p.is_user_defined && (
                  <span style={{
                    fontSize: '0.65rem', padding: '2px 6px', borderRadius: '4px',
                    background: 'rgba(168,85,247,0.15)', color: '#c084fc', fontWeight: 600,
                  }}>CUSTOM</span>
                )}
                <span style={{
                  fontSize: '0.65rem', padding: '2px 6px', borderRadius: '4px',
                  background: 'rgba(255,255,255,0.06)', color: '#94a3b8',
                }}>
                  {p.total_models > 0 ? `${p.total_models} models` : p.api_url ? 'endpoint' : '—'}
                </span>
              </div>
            </button>
          );
        })}
      </div>

      {/* Model Selection for Active Provider */}
      {activeProviderData && (
        <div style={{
          background: 'rgba(0,0,0,0.15)',
          border: '1px solid rgba(255,255,255,0.06)',
          borderRadius: '10px',
          padding: '20px',
          marginBottom: '20px',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '14px' }}>
            <h3 style={{ margin: 0, fontSize: '1rem', color: '#e2e8f0' }}>
              {activeProviderData.name} Models
            </h3>
            <span style={{ fontSize: '0.75rem', color: '#64748b' }}>
              Click a model to switch instantly
            </span>
          </div>

          {activeProviderData.models.length > 0 ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', maxHeight: '280px', overflowY: 'auto' }}>
              {activeProviderData.models.map(modelId => {
                const isActive = modelId === currentModel && activeProviderData.slug === currentProvider;
                return (
                  <button
                    key={modelId}
                    onClick={() => handleSwitch(modelId, activeProviderData.slug)}
                    disabled={switching}
                    style={{
                      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                      padding: '10px 14px',
                      background: isActive ? 'rgba(56,189,248,0.08)' : 'rgba(255,255,255,0.02)',
                      border: `1px solid ${isActive ? 'rgba(56,189,248,0.25)' : 'rgba(255,255,255,0.04)'}`,
                      borderRadius: '8px',
                      cursor: switching ? 'wait' : 'pointer',
                      transition: 'all 0.15s ease',
                      outline: 'none',
                      opacity: switching ? 0.6 : 1,
                    }}
                  >
                    <span style={{
                      fontFamily: "'JetBrains Mono', monospace",
                      fontSize: '0.85rem',
                      color: isActive ? '#38bdf8' : '#cbd5e1',
                    }}>
                      {modelId}
                    </span>
                    {isActive && (
                      <span style={{
                        fontSize: '0.65rem', padding: '2px 8px', borderRadius: '4px',
                        background: 'rgba(34,197,94,0.15)', color: '#4ade80', fontWeight: 600,
                      }}>
                        CURRENT
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          ) : activeProviderData.api_url ? (
            <div style={{ color: '#64748b', fontSize: '0.875rem', fontStyle: 'italic' }}>
              Custom endpoint: <code style={{ background: 'rgba(0,0,0,0.3)', padding: '2px 6px', borderRadius: '4px' }}>
                {activeProviderData.api_url}
              </code>
            </div>
          ) : (
            <div style={{ color: '#64748b', fontSize: '0.875rem', fontStyle: 'italic' }}>
              No curated models available for this provider.
            </div>
          )}
        </div>
      )}

      {/* Custom Model Input */}
      <div style={{
        background: 'rgba(0,0,0,0.1)',
        border: '1px solid rgba(255,255,255,0.05)',
        borderRadius: '10px',
        padding: '16px 20px',
        marginBottom: '16px',
      }}>
        <label style={{ display: 'block', marginBottom: '8px', fontSize: '0.8rem', fontWeight: 600, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
          Or Enter Any Model ID
        </label>
        <div style={{ display: 'flex', gap: '10px', alignItems: 'stretch' }}>
          <input
            type="text"
            value={customModel}
            onChange={e => setCustomModel(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && customModel.trim()) handleSwitch(customModel.trim(), selectedProvider || ''); }}
            placeholder="e.g. sonnet, opus, google/gemma-3-27b-it"
            style={{
              flex: 1,
              padding: '10px 14px',
              background: 'rgba(0,0,0,0.3)',
              border: '1px solid rgba(255,255,255,0.08)',
              color: '#f8fafc',
              borderRadius: '8px',
              fontSize: '0.9rem',
              fontFamily: "'JetBrains Mono', monospace",
              outline: 'none',
            }}
          />
          <button
            onClick={() => { if (customModel.trim()) handleSwitch(customModel.trim(), selectedProvider || ''); }}
            disabled={switching || !customModel.trim()}
            style={{
              padding: '10px 20px',
              background: switching ? '#1e293b' : 'linear-gradient(135deg, #0ea5e9, #6366f1)',
              color: '#fff',
              border: 'none',
              borderRadius: '8px',
              fontSize: '0.85rem',
              fontWeight: 600,
              cursor: switching ? 'wait' : 'pointer',
              opacity: !customModel.trim() ? 0.4 : 1,
              transition: 'all 0.2s ease',
              whiteSpace: 'nowrap',
            }}
          >
            {switching ? '⏳ Switching…' : '⚡ Switch'}
          </button>
        </div>
      </div>

      {/* Persistence Toggle */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer', fontSize: '0.85rem', color: '#94a3b8' }}>
          <input
            type="checkbox"
            checked={persistGlobal}
            onChange={e => setPersistGlobal(e.target.checked)}
            style={{ accentColor: '#6366f1' }}
          />
          Save to config.yaml (global)
        </label>
        <span style={{ fontSize: '0.75rem', color: '#475569' }}>
          {persistGlobal ? 'Change persists across sessions' : 'Session-only — resets on restart'}
        </span>
      </div>
    </div>
  );
}
