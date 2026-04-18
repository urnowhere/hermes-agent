import { useState } from 'react';
import { PersonalityPicker } from './PersonalityPicker';

export interface SettingField {
  id: string;
  label: string;
  type: 'text' | 'boolean' | 'number' | 'select' | 'textarea';
  value: string | boolean | number;
  options?: string[];
  placeholder?: string;
}

export interface SettingCategory {
  id: string;
  label: string;
  fields: SettingField[];
}

interface SettingsFormProps {
  categories: SettingCategory[];
  onSave?: (updates: Record<string, any>) => Promise<void>;
  authStatus?: Record<string, unknown> | null;
}

export function SettingsForm({ categories, onSave, authStatus }: SettingsFormProps) {
  const [editValues, setEditValues] = useState<Record<string, any>>({});
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [activeTab, setActiveTab] = useState(categories[0]?.id || '');

  const hasChanges = Object.keys(editValues).length > 0;

  const getValue = (field: SettingField) => editValues[field.id] !== undefined ? editValues[field.id] : field.value;

  const handleChange = (id: string, value: any, original: any) => {
    if (value === original) {
      setEditValues((prev) => {
        const next = { ...prev };
        delete next[id];
        return next;
      });
    } else {
      setEditValues((prev) => ({ ...prev, [id]: value }));
    }
    setSaved(false);
  };

  const handleSave = async () => {
    if (!onSave || !hasChanges) return;
    setSaving(true);
    try {
      await onSave(editValues);
      setEditValues({});
      setSaved(true);
    } finally {
      setSaving(false);
    }
  };

  const handleReset = () => {
    setEditValues({});
    setSaved(false);
  };

  const inputStyle: React.CSSProperties = {
    padding: '10px 14px',
    borderRadius: '10px',
    border: '1px solid rgba(129, 140, 248, 0.3)',
    background: 'rgba(0, 0, 0, 0.2)',
    color: 'white',
    fontSize: '0.9rem',
    width: '100%',
  };

  const tabStyle = (isActive: boolean): React.CSSProperties => ({
    padding: '8px 16px',
    cursor: 'pointer',
    borderBottom: isActive ? '2px solid #818cf8' : '2px solid transparent',
    color: isActive ? '#e2e8f0' : '#64748b',
    fontWeight: isActive ? 600 : 400,
    fontSize: '0.9rem',
    background: 'transparent',
    borderTop: 'none',
    borderLeft: 'none',
    borderRight: 'none',
  });

  const activeCategory = categories.find((c) => c.id === activeTab) || categories[0];

  return (
    <section style={{
      background: 'rgba(255, 255, 255, 0.03)',
      border: '1px solid rgba(255, 255, 255, 0.06)',
      borderRadius: '16px',
      padding: '20px',
    }} aria-label="Settings form">
      <div style={{ marginBottom: '16px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h2 style={{ margin: 0, color: '#e2e8f0', fontSize: '1.1rem' }}>⚙️ Settings</h2>
          <p style={{ margin: '4px 0 0', color: '#64748b', fontSize: '0.85rem' }}>Configuration schema.</p>
        </div>
        <div style={{ display: 'flex', gap: '8px' }}>
          <button
            type="button"
            onClick={handleSave}
            disabled={!hasChanges || saving}
            style={{
              padding: '6px 16px', borderRadius: '8px', cursor: hasChanges ? 'pointer' : 'default',
              background: hasChanges ? 'rgba(129, 140, 248, 0.2)' : 'rgba(255, 255, 255, 0.04)',
              border: `1px solid ${hasChanges ? 'rgba(129, 140, 248, 0.4)' : 'rgba(255, 255, 255, 0.1)'}`,
              color: hasChanges ? '#a5b4fc' : '#475569', fontSize: '0.85rem',
            }}
          >
            {saving ? 'Saving…' : saved ? '✓ Saved' : 'Save'}
          </button>
          <button
            type="button"
            onClick={handleReset}
            disabled={!hasChanges}
            style={{
              padding: '6px 16px', borderRadius: '8px', cursor: hasChanges ? 'pointer' : 'default',
              background: 'rgba(255, 255, 255, 0.04)',
              border: '1px solid rgba(255, 255, 255, 0.1)',
              color: hasChanges ? '#94a3b8' : '#475569', fontSize: '0.85rem',
            }}
          >
            Reset
          </button>
        </div>
      </div>

      <div style={{ display: 'flex', gap: '12px', borderBottom: '1px solid rgba(255,255,255,0.1)', marginBottom: '20px', overflowX: 'auto', whiteSpace: 'nowrap' }}>
        {categories.map((cat) => (
          <button
            key={cat.id}
            type="button"
            onClick={() => setActiveTab(cat.id)}
            style={tabStyle(activeTab === cat.id)}
          >
            {cat.label}
          </button>
        ))}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: '16px', marginBottom: '24px' }}>
        {activeCategory?.fields.map((field) => {
          const val = getValue(field);
          const isEdited = editValues[field.id] !== undefined;
          const borderColor = isEdited ? 'rgba(129, 140, 248, 0.6)' : 'rgba(255, 255, 255, 0.1)';

          if (field.id === 'display.personality') {
            return (
              <div key={field.id} style={{ display: 'flex', flexDirection: 'column', gap: '6px', gridColumn: '1 / -1', marginBottom: '12px' }}>
                <span style={{ color: '#94a3b8', fontSize: '0.85rem', fontWeight: 600 }}>{field.label}</span>
                <PersonalityPicker 
                  value={String(val)} 
                  options={field.options} 
                  onChange={(newVal) => handleChange(field.id, newVal, field.value)} 
                />
              </div>
            );
          }

          return (
            <label key={field.id} style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
              <span style={{ color: '#94a3b8', fontSize: '0.8rem', fontWeight: 500 }}>{field.label}</span>
              {field.type === 'boolean' ? (
                <div style={{ display: 'flex', alignItems: 'center', height: '40px' }}>
                  <input
                    type="checkbox"
                    checked={Boolean(val)}
                    onChange={(e) => handleChange(field.id, e.target.checked, field.value)}
                    style={{ width: '18px', height: '18px', cursor: 'pointer', accentColor: '#818cf8' }}
                  />
                </div>
              ) : field.type === 'select' && field.options ? (
                <select
                  value={String(val)}
                  onChange={(e) => handleChange(field.id, e.target.value, field.value)}
                  style={{ ...inputStyle, borderColor, cursor: 'pointer' }}
                >
                  {field.options.map((opt) => (
                    <option key={opt} value={opt} style={{ background: '#0f172a' }}>{opt}</option>
                  ))}
                </select>
              ) : field.type === 'textarea' ? (
                <textarea
                  value={String(val)}
                  placeholder={field.placeholder || ''}
                  onChange={(e) => handleChange(field.id, e.target.value, field.value)}
                  style={{ ...inputStyle, borderColor, fontFamily: 'monospace', fontSize: '0.8rem', minHeight: '80px', resize: 'vertical' }}
                />
              ) : (
                <input
                  type={field.type === 'number' ? 'number' : 'text'}
                  value={String(val)}
                  onChange={(e) => {
                    const newVal = field.type === 'number' ? Number(e.target.value) : e.target.value;
                    handleChange(field.id, newVal, field.value);
                  }}
                  style={{ ...inputStyle, borderColor }}
                />
              )}
            </label>
          );
        })}
      </div>

      {authStatus && (
        <div style={{
          padding: '12px 16px',
          background: 'rgba(34, 197, 94, 0.08)',
          border: '1px solid rgba(34, 197, 94, 0.2)',
          borderRadius: '12px',
          fontSize: '0.85rem',
          color: '#94a3b8',
        }}>
          <strong style={{ color: '#86efac' }}>🔐 Auth Status</strong>
          <div style={{ marginTop: '6px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {Object.entries(authStatus).map(([key, val]) => (
              <div key={key} style={{ display: 'flex', flexDirection: 'column' }}>
                <span style={{ color: '#64748b' }}>{key}:</span>
                <span style={{ color: '#cbd5e1', fontSize: '0.8rem', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                  {typeof val === 'object' ? JSON.stringify(val, null, 2) : String(val)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
