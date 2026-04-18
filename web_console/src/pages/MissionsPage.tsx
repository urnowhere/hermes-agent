import { useState, useEffect, useRef } from 'react';
import { getBackendUrl } from '../store/backendStore';

interface KanbanCard {
  id: string;
  title: string;
  description: string;
  assignee?: string;
  priority: 'low' | 'medium' | 'high';
  sessionId?: string;
}

interface KanbanColumn {
  id: string;
  title: string;
  cards: KanbanCard[];
}

const DEFAULT_COLUMNS: KanbanColumn[] = [
  { id: 'backlog', title: '📋 Backlog', cards: [] },
  { id: 'in_progress', title: '🔄 In Progress', cards: [] },
  { id: 'review', title: '👁️ Review', cards: [] },
  { id: 'done', title: '✅ Done', cards: [] },
];

const PRIORITY_COLORS: Record<string, string> = {
  low: '#4ade80',
  medium: '#fbbf24',
  high: '#f87171',
};

export function MissionsPage() {
  const [columns, setColumns] = useState<KanbanColumn[]>(DEFAULT_COLUMNS);
  const [draggedCard, setDraggedCard] = useState<{ cardId: string; fromColumnId: string } | null>(null);
  const [showAddModal, setShowAddModal] = useState<string | null>(null);
  const [newTitle, setNewTitle] = useState('');
  const [newDesc, setNewDesc] = useState('');
  const [newPriority, setNewPriority] = useState<'low' | 'medium' | 'high'>('medium');
  const [isInitializing, setIsInitializing] = useState(true);
  const saveTimeoutRef = useRef<number | null>(null);

  useEffect(() => {
    fetch(`${getBackendUrl()}/api/gui/missions`)
      .then(res => res.json())
      .then(data => {
        if (data.ok && data.columns) {
          setColumns(data.columns);
        }
      })
      .catch(err => console.error("Failed to load missions:", err))
      .finally(() => setIsInitializing(false));
  }, []);

  const saveColumns = (newCols: KanbanColumn[]) => {
    setColumns(newCols);
    if (saveTimeoutRef.current) window.clearTimeout(saveTimeoutRef.current);
    saveTimeoutRef.current = window.setTimeout(() => {
      fetch(`${getBackendUrl()}/api/gui/missions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ columns: newCols })
      }).catch(err => console.error("Failed to save missions:", err));
    }, 500);
  };

  const addCard = (columnId: string) => {
    if (!newTitle.trim()) return;
    const card: KanbanCard = {
      id: `card-${Date.now()}`,
      title: newTitle.trim(),
      description: newDesc.trim(),
      priority: newPriority,
    };
    saveColumns(columns.map(col =>
      col.id === columnId ? { ...col, cards: [...col.cards, card] } : col
    ));
    setNewTitle('');
    setNewDesc('');
    setNewPriority('medium');
    setShowAddModal(null);
  };

  const removeCard = (columnId: string, cardId: string) => {
    saveColumns(columns.map(col =>
      col.id === columnId ? { ...col, cards: col.cards.filter(c => c.id !== cardId) } : col
    ));
  };

  const handleDragStart = (cardId: string, fromColumnId: string) => {
    setDraggedCard({ cardId, fromColumnId });
  };

  const handleDrop = (toColumnId: string) => {
    if (!draggedCard) return;
    const { cardId, fromColumnId } = draggedCard;
    if (fromColumnId === toColumnId) { setDraggedCard(null); return; }

    let movedCard: KanbanCard | undefined;
    const updated = columns.map(col => {
      if (col.id === fromColumnId) {
        const card = col.cards.find(c => c.id === cardId);
        if (card) movedCard = card;
        return { ...col, cards: col.cards.filter(c => c.id !== cardId) };
      }
      return col;
    });
    
    if (movedCard) {
      saveColumns(updated.map(col =>
        col.id === toColumnId ? { ...col, cards: [...col.cards, movedCard!] } : col
      ));
    } else {
      saveColumns(updated);
    }
    setDraggedCard(null);
  };

  return (
    <div style={{ padding: '20px', height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
        <h2 style={{ margin: 0, fontSize: '1.4rem', color: '#f4f4f5' }}>🎯 Missions Board</h2>
        <span style={{ fontSize: '0.8rem', color: '#64748b' }}>Drag cards between columns to update status</span>
      </div>

      <div style={{ display: 'flex', gap: '16px', flex: 1, overflowX: 'auto', paddingBottom: '16px', opacity: isInitializing ? 0.5 : 1, transition: 'opacity 0.2s' }}>
        {columns.map(col => (
          <div
            key={col.id}
            onDragOver={(e) => { e.preventDefault(); e.currentTarget.style.background = 'rgba(129,140,248,0.06)'; }}
            onDragLeave={(e) => { e.currentTarget.style.background = 'rgba(255,255,255,0.02)'; }}
            onDrop={(e) => { e.preventDefault(); e.currentTarget.style.background = 'rgba(255,255,255,0.02)'; handleDrop(col.id); }}
            style={{
              flex: '0 0 auto',
              width: '280px',
              background: 'rgba(255,255,255,0.02)',
              borderRadius: '14px',
              border: '1px solid rgba(255,255,255,0.06)',
              padding: '12px',
              display: 'flex',
              flexDirection: 'column',
              gap: '8px',
              transition: 'background 0.15s',
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '4px' }}>
              <span style={{ fontSize: '0.9rem', fontWeight: 600, color: '#e2e8f0' }}>{col.title}</span>
              <span style={{
                fontSize: '0.7rem',
                background: 'rgba(129,140,248,0.15)',
                color: '#a5b4fc',
                padding: '2px 8px',
                borderRadius: '10px',
                fontWeight: 600,
              }}>
                {col.cards.length}
              </span>
            </div>

            <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '8px', overflow: 'auto' }}>
              {col.cards.map(card => (
                <div
                  key={card.id}
                  draggable
                  onDragStart={() => handleDragStart(card.id, col.id)}
                  style={{
                    background: 'rgba(255,255,255,0.04)',
                    border: '1px solid rgba(255,255,255,0.08)',
                    borderRadius: '10px',
                    padding: '12px',
                    cursor: 'grab',
                    transition: 'transform 0.15s, box-shadow 0.15s',
                  }}
                  onMouseEnter={(e) => { e.currentTarget.style.transform = 'translateY(-1px)'; e.currentTarget.style.boxShadow = '0 4px 12px rgba(0,0,0,0.3)'; }}
                  onMouseLeave={(e) => { e.currentTarget.style.transform = 'none'; e.currentTarget.style.boxShadow = 'none'; }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '8px' }}>
                    <span style={{ fontSize: '0.85rem', fontWeight: 600, color: '#f4f4f5' }}>{card.title}</span>
                    <button
                      type="button"
                      onClick={() => removeCard(col.id, card.id)}
                      style={{ background: 'none', border: 'none', color: '#64748b', cursor: 'pointer', fontSize: '0.75rem', padding: '2px' }}
                    >✕</button>
                  </div>
                  {card.description && (
                    <p style={{ margin: '6px 0 0', fontSize: '0.78rem', color: '#94a3b8', lineHeight: 1.4 }}>{card.description}</p>
                  )}
                  <div style={{ marginTop: '8px', display: 'flex', gap: '6px', alignItems: 'center' }}>
                    <span style={{
                      fontSize: '0.65rem',
                      padding: '2px 8px',
                      borderRadius: '6px',
                      background: `${PRIORITY_COLORS[card.priority]}20`,
                      color: PRIORITY_COLORS[card.priority],
                      fontWeight: 600,
                      textTransform: 'uppercase',
                    }}>{card.priority}</span>
                    {card.sessionId && (
                      <span style={{ fontSize: '0.65rem', color: '#64748b' }}>🔗 {card.sessionId.slice(0, 8)}</span>
                    )}
                  </div>
                </div>
              ))}
            </div>

            {showAddModal === col.id ? (
              <div style={{ background: 'rgba(255,255,255,0.04)', borderRadius: '10px', padding: '10px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                <input
                  type="text"
                  value={newTitle}
                  onChange={(e) => setNewTitle(e.target.value)}
                  placeholder="Mission title..."
                  autoFocus
                  onKeyDown={(e) => { if (e.key === 'Enter') addCard(col.id); if (e.key === 'Escape') setShowAddModal(null); }}
                  style={{ padding: '8px', borderRadius: '8px', border: '1px solid rgba(255,255,255,0.1)', background: 'rgba(0,0,0,0.3)', color: '#f4f4f5', fontSize: '0.85rem' }}
                />
                <textarea
                  value={newDesc}
                  onChange={(e) => setNewDesc(e.target.value)}
                  placeholder="Description (optional)"
                  rows={2}
                  style={{ padding: '8px', borderRadius: '8px', border: '1px solid rgba(255,255,255,0.1)', background: 'rgba(0,0,0,0.3)', color: '#f4f4f5', fontSize: '0.8rem', resize: 'none' }}
                />
                <div style={{ display: 'flex', gap: '6px' }}>
                  <select
                    value={newPriority}
                    onChange={(e) => setNewPriority(e.target.value as 'low' | 'medium' | 'high')}
                    style={{ flex: 1, padding: '6px', borderRadius: '8px', border: '1px solid rgba(255,255,255,0.1)', background: 'rgba(0,0,0,0.3)', color: '#f4f4f5', fontSize: '0.8rem' }}
                  >
                    <option value="low">Low</option>
                    <option value="medium">Medium</option>
                    <option value="high">High</option>
                  </select>
                  <button
                    type="button"
                    onClick={() => addCard(col.id)}
                    style={{ padding: '6px 14px', borderRadius: '8px', background: 'rgba(129,140,248,0.2)', border: '1px solid rgba(129,140,248,0.3)', color: '#a5b4fc', cursor: 'pointer', fontWeight: 600, fontSize: '0.8rem' }}
                  >Add</button>
                  <button
                    type="button"
                    onClick={() => setShowAddModal(null)}
                    style={{ padding: '6px 10px', borderRadius: '8px', background: 'none', border: '1px solid rgba(255,255,255,0.1)', color: '#64748b', cursor: 'pointer', fontSize: '0.8rem' }}
                  >✕</button>
                </div>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => setShowAddModal(col.id)}
                style={{
                  padding: '8px',
                  borderRadius: '8px',
                  border: '1px dashed rgba(255,255,255,0.1)',
                  background: 'transparent',
                  color: '#64748b',
                  cursor: 'pointer',
                  fontSize: '0.8rem',
                  transition: 'all 0.15s',
                }}
                onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'rgba(129,140,248,0.3)'; e.currentTarget.style.color = '#a5b4fc'; }}
                onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'rgba(255,255,255,0.1)'; e.currentTarget.style.color = '#64748b'; }}
              >+ Add Mission</button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
