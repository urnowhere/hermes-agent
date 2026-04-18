import { useEffect, useState } from 'react';
import { openSessionEventStream, type GuiEvent } from '../../lib/events';

interface TodoItem {
  id: string;
  content: string;
  status: 'pending' | 'in_progress' | 'completed' | 'cancelled';
}

const STATUS_ICONS: Record<string, string> = {
  pending: '○',
  in_progress: '◔',
  completed: '●',
  cancelled: '✗',
};

const STATUS_COLORS: Record<string, string> = {
  pending: '#94a3b8',
  in_progress: '#3b82f6',
  completed: '#22c55e',
  cancelled: '#ef4444',
};

/**
 * TodoPanel listens to the SSE event stream and captures `tool.completed`
 * events from the `todo` tool. Every time the agent calls `todo`, the result
 * payload contains the full current list which we parse and display.
 *
 * This avoids the need for a separate backend route since TodoStore is
 * transient per AIAgent instance — the event stream IS the source of truth.
 */
export function TodoPanel() {
  const [todos, setTodos] = useState<TodoItem[]>([]);
  const [sessionId, setSessionId] = useState<string>('current');
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  // Listen for session id from ChatPage
  useEffect(() => {
    const handleSync = (e: any) => {
      if (e.detail?.sessionId) {
        setSessionId(e.detail.sessionId);
      }
    };
    window.addEventListener('hermes-session-sync', handleSync);
    window.dispatchEvent(new CustomEvent('hermes-run-request-sync'));
    return () => window.removeEventListener('hermes-session-sync', handleSync);
  }, []);

  // Subscribe to SSE and capture todo tool results
  useEffect(() => {
    const sub = openSessionEventStream(sessionId, (event: GuiEvent) => {
      // Look for completed todo tool calls
      if (event.type === 'tool.completed' && event.payload.tool_name === 'todo') {
        try {
          // The result_preview or result contains the JSON output from todo_tool
          const raw = String(event.payload.result ?? event.payload.result_preview ?? '');
          const parsed = JSON.parse(raw);
          if (parsed.todos && Array.isArray(parsed.todos)) {
            setTodos(parsed.todos);
            setLastUpdated(new Date());
          }
        } catch {
          // result might be truncated; ignore parse failures
        }
      }
    });

    return () => sub.close();
  }, [sessionId]);

  const summary = {
    pending: todos.filter(t => t.status === 'pending').length,
    in_progress: todos.filter(t => t.status === 'in_progress').length,
    completed: todos.filter(t => t.status === 'completed').length,
    cancelled: todos.filter(t => t.status === 'cancelled').length,
  };

  if (todos.length === 0) {
    return (
      <div style={{ padding: '24px', color: '#94a3b8', textAlign: 'center' }}>
        <div style={{ fontSize: '2rem', marginBottom: '16px' }}>📋</div>
        <p>No task list yet.</p>
        <p style={{ fontSize: '0.85rem' }}>The agent will create a task list when working on complex, multi-step problems.</p>
      </div>
    );
  }

  return (
    <div style={{ padding: '16px', color: '#f8fafc', display: 'flex', flexDirection: 'column', gap: '16px', overflowY: 'auto', height: '100%' }}>
      {/* Summary bar */}
      <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
        {Object.entries(summary).map(([status, count]) => (
          count > 0 && (
            <div key={status} style={{ 
              padding: '4px 10px', 
              borderRadius: '12px', 
              fontSize: '0.75rem', 
              fontWeight: 600,
              background: `${STATUS_COLORS[status]}15`,
              color: STATUS_COLORS[status],
            }}>
              {count} {status.replace('_', ' ')}
            </div>
          )
        ))}
      </div>

      {lastUpdated && (
        <div style={{ fontSize: '0.7rem', color: '#64748b' }}>
          Last updated: {lastUpdated.toLocaleTimeString()}
        </div>
      )}

      {/* Todo items */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
        {todos.map((item) => (
          <div 
            key={item.id} 
            style={{ 
              display: 'flex', 
              alignItems: 'flex-start', 
              gap: '10px',
              padding: '10px 12px',
              background: item.status === 'in_progress' ? 'rgba(59, 130, 246, 0.08)' : 'rgba(255,255,255,0.03)',
              borderRadius: '6px',
              border: `1px solid ${item.status === 'in_progress' ? 'rgba(59, 130, 246, 0.2)' : 'rgba(255,255,255,0.05)'}`,
              opacity: item.status === 'cancelled' ? 0.5 : 1,
            }}
          >
            <span style={{ 
              color: STATUS_COLORS[item.status] || '#94a3b8', 
              fontSize: '1rem', 
              lineHeight: '1.4',
              flexShrink: 0,
            }}>
              {STATUS_ICONS[item.status] || '?'}
            </span>
            <div style={{ flex: 1 }}>
              <div style={{ 
                fontSize: '0.875rem', 
                color: item.status === 'completed' || item.status === 'cancelled' ? '#64748b' : '#e2e8f0',
                textDecoration: item.status === 'cancelled' ? 'line-through' : 'none',
                lineHeight: 1.4,
              }}>
                {item.content}
              </div>
              <div style={{ fontSize: '0.7rem', color: '#475569', marginTop: '4px' }}>
                {item.id}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
