import { useEffect, useState, useCallback } from 'react';
import { apiClient } from '../lib/api';
import { toastStore } from '../store/toastStore';
import { EmptyState } from '../components/shared/EmptyState';

interface MemoryUsage {
  text: string;
  percent: number | null;
  current_chars: number | null;
  char_limit: number | null;
}

interface MemoryPayload {
  target: string;
  enabled: boolean;
  entries: string[];
  entry_count: number;
  usage: MemoryUsage;
  path: string;
}

interface MemoryResponse {
  ok: boolean;
  memory?: MemoryPayload;
  user_profile?: MemoryPayload;
}

type MemoryTarget = 'memory' | 'user';

export function MemoryPage() {
  const [memory, setMemory] = useState<MemoryPayload | null>(null);
  const [userProfile, setUserProfile] = useState<MemoryPayload | null>(null);
  const [activeTab, setActiveTab] = useState<MemoryTarget>('memory');
  const [loading, setLoading] = useState(true);
  const [newEntry, setNewEntry] = useState('');
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [editContent, setEditContent] = useState('');

  const loadAll = useCallback(async () => {
    setLoading(true);
    try {
      const [memRes, profileRes] = await Promise.all([
        apiClient.get<MemoryResponse>('/memory').catch(() => null),
        apiClient.get<MemoryResponse>('/user-profile').catch(() => null),
      ]);
      if (memRes?.ok && memRes.memory) setMemory(memRes.memory);
      if (profileRes?.ok && profileRes.user_profile) setUserProfile(profileRes.user_profile);
    } catch (err) {
      toastStore.error('Failed to load memory', err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadAll(); }, [loadAll]);

  const activePayload = activeTab === 'memory' ? memory : userProfile;

  const handleAdd = async () => {
    if (!newEntry.trim()) return;
    try {
      const res = await apiClient.post<MemoryResponse>('/memory', { target: activeTab, content: newEntry.trim() });
      if (res.ok) {
        toastStore.success('Entry added');
        setNewEntry('');
        await loadAll();
      }
    } catch (err) {
      toastStore.error('Failed to add entry', err instanceof Error ? err.message : String(err));
    }
  };

  const handleRemove = async (entry: string) => {
    try {
      const res = await apiClient.del<MemoryResponse>('/memory', { target: activeTab, old_text: entry });
      if (res.ok) {
        toastStore.success('Entry removed');
        await loadAll();
      }
    } catch (err) {
      toastStore.error('Failed to remove entry', err instanceof Error ? err.message : String(err));
    }
  };

  const handleSaveEdit = async (originalEntry: string) => {
    if (!editContent.trim()) return;
    try {
      const res = await apiClient.patch<MemoryResponse>('/memory', {
        target: activeTab,
        old_text: originalEntry,
        content: editContent.trim(),
      });
      if (res.ok) {
        toastStore.success('Entry updated');
        setEditingIndex(null);
        setEditContent('');
        await loadAll();
      }
    } catch (err) {
      toastStore.error('Failed to update entry', err instanceof Error ? err.message : String(err));
    }
  };

  if (loading) {
    return <EmptyState title="Loading Memory..." description="Fetching core memories from DB..." icon="🧠" />;
  }

  const usage = activePayload?.usage;
  const entries = activePayload?.entries ?? [];
  const enabled = activePayload?.enabled ?? true;

  const tabStyle = (tab: MemoryTarget): React.CSSProperties => ({
    padding: '8px 18px',
    borderRadius: '10px',
    cursor: 'pointer',
    fontSize: '0.85rem',
    fontWeight: 600,
    border: 'none',
    background: activeTab === tab ? 'rgba(99, 102, 241, 0.2)' : 'transparent',
    color: activeTab === tab ? '#a5b4fc' : '#64748b',
    transition: 'all 0.2s ease',
  });

  const btnStyle: React.CSSProperties = {
    padding: '4px 10px',
    borderRadius: '6px',
    cursor: 'pointer',
    fontSize: '0.75rem',
    border: '1px solid rgba(255,255,255,0.08)',
    background: 'rgba(255,255,255,0.04)',
    color: '#94a3b8',
  };

  return (
    <div className="layout-container" style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: '2rem', overflow: 'hidden' }}>
      <header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '1rem', marginBottom: '1.5rem' }}>
        <div>
          <h2 style={{ fontSize: '1.5rem', fontWeight: 600, color: '#f8fafc', margin: 0, display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            <span>🧠</span> Agent Memory
          </h2>
          <p style={{ color: '#94a3b8', margin: '0.5rem 0 0', fontSize: '0.9rem' }}>
            What Hermes remembers about you and the workspace context across sessions.
          </p>
        </div>
        <div style={{ display: 'flex', gap: '4px', background: 'rgba(0,0,0,0.2)', borderRadius: '12px', padding: '3px' }}>
          <button type="button" onClick={() => setActiveTab('memory')} style={tabStyle('memory')}>
            📝 Memory
          </button>
          <button type="button" onClick={() => setActiveTab('user')} style={tabStyle('user')}>
            👤 User Profile
          </button>
        </div>
      </header>

      {/* Usage bar */}
      {usage && usage.percent !== null && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '16px',
          padding: '10px 16px', borderRadius: '12px',
          background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)',
        }}>
          <div style={{ flex: 1, height: '6px', borderRadius: '3px', background: 'rgba(255,255,255,0.08)', overflow: 'hidden' }}>
            <div style={{
              width: `${Math.min(usage.percent, 100)}%`,
              height: '100%',
              borderRadius: '3px',
              background: usage.percent > 80 ? '#f87171' : usage.percent > 50 ? '#fbbf24' : '#34d399',
              transition: 'width 0.4s ease',
            }} />
          </div>
          <span style={{ fontSize: '0.8rem', color: '#94a3b8', whiteSpace: 'nowrap' }}>
            {usage.current_chars?.toLocaleString()} / {usage.char_limit?.toLocaleString()} chars ({usage.percent}%)
          </span>
        </div>
      )}

      {/* Disabled warning */}
      {!enabled && (
        <div style={{
          padding: '12px 16px', borderRadius: '10px', marginBottom: '12px',
          background: 'rgba(251,191,36,0.08)', border: '1px solid rgba(251,191,36,0.2)',
          color: '#fde68a', fontSize: '0.85rem',
        }}>
          ⚠️ {activeTab === 'user' ? 'User profile' : 'Memory'} is disabled in config.yaml
        </div>
      )}

      {/* Add new entry */}
      <div style={{ display: 'flex', gap: '8px', marginBottom: '16px' }}>
        <input
          type="text"
          value={newEntry}
          onChange={(e) => setNewEntry(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleAdd()}
          placeholder={`Add a new ${activeTab === 'user' ? 'user profile' : 'memory'} entry...`}
          style={{
            flex: 1, padding: '10px 14px', borderRadius: '10px', fontSize: '0.9rem',
            background: 'rgba(0,0,0,0.3)', border: '1px solid rgba(255,255,255,0.08)',
            color: '#e2e8f0', outline: 'none',
          }}
        />
        <button
          type="button"
          onClick={handleAdd}
          disabled={!newEntry.trim()}
          style={{
            padding: '10px 18px', borderRadius: '10px', border: 'none', cursor: 'pointer',
            background: newEntry.trim() ? '#6366f1' : 'rgba(99,102,241,0.2)',
            color: newEntry.trim() ? '#fff' : '#64748b',
            fontWeight: 600, fontSize: '0.85rem', transition: 'all 0.2s ease',
          }}
        >
          + Add
        </button>
      </div>

      {/* Entry list */}
      <div style={{ flex: 1, overflow: 'auto', display: 'flex', flexDirection: 'column', gap: '6px' }}>
        {entries.length === 0 ? (
          <div style={{ padding: '40px', textAlign: 'center', color: '#64748b', fontStyle: 'italic' }}>
            No {activeTab === 'user' ? 'user profile' : 'memory'} entries yet. The agent will add facts here automatically during conversations.
          </div>
        ) : (
          entries.map((entry, index) => (
            <div
              key={`${activeTab}-${index}`}
              style={{
                display: 'flex', alignItems: 'flex-start', gap: '10px',
                padding: '12px 14px', borderRadius: '10px',
                background: 'rgba(0,0,0,0.15)', border: '1px solid rgba(255,255,255,0.04)',
                transition: 'background 0.15s ease',
              }}
            >
              {editingIndex === index ? (
                <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '8px' }}>
                  <textarea
                    value={editContent}
                    onChange={(e) => setEditContent(e.target.value)}
                    style={{
                      width: '100%', minHeight: '60px', padding: '8px 10px',
                      background: 'rgba(0,0,0,0.3)', border: '1px solid rgba(99,102,241,0.3)',
                      borderRadius: '8px', color: '#e2e8f0', fontFamily: 'monospace',
                      fontSize: '0.85rem', resize: 'vertical', outline: 'none',
                    }}
                  />
                  <div style={{ display: 'flex', gap: '6px' }}>
                    <button type="button" onClick={() => handleSaveEdit(entry)} style={{ ...btnStyle, color: '#86efac', borderColor: 'rgba(34,197,94,0.3)' }}>Save</button>
                    <button type="button" onClick={() => { setEditingIndex(null); setEditContent(''); }} style={btnStyle}>Cancel</button>
                  </div>
                </div>
              ) : (
                <>
                  <div style={{ flex: 1, color: '#e2e8f0', fontSize: '0.9rem', lineHeight: 1.5, fontFamily: 'monospace', whiteSpace: 'pre-wrap' }}>
                    {entry}
                  </div>
                  <div style={{ display: 'flex', gap: '4px', flexShrink: 0 }}>
                    <button
                      type="button"
                      onClick={() => { setEditingIndex(index); setEditContent(entry); }}
                      style={btnStyle}
                      title="Edit entry"
                    >
                      ✏️
                    </button>
                    <button
                      type="button"
                      onClick={() => handleRemove(entry)}
                      style={{ ...btnStyle, color: '#fca5a5', borderColor: 'rgba(239,68,68,0.2)' }}
                      title="Remove entry"
                    >
                      ✕
                    </button>
                  </div>
                </>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
