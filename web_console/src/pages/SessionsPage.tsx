import { useEffect, useMemo, useState } from 'react';
import { apiClient } from '../lib/api';
import { SessionList } from '../components/sessions/SessionList';
import { SessionPreview } from '../components/sessions/SessionPreview';

interface ApiSessionSummary {
  session_id: string;
  title: string | null;
  source?: string;
  last_active?: number;
}

interface SessionsResponse {
  ok: boolean;
  sessions: ApiSessionSummary[];
}

interface SessionDetailResponse {
  ok: boolean;
  session: {
    title?: string | null;
    recap?: {
      preview?: string;
    };
  };
}

interface TranscriptResponse {
  ok: boolean;
  items: Array<{ role?: string; content?: string }>;
}

interface SessionSearchResponse {
  ok: boolean;
  search?: {
    results?: Array<{ session_id: string; snippet?: string; session_title?: string }>;
  };
}

export function SessionsPage() {
  const [sessions, setSessions] = useState<Array<{ id: string; title: string; subtitle: string; source: string }>>([]);
  const [selectedId, setSelectedId] = useState<string>('');
  const [summary, setSummary] = useState<string>('Select a session to view details.');
  const [transcript, setTranscript] = useState<string[]>([]);
  const [title, setTitle] = useState<string>('');
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<Array<{ id: string; title: string; snippet: string }>>([]);
  const [sourceFilter, setSourceFilter] = useState<'all' | 'cli' | 'web_console'>('all');

  const refreshSessions = async () => {
    try {
      const sourceParam = sourceFilter === 'all' ? '' : `?source=${sourceFilter}`;
      const response = await apiClient.get<SessionsResponse>(`/sessions${sourceParam}`);
      if (response.ok && response.sessions.length > 0) {
        const normalized = response.sessions.map((session) => ({
          id: session.session_id,
          title: session.title ?? session.session_id.slice(0, 12),
          subtitle: `Source: ${session.source ?? 'unknown'}`,
          source: session.source ?? 'unknown',
        }));
        setSessions(normalized);
        if (!selectedId || !normalized.find((s) => s.id === selectedId)) {
          setSelectedId(normalized[0].id);
        }
      } else {
        setSessions([]);
      }
    } catch {
      // keep existing state
    }
  };

  useEffect(() => {
    refreshSessions();
  }, [sourceFilter]);

  useEffect(() => {
    if (!selectedId) return;
    let active = true;

    Promise.all([
      apiClient.get<SessionDetailResponse>(`/sessions/${selectedId}`),
      apiClient.get<TranscriptResponse>(`/sessions/${selectedId}/transcript`)
    ])
      .then(([detail, transcriptResponse]) => {
        if (!active) return;
        if (detail.ok) {
          setTitle(detail.session.title ?? selectedId);
          setSummary(detail.session.recap?.preview ?? 'No summary available.');
        }
        if (transcriptResponse.ok) {
          setTranscript(
            transcriptResponse.items.map((item) => `${item.role ?? 'message'}: ${item.content ?? ''}`)
          );
        }
      })
      .catch(() => {
        // keep existing state
      });

    return () => {
      active = false;
    };
  }, [selectedId]);

  const handleRename = async (id: string, newTitle: string) => {
    await apiClient.post(`/sessions/${id}/title`, { title: newTitle });
    await refreshSessions();
  };

  const handleResume = async (id: string) => {
    await apiClient.post(`/sessions/${id}/resume`, {});
    // Navigate to chat with this session (using hash routing)
    window.location.hash = `#/chat/${id}`;
  };

  const handleDelete = async (id: string) => {
    await apiClient.del(`/sessions/${id}`);
    if (selectedId === id) {
      setSelectedId('');
      setTitle('');
      setSummary('Session deleted.');
      setTranscript([]);
    }
    await refreshSessions();
  };

  const handleSearch = async () => {
    if (!searchQuery.trim()) { setSearchResults([]); return; }
    try {
      const res = await apiClient.get<SessionSearchResponse>(`/session-search?q=${encodeURIComponent(searchQuery)}`);
      if (res.ok && res.search?.results) {
        setSearchResults(res.search.results.map((r) => ({
          id: r.session_id,
          title: r.session_title ?? r.session_id.slice(0, 12),
          snippet: r.snippet ?? '',
        })));
      }
    } catch {
      setSearchResults([]);
    }
  };

  return (
    <div className="sessions-layout">
      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', minWidth: '260px', overflowY: 'auto', minHeight: 0, paddingRight: '8px', paddingBottom: '20px' }}>
        {/* Source filter tabs */}
        <div style={{ display: 'flex', gap: '4px', background: 'rgba(255,255,255,0.03)', borderRadius: '10px', padding: '3px', border: '1px solid rgba(255,255,255,0.06)' }}>
          {([['all', '📋 All'], ['cli', '💻 CLI'], ['web_console', '🌐 Web']] as const).map(([value, label]) => (
            <button
              key={value}
              type="button"
              onClick={() => setSourceFilter(value as 'all' | 'cli' | 'web_console')}
              style={{
                flex: 1,
                padding: '6px 10px',
                borderRadius: '8px',
                border: 'none',
                cursor: 'pointer',
                fontSize: '0.8rem',
                fontWeight: 600,
                background: sourceFilter === value ? 'rgba(129, 140, 248, 0.2)' : 'transparent',
                color: sourceFilter === value ? '#a5b4fc' : '#64748b',
                transition: 'all 0.15s ease',
              }}
            >
              {label}
            </button>
          ))}
        </div>
        {/* Session search bar */}
        <div style={{ display: 'flex', gap: '6px' }}>
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') handleSearch(); }}
            placeholder="Search across sessions…"
            style={{ flex: 1, padding: '8px 12px', borderRadius: '10px', border: '1px solid rgba(129,140,248,0.2)', background: 'rgba(0,0,0,0.2)', color: 'white', fontSize: '0.85rem' }}
          />
          <button type="button" onClick={handleSearch} style={{ padding: '8px 12px', borderRadius: '10px', background: 'rgba(129,140,248,0.15)', border: '1px solid rgba(129,140,248,0.3)', color: '#a5b4fc', cursor: 'pointer', fontSize: '0.85rem' }}>🔍</button>
        </div>

        {searchResults.length > 0 && (
          <div style={{ background: 'rgba(129,140,248,0.06)', border: '1px solid rgba(129,140,248,0.15)', borderRadius: '12px', padding: '8px', maxHeight: '160px', overflow: 'auto' }}>
            <p style={{ margin: '0 0 6px', color: '#a5b4fc', fontSize: '0.75rem', fontWeight: 600 }}>🔍 Search Results</p>
            {searchResults.map((r) => (
              <div key={r.id} style={{ padding: '6px 8px', cursor: 'pointer', borderRadius: '6px', fontSize: '0.8rem', color: '#cbd5e1' }}
                onClick={() => { setSelectedId(r.id); setSearchResults([]); setSearchQuery(''); }}>
                <strong style={{ color: '#e2e8f0' }}>{r.title}</strong>
                <span style={{ color: '#64748b', display: 'block', fontSize: '0.75rem' }}>{r.snippet}</span>
              </div>
            ))}
          </div>
        )}

        <SessionList
          sessions={sessions}
          selectedId={selectedId}
          onSelect={setSelectedId}
          onRename={handleRename}
          onResume={handleResume}
          onDelete={handleDelete}
        />
      </div>
      <SessionPreview title={title} summary={summary} transcript={transcript} sessionId={selectedId} />
    </div>
  );
}
