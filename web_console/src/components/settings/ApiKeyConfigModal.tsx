import { useState, useEffect } from 'react';
import { apiClient } from '../../lib/api';
import { toastStore } from '../../store/toastStore';
import { AuthSchemaResponse, AuthSchemaItem } from './ApiKeyManager';

interface ApiKeyConfigModalProps {
  onClose: () => void;
  onSaved: () => void;
  categories: string[];
}

export function ApiKeyConfigModal({ onClose, onSaved, categories }: ApiKeyConfigModalProps) {
  const [schema, setSchema] = useState<Record<string, AuthSchemaItem>>({});
  const [config, setConfig] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [showMasked, setShowMasked] = useState<Record<string, boolean>>({});
  const [showAdvanced, setShowAdvanced] = useState(false);

  useEffect(() => {
    async function loadSchema() {
      const res = await apiClient.get<AuthSchemaResponse>('/auth/schema');
      if (res?.ok && res.schema) {
        setSchema(res.schema);
        const initialConfig: Record<string, string> = {};
        for (const [key, item] of Object.entries(res.schema)) {
          initialConfig[key] = item.value || '';
        }
        setConfig(initialConfig);
      } else {
        toastStore.error('Failed to load API key configuration schema.');
      }
      setLoading(false);
    }
    loadSchema();
  }, []);

  const handleSave = async () => {
    setSaving(true);
    // Send only keys that have been modified and don't start with ***
    const payload: Record<string, string> = {};
    for (const [key, val] of Object.entries(config)) {
      if (val !== schema[key]?.value && !val.startsWith('***')) {
        payload[key] = val;
      }
    }
    
    if (Object.keys(payload).length === 0) {
      toastStore.info('No changes made.');
      setSaving(false);
      onClose();
      return;
    }

    const res = await apiClient.patch<any>('/auth/keys', payload);
    setSaving(false);
    if (res?.ok) {
      toastStore.success('API keys updated successfully.');
      onSaved();
    } else {
      toastStore.error(res?.error?.message || 'Failed to update API keys.');
    }
  };

  const handleFieldChange = (key: string, value: string) => {
    setConfig(prev => ({ ...prev, [key]: value }));
  };

  const toggleMask = (key: string) => {
    setShowMasked(prev => ({ ...prev, [key]: !prev[key] }));
  };

  const inputStyle = {
    width: '100%',
    padding: '8px 12px',
    borderRadius: '8px',
    border: '1px solid rgba(255, 255, 255, 0.1)',
    background: 'rgba(0, 0, 0, 0.2)',
    color: '#e2e8f0',
    fontSize: '0.9rem',
    marginBottom: '12px'
  };

  // Filter schema to only the categories we're editing
  const filteredFields = Object.entries(schema)
    .filter(([_, item]) => {
      if (!categories.includes(item.category || 'provider')) {
        return false;
      }
      return showAdvanced || !item.advanced;
    })
    .sort(([aKey], [bKey]) => aKey.localeCompare(bKey));

  const advancedCount = Object.values(schema).filter(
    item => categories.includes(item.category || 'provider') && item.advanced
  ).length;

  return (
    <div style={{
      position: 'fixed', top: 0, left: 0, width: '100vw', height: '100vh',
      background: 'rgba(0, 0, 0, 0.6)', backdropFilter: 'blur(4px)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100
    }}>
      <div style={{
        background: '#1e293b', border: '1px solid rgba(255, 255, 255, 0.1)',
        borderRadius: '16px', padding: '24px', width: '600px', maxWidth: '90vw',
        maxHeight: '85vh', display: 'flex', flexDirection: 'column'
      }}>
        <h3 style={{ margin: '0 0 16px', color: '#e2e8f0', textTransform: 'capitalize' }}>
          Configure API Keys
        </h3>

        {loading ? (
          <p style={{ color: '#94a3b8' }}>Loading schema...</p>
        ) : (
          <div style={{ overflowY: 'auto', paddingRight: '8px', flex: 1 }}>
            {advancedCount > 0 && (
              <div style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                gap: '12px',
                marginBottom: '16px',
                padding: '12px 14px',
                background: 'rgba(15, 23, 42, 0.55)',
                border: '1px solid rgba(148, 163, 184, 0.14)',
                borderRadius: '10px',
                color: '#cbd5e1',
                fontSize: '0.82rem',
              }}>
                <div>
                  Advanced provider fields include base URL overrides and less common providers such as NVIDIA NIM.
                </div>
                <button
                  type="button"
                  onClick={() => setShowAdvanced(prev => !prev)}
                  style={{
                    background: showAdvanced ? 'rgba(56, 189, 248, 0.16)' : 'rgba(255,255,255,0.05)',
                    border: `1px solid ${showAdvanced ? 'rgba(56, 189, 248, 0.35)' : 'rgba(255,255,255,0.1)'}`,
                    color: showAdvanced ? '#38bdf8' : '#cbd5e1',
                    borderRadius: '8px',
                    padding: '8px 12px',
                    cursor: 'pointer',
                    fontSize: '0.8rem',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {showAdvanced ? 'Hide Advanced' : `Show Advanced (${advancedCount})`}
                </button>
              </div>
            )}
            <form id="apikeyform" onSubmit={e => { e.preventDefault(); handleSave(); }}>
              {filteredFields.map(([key, field]) => (
                <div key={key} style={{ marginBottom: '16px' }}>
                  <label style={{ display: 'block', marginBottom: '6px', fontSize: '0.85rem', color: '#94a3b8' }}>
                    <strong>{field.prompt}</strong> ({key})
                  </label>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <input
                      type={field.password && !showMasked[key] ? 'password' : 'text'}
                      value={config[key] || ''}
                      onChange={e => handleFieldChange(key, e.target.value)}
                      style={{ ...inputStyle, marginBottom: 0, flex: 1 }}
                      placeholder={field.description || ''}
                      autoComplete="off"
                    />
                    {field.password && (
                      <button
                        type="button"
                        onClick={() => toggleMask(key)}
                        style={{
                          background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.1)',
                          color: '#cbd5e1', borderRadius: '8px', padding: '8px 12px', cursor: 'pointer',
                          fontSize: '0.8rem'
                        }}
                      >
                        {showMasked[key] ? 'Hide' : 'Reveal'}
                      </button>
                    )}
                  </div>
                  {field.url && (
                    <a href={field.url} target="_blank" rel="noreferrer" style={{ fontSize: '0.75rem', color: '#38bdf8', textDecoration: 'none', marginTop: '4px', display: 'inline-block' }}>
                      Get API Key ↗
                    </a>
                  )}
                </div>
              ))}
            </form>
          </div>
        )}
        
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '12px', marginTop: '20px', paddingTop: '16px', borderTop: '1px solid rgba(255,255,255,0.05)' }}>
          <button
            type="button"
            onClick={onClose}
            style={{
              padding: '8px 16px', borderRadius: '8px', background: 'transparent',
              border: '1px solid rgba(255, 255, 255, 0.1)', color: '#e2e8f0', cursor: 'pointer'
            }}
          >
            Cancel
          </button>
          <button
            type="submit"
            form="apikeyform"
            disabled={saving}
            style={{
              padding: '8px 16px', borderRadius: '8px', background: 'rgba(56, 189, 248, 0.2)',
              border: '1px solid rgba(56, 189, 248, 0.3)', color: '#38bdf8', cursor: 'pointer'
            }}
          >
            {saving ? 'Saving...' : 'Save Keys'}
          </button>
        </div>
      </div>
    </div>
  );
}
