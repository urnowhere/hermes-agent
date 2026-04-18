import { useMemo, useState } from 'react';

interface DiffViewerProps {
  diff: string;
}

interface DiffLine {
  type: 'add' | 'remove' | 'context' | 'header' | 'info';
  text: string;
  lineNum?: number;
}

function parseDiff(raw: string): { fileName: string; lines: DiffLine[] }[] {
  if (!raw.trim()) return [];

  const chunks: { fileName: string; lines: DiffLine[] }[] = [];
  let current: { fileName: string; lines: DiffLine[] } | null = null;

  for (const line of raw.split('\n')) {
    if (line.startsWith('diff --git') || line.startsWith('--- ') && !current) {
      // Start of new file
      const match = line.match(/diff --git a\/(.+?) b\//);
      current = { fileName: match?.[1] ?? 'unknown', lines: [] };
      chunks.push(current);
      current.lines.push({ type: 'header', text: line });
    } else if (line.startsWith('--- ') || line.startsWith('+++ ')) {
      if (current) {
        if (line.startsWith('+++ b/')) {
          current.fileName = line.slice(6);
        }
        current.lines.push({ type: 'info', text: line });
      }
    } else if (line.startsWith('@@')) {
      if (!current) {
        current = { fileName: 'changes', lines: [] };
        chunks.push(current);
      }
      current.lines.push({ type: 'header', text: line });
    } else if (line.startsWith('+')) {
      if (!current) { current = { fileName: 'changes', lines: [] }; chunks.push(current); }
      current.lines.push({ type: 'add', text: line.slice(1) });
    } else if (line.startsWith('-')) {
      if (!current) { current = { fileName: 'changes', lines: [] }; chunks.push(current); }
      current.lines.push({ type: 'remove', text: line.slice(1) });
    } else {
      if (!current) { current = { fileName: 'changes', lines: [] }; chunks.push(current); }
      current.lines.push({ type: 'context', text: line.startsWith(' ') ? line.slice(1) : line });
    }
  }

  return chunks;
}

const LINE_STYLES: Record<DiffLine['type'], React.CSSProperties> = {
  add: { background: 'rgba(34, 197, 94, 0.1)', color: '#86efac', borderLeft: '3px solid rgba(34, 197, 94, 0.5)' },
  remove: { background: 'rgba(239, 68, 68, 0.1)', color: '#fca5a5', borderLeft: '3px solid rgba(239, 68, 68, 0.5)' },
  context: { background: 'transparent', color: '#94a3b8', borderLeft: '3px solid transparent' },
  header: { background: 'rgba(129, 140, 248, 0.08)', color: '#818cf8', borderLeft: '3px solid rgba(129, 140, 248, 0.4)' },
  info: { background: 'transparent', color: '#64748b', borderLeft: '3px solid transparent' },
};

const PREFIX: Record<DiffLine['type'], string> = {
  add: '+',
  remove: '-',
  context: ' ',
  header: '',
  info: '',
};

export function DiffViewer({ diff }: DiffViewerProps) {
  const [collapsed, setCollapsed] = useState<Record<number, boolean>>({});
  const chunks = useMemo(() => parseDiff(diff), [diff]);

  if (!diff.trim()) {
    return (
      <section className="workspace-card" aria-label="Diff viewer">
        <div className="workspace-card-header">
          <h2>Diff</h2>
          <p>No changes to display.</p>
        </div>
      </section>
    );
  }

  return (
    <section className="workspace-card" aria-label="Diff viewer">
      <div className="workspace-card-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h2>Diff</h2>
          <p>{chunks.length} file{chunks.length !== 1 ? 's' : ''} changed</p>
        </div>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
        {chunks.map((chunk, ci) => {
          const addCount = chunk.lines.filter(l => l.type === 'add').length;
          const removeCount = chunk.lines.filter(l => l.type === 'remove').length;
          const isCollapsed = collapsed[ci] ?? false;

          return (
            <div key={ci} style={{
              border: '1px solid rgba(255,255,255,0.06)',
              borderRadius: '10px',
              overflow: 'hidden',
            }}>
              {/* File header */}
              <div
                onClick={() => setCollapsed(prev => ({ ...prev, [ci]: !prev[ci] }))}
                style={{
                  padding: '8px 12px',
                  background: 'rgba(129, 140, 248, 0.06)',
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  cursor: 'pointer',
                  fontSize: '0.85rem',
                }}
              >
                <span style={{ color: '#e2e8f0', fontFamily: 'monospace' }}>
                  {isCollapsed ? '▸' : '▾'} {chunk.fileName}
                </span>
                <span style={{ display: 'flex', gap: '8px', fontSize: '0.75rem' }}>
                  {addCount > 0 && <span style={{ color: '#86efac' }}>+{addCount}</span>}
                  {removeCount > 0 && <span style={{ color: '#fca5a5' }}>−{removeCount}</span>}
                </span>
              </div>

              {/* Diff lines */}
              {!isCollapsed && (
                <div style={{
                  fontFamily: 'monospace',
                  fontSize: '0.8rem',
                  lineHeight: '1.5',
                  overflowX: 'auto',
                }}>
                  {chunk.lines.filter(l => l.type !== 'info').map((line, li) => (
                    <div
                      key={li}
                      style={{
                        ...LINE_STYLES[line.type],
                        padding: '0 12px 0 8px',
                        whiteSpace: 'pre',
                        minHeight: '1.5em',
                      }}
                    >
                      <span style={{ display: 'inline-block', width: '14px', color: '#475569', textAlign: 'center', marginRight: '8px', userSelect: 'none' }}>
                        {PREFIX[line.type]}
                      </span>
                      {line.text}
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}
