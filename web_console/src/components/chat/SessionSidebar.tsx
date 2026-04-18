import { useEffect, useState } from 'react';
import { apiClient } from '../../lib/api';
import { toastStore } from '../../store/toastStore';

interface Session {
  session_id: string;
  source: string;
  created_at: number;
  updated_at: number;
  title?: string;
  summary?: string;
}

interface SessionSidebarProps {
  activeSessionId: string | null;
  onSelectSession: (sessionId: string) => void;
  onNewChat: () => void;
}

export function SessionSidebar({ activeSessionId, onSelectSession, onNewChat }: SessionSidebarProps) {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchSessions = async () => {
    try {
      const res = await apiClient.get<{ ok: boolean; sessions: Session[] }>('/sessions?limit=50');
      if (res.ok) {
        setSessions(res.sessions || []);
      }
    } catch (err) {
      toastStore.error('Sessions Load Failed', err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchSessions();
  }, [activeSessionId]);

  return (
    <div style={{
      width: '260px',
      borderRight: '1px solid rgba(255,255,255,0.08)',
      background: 'rgba(0,0,0,0.2)',
      display: 'flex',
      flexDirection: 'column',
      height: '100%',
      flexShrink: 0
    }}>
      <div style={{ padding: '16px' }}>
        <button
          onClick={onNewChat}
          style={{
            width: '100%',
            padding: '10px 16px',
            background: 'rgba(255,255,255,0.05)',
            border: '1px solid rgba(255,255,255,0.1)',
            borderRadius: '6px',
            color: '#f8fafc',
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: '8px',
            transition: 'background 0.2s',
            fontWeight: 500
          }}
          onMouseOver={(e) => (e.currentTarget.style.background = 'rgba(255,255,255,0.1)')}
          onMouseOut={(e) => (e.currentTarget.style.background = 'rgba(255,255,255,0.05)')}
        >
          <span>✏️</span> New Chat
        </button>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: '0 12px 16px', display: 'flex', flexDirection: 'column', gap: '4px' }}>
        {loading ? (
          <div style={{ textAlign: 'center', padding: '24px', color: '#64748b', fontSize: '0.875rem' }}>Loading...</div>
        ) : sessions.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '24px', color: '#64748b', fontSize: '0.875rem' }}>No recent chats</div>
        ) : (
          sessions.map((s) => {
            const isActive = s.session_id === activeSessionId;
            const dateStr = s.updated_at ? new Date(s.updated_at * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) : '';
            return (
              <button
                key={s.session_id}
                onClick={() => onSelectSession(s.session_id)}
                style={{
                  width: '100%',
                  textAlign: 'left',
                  background: isActive ? 'rgba(99, 102, 241, 0.15)' : 'transparent',
                  border: 'none',
                  borderRadius: '6px',
                  padding: '10px 12px',
                  cursor: 'pointer',
                  transition: 'background 0.2s',
                  position: 'relative'
                }}
                onMouseOver={(e) => {
                  if (!isActive) e.currentTarget.style.background = 'rgba(255,255,255,0.05)';
                }}
                onMouseOut={(e) => {
                  if (!isActive) e.currentTarget.style.background = 'transparent';
                }}
              >
                <div style={{
                  color: isActive ? '#818cf8' : '#e2e8f0',
                  fontSize: '0.875rem',
                  fontWeight: isActive ? 600 : 400,
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  marginBottom: '4px'
                }}>
                  {s.title || s.summary || 'New Chat'}
                </div>
                <div style={{ color: '#64748b', fontSize: '0.75rem', display: 'flex', justifyContent: 'space-between' }}>
                  <span>{dateStr}</span>
                  <span style={{ textTransform: 'capitalize' }}>{s.source}</span>
                </div>
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}
