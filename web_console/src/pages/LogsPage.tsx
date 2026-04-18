import { useEffect, useState, useRef, useCallback } from 'react';
import { apiClient } from '../lib/api';

interface LogsApiResponse {
  ok: boolean;
  logs?: {
    directory: string;
    file: string | null;
    available_files: string[];
    line_count: number;
    lines: string[];
  };
}

const LEVEL_COLORS: Record<string, string> = {
  DEBUG: '#64748b',
  INFO: '#38bdf8',
  WARNING: '#fbbf24',
  ERROR: '#ef4444',
  CRITICAL: '#f43f5e',
};

const LOG_LEVELS = ['ALL', 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'] as const;

function getLineLevel(line: string): string | null {
  const match = line.match(/\s(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s/);
  return match ? match[1] : null;
}

function shouldShowLine(line: string, minLevel: string): boolean {
  if (minLevel === 'ALL') return true;
  const level = getLineLevel(line);
  if (!level) return true; // Show non-leveled lines always
  const order: Record<string, number> = { DEBUG: 0, INFO: 1, WARNING: 2, ERROR: 3, CRITICAL: 4 };
  return (order[level] ?? 0) >= (order[minLevel] ?? 0);
}

export function LogsPage() {
  const [lines, setLines] = useState<string[]>([]);
  const [availableFiles, setAvailableFiles] = useState<string[]>([]);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [minLevel, setMinLevel] = useState<string>('ALL');
  const [searchFilter, setSearchFilter] = useState<string>('');
  const [loading, setLoading] = useState(true);
  const [autoScroll, setAutoScroll] = useState(true);
  const containerRef = useRef<HTMLDivElement>(null);
  const isInitialLoad = useRef(true);

  const fetchLogs = useCallback(async (file?: string | null) => {
    try {
      const params = new URLSearchParams({ limit: '500' });
      if (file) params.set('file', file);
      const response = await apiClient.get<LogsApiResponse>(`/logs?${params}`);
      if (response.ok && response.logs) {
        setLines(response.logs.lines);
        if (isInitialLoad.current && response.logs.available_files.length > 0) {
          setAvailableFiles(response.logs.available_files);
          if (!file) setSelectedFile(response.logs.file);
          isInitialLoad.current = false;
        }
        setLoading(false);
      }
    } catch {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchLogs(selectedFile);
    const pollId = setInterval(() => fetchLogs(selectedFile), 4000);
    return () => clearInterval(pollId);
  }, [selectedFile, fetchLogs]);

  // Auto-scroll to bottom when new lines arrive
  useEffect(() => {
    if (autoScroll && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [lines, autoScroll]);

  const lowerSearch = searchFilter.toLowerCase();
  const filteredLines = lines.filter(line => {
    if (!shouldShowLine(line, minLevel)) return false;
    if (lowerSearch && !line.toLowerCase().includes(lowerSearch)) return false;
    return true;
  });

  const levelCounts = lines.reduce<Record<string, number>>((acc, line) => {
    const level = getLineLevel(line);
    if (level) acc[level] = (acc[level] || 0) + 1;
    return acc;
  }, {});

  if (loading && lines.length === 0) {
    return (
      <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#64748b' }}>
        Loading system logs…
      </div>
    );
  }

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* Toolbar */}
      <div style={{
        display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '10px',
        padding: '8px 16px',
        background: 'rgba(0,0,0,0.3)',
        borderBottom: '1px solid rgba(255,255,255,0.06)',
        flexShrink: 0,
      }}>
        {/* File selector */}
        <select
          value={selectedFile || ''}
          onChange={(e) => {
            setSelectedFile(e.target.value || null);
            setLoading(true);
            isInitialLoad.current = false;
          }}
          style={{
            background: 'rgba(255,255,255,0.06)',
            border: '1px solid rgba(255,255,255,0.1)',
            borderRadius: '6px',
            color: '#e2e8f0',
            padding: '4px 8px',
            fontSize: '0.78rem',
            fontFamily: "'JetBrains Mono', monospace",
            cursor: 'pointer',
            outline: 'none',
          }}
        >
          {availableFiles.map(f => (
            <option key={f} value={f} style={{ background: '#1e1e2e' }}>{f}</option>
          ))}
        </select>

        {/* Level filter */}
        <select
          value={minLevel}
          onChange={(e) => setMinLevel(e.target.value)}
          style={{
            background: 'rgba(255,255,255,0.06)',
            border: '1px solid rgba(255,255,255,0.1)',
            borderRadius: '6px',
            color: '#e2e8f0',
            padding: '4px 8px',
            fontSize: '0.78rem',
            cursor: 'pointer',
            outline: 'none',
          }}
        >
          {LOG_LEVELS.map(level => (
            <option key={level} value={level} style={{ background: '#1e1e2e' }}>
              {level === 'ALL' ? '🔍 All Levels' : `${level}${levelCounts[level] ? ` (${levelCounts[level]})` : ''}`}
            </option>
          ))}
        </select>

        {/* Text search filter */}
        <input
          type="text"
          placeholder="🔍 Filter (session ID, tool, keyword…)"
          value={searchFilter}
          onChange={(e) => setSearchFilter(e.target.value)}
          style={{
            background: 'rgba(255,255,255,0.06)',
            border: '1px solid rgba(255,255,255,0.1)',
            borderRadius: '6px',
            color: '#e2e8f0',
            padding: '4px 10px',
            fontSize: '0.78rem',
            fontFamily: "'JetBrains Mono', monospace",
            outline: 'none',
            minWidth: '180px',
            flex: '0 1 240px',
          }}
        />

        {/* Level counts badges */}
        <div style={{ display: 'flex', gap: '6px', marginLeft: '4px' }}>
          {(['ERROR', 'WARNING', 'INFO'] as const).map(level => {
            const count = levelCounts[level] || 0;
            if (count === 0) return null;
            return (
              <span
                key={level}
                onClick={() => setMinLevel(minLevel === level ? 'ALL' : level)}
                style={{
                  padding: '1px 7px',
                  borderRadius: '8px',
                  fontSize: '0.65rem',
                  fontWeight: 600,
                  fontFamily: "'JetBrains Mono', monospace",
                  background: `${LEVEL_COLORS[level]}18`,
                  color: LEVEL_COLORS[level],
                  border: `1px solid ${LEVEL_COLORS[level]}30`,
                  cursor: 'pointer',
                  transition: 'all 0.15s ease',
                  opacity: minLevel !== 'ALL' && minLevel !== level ? 0.4 : 1,
                }}
              >
                {count} {level.slice(0, 3)}
              </span>
            );
          })}
        </div>

        <div style={{ flex: 1 }} />

        {/* Line count */}
        <span style={{ fontSize: '0.7rem', color: '#475569', fontFamily: "'JetBrains Mono', monospace" }}>
          {filteredLines.length}/{lines.length} lines
        </span>

        {/* Auto-scroll toggle */}
        <button
          onClick={() => setAutoScroll(!autoScroll)}
          title={autoScroll ? 'Auto-scroll ON' : 'Auto-scroll OFF'}
          style={{
            padding: '3px 8px',
            borderRadius: '6px',
            border: 'none',
            background: autoScroll ? 'rgba(34,197,94,0.15)' : 'rgba(255,255,255,0.06)',
            color: autoScroll ? '#22c55e' : '#64748b',
            fontSize: '0.75rem',
            cursor: 'pointer',
            transition: 'all 0.15s ease',
          }}
        >
          {autoScroll ? '⬇ Auto' : '⏸ Paused'}
        </button>
      </div>

      {/* Log content */}
      <div
        ref={containerRef}
        onScroll={() => {
          if (!containerRef.current) return;
          const { scrollTop, scrollHeight, clientHeight } = containerRef.current;
          const isAtBottom = scrollHeight - scrollTop - clientHeight < 40;
          if (autoScroll && !isAtBottom) setAutoScroll(false);
          if (!autoScroll && isAtBottom) setAutoScroll(true);
        }}
        style={{
          flex: 1,
          overflow: 'auto',
          padding: '8px 16px',
          fontFamily: "'JetBrains Mono', monospace",
          fontSize: '0.75rem',
          lineHeight: 1.55,
          background: 'rgba(0,0,0,0.15)',
        }}
      >
        {filteredLines.length === 0 ? (
          <div style={{ color: '#475569', textAlign: 'center', paddingTop: '40px' }}>
            {lines.length === 0 ? 'No log entries yet.' : `No lines matching level ≥ ${minLevel}`}
          </div>
        ) : (
          filteredLines.map((line, i) => {
            const level = getLineLevel(line);
            const color = level ? LEVEL_COLORS[level] : '#94a3b8';
            const isError = level === 'ERROR' || level === 'CRITICAL';
            return (
              <div
                key={i}
                style={{
                  color,
                  padding: '1px 0',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-all',
                  background: isError ? 'rgba(239,68,68,0.04)' : 'transparent',
                  borderLeft: isError ? '2px solid rgba(239,68,68,0.3)' : '2px solid transparent',
                  paddingLeft: '8px',
                  marginLeft: '-8px',
                }}
              >
                {line}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
