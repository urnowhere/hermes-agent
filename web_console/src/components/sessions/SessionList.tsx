import { useState } from 'react';

interface SessionListProps {
  sessions: Array<{ id: string; title: string; subtitle: string }>;
  selectedId: string;
  onSelect(id: string): void;
  onRename?: (id: string, newTitle: string) => Promise<void>;
  onResume?: (id: string) => Promise<void>;
  onDelete?: (id: string) => Promise<void>;
}

export function SessionList({ sessions, selectedId, onSelect, onRename, onResume, onDelete }: SessionListProps) {
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameText, setRenameText] = useState('');

  const btnStyle: React.CSSProperties = {
    background: 'rgba(255, 255, 255, 0.06)',
    border: '1px solid rgba(255, 255, 255, 0.1)',
    color: '#a5b4fc',
    padding: '3px 8px',
    borderRadius: '6px',
    cursor: 'pointer',
    fontSize: '0.75rem',
  };

  return (
    <section style={{
      background: 'rgba(255, 255, 255, 0.03)',
      border: '1px solid rgba(255, 255, 255, 0.06)',
      borderRadius: '16px',
      padding: '16px',
      minWidth: '260px',
    }} aria-label="Session list">
      <div style={{ marginBottom: '12px' }}>
        <h2 style={{ margin: 0, color: '#e2e8f0', fontSize: '1.05rem' }}>💬 Sessions</h2>
        <p style={{ margin: '4px 0 0', color: '#64748b', fontSize: '0.8rem' }}>Browse recent Hermes conversations.</p>
      </div>
      <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: '6px' }}>
        {sessions.map((session) => (
          <li key={session.id}>
            <div
              style={{
                padding: '10px 12px',
                borderRadius: '12px',
                cursor: 'pointer',
                background: selectedId === session.id ? 'rgba(129, 140, 248, 0.12)' : 'rgba(255, 255, 255, 0.02)',
                border: `1px solid ${selectedId === session.id ? 'rgba(129, 140, 248, 0.3)' : 'transparent'}`,
                transition: 'all 0.15s',
              }}
              onClick={() => onSelect(session.id)}
            >
              {renamingId === session.id ? (
                <div style={{ display: 'flex', gap: '6px' }} onClick={(e) => e.stopPropagation()}>
                  <input
                    type="text"
                    value={renameText}
                    onChange={(e) => setRenameText(e.target.value)}
                    onKeyDown={async (e) => {
                      if (e.key === 'Enter' && onRename) {
                        await onRename(session.id, renameText);
                        setRenamingId(null);
                      }
                    }}
                    style={{ flex: 1, padding: '4px 8px', borderRadius: '6px', border: '1px solid rgba(129,140,248,0.3)', background: 'rgba(0,0,0,0.2)', color: 'white', fontSize: '0.85rem' }}
                    autoFocus
                  />
                  <button type="button" onClick={async () => { if (onRename) await onRename(session.id, renameText); setRenamingId(null); }} style={{ ...btnStyle, color: '#86efac' }}>✓</button>
                  <button type="button" onClick={() => setRenamingId(null)} style={btnStyle}>✕</button>
                </div>
              ) : (
                <>
                  <strong style={{ color: '#e2e8f0', fontSize: '0.9rem', display: 'block' }}>{session.title}</strong>
                  <span style={{ color: '#64748b', fontSize: '0.75rem' }}>{session.subtitle}</span>
                  {selectedId === session.id && (
                    <div style={{ display: 'flex', gap: '4px', marginTop: '8px' }} onClick={(e) => e.stopPropagation()}>
                      {onRename && (
                        <button type="button" onClick={() => { setRenamingId(session.id); setRenameText(session.title); }} style={btnStyle} title="Rename">✏️</button>
                      )}
                      {onResume && (
                        <button type="button" onClick={() => onResume(session.id)} style={{ ...btnStyle, color: '#86efac' }} title="Resume">▶ Resume</button>
                      )}
                      {onDelete && (
                        <button type="button" onClick={() => onDelete(session.id)} style={{ ...btnStyle, color: '#fca5a5' }} title="Delete">🗑️</button>
                      )}
                    </div>
                  )}
                </>
              )}
            </div>
          </li>
        ))}
        {sessions.length === 0 && (
          <p style={{ color: '#475569', textAlign: 'center', padding: '16px', fontSize: '0.85rem' }}>No sessions yet.</p>
        )}
      </ul>
    </section>
  );
}
