import { useState } from 'react';

interface MemoryItem {
  id: string;
  title: string;
  body: string;
}

interface MemoryListProps {
  title: string;
  description: string;
  items: MemoryItem[];
  onAdd?: (content: string) => Promise<void>;
  onEdit?: (oldText: string, newText: string) => Promise<void>;
  onDelete?: (text: string) => Promise<void>;
}

export function MemoryList({ title, description, items, onAdd, onEdit, onDelete }: MemoryListProps) {
  const [newContent, setNewContent] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editText, setEditText] = useState('');
  const [isAdding, setIsAdding] = useState(false);

  const handleAdd = async () => {
    if (!newContent.trim() || !onAdd) return;
    await onAdd(newContent.trim());
    setNewContent('');
    setIsAdding(false);
  };

  const handleEdit = async (item: MemoryItem) => {
    if (!editText.trim() || !onEdit) return;
    await onEdit(item.body, editText.trim());
    setEditingId(null);
    setEditText('');
  };

  const handleDelete = async (item: MemoryItem) => {
    if (!onDelete) return;
    await onDelete(item.body);
  };

  const cardStyle: React.CSSProperties = {
    background: 'rgba(255, 255, 255, 0.03)',
    border: '1px solid rgba(255, 255, 255, 0.06)',
    borderRadius: '16px',
    padding: '20px',
  };

  const itemStyle: React.CSSProperties = {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    gap: '12px',
    padding: '12px 16px',
    background: 'rgba(255, 255, 255, 0.04)',
    borderRadius: '12px',
    marginBottom: '8px',
  };

  const btnStyle: React.CSSProperties = {
    background: 'rgba(255, 255, 255, 0.06)',
    border: '1px solid rgba(255, 255, 255, 0.1)',
    color: '#a5b4fc',
    padding: '4px 10px',
    borderRadius: '8px',
    cursor: 'pointer',
    fontSize: '0.8rem',
  };

  return (
    <section style={cardStyle} aria-label={title}>
      <div style={{ marginBottom: '16px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h2 style={{ margin: 0, color: '#e2e8f0', fontSize: '1.1rem' }}>{title}</h2>
          {onAdd && (
            <button
              type="button"
              onClick={() => setIsAdding(!isAdding)}
              style={{ ...btnStyle, background: 'rgba(129, 140, 248, 0.15)', borderColor: 'rgba(129, 140, 248, 0.3)' }}
            >
              {isAdding ? '✕ Cancel' : '+ Add'}
            </button>
          )}
        </div>
        <p style={{ margin: '4px 0 0', color: '#64748b', fontSize: '0.85rem' }}>{description}</p>
      </div>

      {/* Add form */}
      {isAdding && (
        <div style={{ display: 'flex', gap: '8px', marginBottom: '12px' }}>
          <input
            type="text"
            value={newContent}
            onChange={(e) => setNewContent(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') handleAdd(); }}
            placeholder="New memory entry..."
            style={{ flex: 1, padding: '10px 14px', borderRadius: '10px', border: '1px solid rgba(129, 140, 248, 0.3)', background: 'rgba(0, 0, 0, 0.2)', color: 'white', fontSize: '0.9rem' }}
          />
          <button type="button" onClick={handleAdd} style={{ ...btnStyle, background: 'rgba(34, 197, 94, 0.2)', color: '#86efac', borderColor: 'rgba(34, 197, 94, 0.3)', padding: '10px 16px' }}>
            Save
          </button>
        </div>
      )}

      {/* Items list */}
      <div>
        {items.map((item) => (
          <div key={item.id} style={itemStyle}>
            {editingId === item.id ? (
              <div style={{ flex: 1, display: 'flex', gap: '8px' }}>
                <input
                  type="text"
                  value={editText}
                  onChange={(e) => setEditText(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') handleEdit(item); }}
                  style={{ flex: 1, padding: '8px 12px', borderRadius: '8px', border: '1px solid rgba(129, 140, 248, 0.3)', background: 'rgba(0, 0, 0, 0.2)', color: 'white', fontSize: '0.85rem' }}
                />
                <button type="button" onClick={() => handleEdit(item)} style={{ ...btnStyle, color: '#86efac' }}>✓</button>
                <button type="button" onClick={() => setEditingId(null)} style={btnStyle}>✕</button>
              </div>
            ) : (
              <>
                <div style={{ flex: 1 }}>
                  <strong style={{ color: '#94a3b8', fontSize: '0.8rem', display: 'block', marginBottom: '4px' }}>{item.title}</strong>
                  <span style={{ color: '#cbd5e1', fontSize: '0.9rem', lineHeight: 1.5 }}>{item.body}</span>
                </div>
                <div style={{ display: 'flex', gap: '4px', flexShrink: 0 }}>
                  {onEdit && (
                    <button type="button" onClick={() => { setEditingId(item.id); setEditText(item.body); }} style={btnStyle} title="Edit">✏️</button>
                  )}
                  {onDelete && (
                    <button type="button" onClick={() => handleDelete(item)} style={{ ...btnStyle, color: '#fca5a5' }} title="Delete">🗑️</button>
                  )}
                </div>
              </>
            )}
          </div>
        ))}
        {items.length === 0 && (
          <p style={{ color: '#475569', textAlign: 'center', padding: '16px', fontSize: '0.9rem' }}>No items yet.</p>
        )}
      </div>
    </section>
  );
}
