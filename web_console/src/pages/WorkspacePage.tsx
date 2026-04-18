import { useEffect, useMemo, useState } from 'react';
import { apiClient } from '../lib/api';
import { CheckpointList } from '../components/workspace/CheckpointList';
import { DiffViewer } from '../components/workspace/DiffViewer';
import { FileTree } from '../components/workspace/FileTree';
import { FileViewer } from '../components/workspace/FileViewer';
import { ProcessPanel } from '../components/workspace/ProcessPanel';
import { TerminalPanel } from '../components/workspace/TerminalPanel';

interface WorkspaceTreeNode {
  name: string;
  path: string;
  type: 'file' | 'directory';
  children?: WorkspaceTreeNode[];
}

interface WorkspaceTreeResponse { ok: boolean; tree: WorkspaceTreeNode; }
interface WorkspaceFileResponse { ok: boolean; path: string; content: string; }
interface WorkspaceDiffResponse { ok: boolean; diff?: string; }
interface WorkspaceCheckpointResponse { ok: boolean; checkpoints?: Array<{ checkpoint_id?: string; label?: string }>; }
interface ProcessesResponse { ok: boolean; processes?: Array<{ process_id?: string; status?: string }>; }
interface ProcessLogResponse { ok: boolean; output?: string; }
interface WorkspaceSearchResponse { ok: boolean; matches?: Array<{ path: string; line?: number; content?: string }>; }

function flattenTree(node: WorkspaceTreeNode | undefined): string[] {
  if (!node) return [];
  if (node.type === 'file') return [node.path];
  return (node.children ?? []).flatMap((child) => flattenTree(child));
}

export function WorkspacePage() {
  const [files, setFiles] = useState<string[]>([]);
  const [selectedPath, setSelectedPath] = useState('');
  const [fileContent, setFileContent] = useState('Select a file to view its contents.');
  const [diff, setDiff] = useState('');
  const [checkpoints, setCheckpoints] = useState<Array<{ id: string; label: string }>>([]);
  const [processes, setProcesses] = useState<Array<{ id: string; status: string }>>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<Array<{ path: string; snippet: string }>>([]);
  const [selectedCheckpointId, setSelectedCheckpointId] = useState('latest');
  const [leftCollapsed, setLeftCollapsed] = useState(false);
  const [rightCollapsed, setRightCollapsed] = useState(false);

  const refreshAll = async () => {
    const [treeRes, cpRes, procRes] = await Promise.all([
      apiClient.get<WorkspaceTreeResponse>('/workspace/tree').catch(() => null),
      apiClient.get<WorkspaceCheckpointResponse>('/workspace/checkpoints').catch(() => null),
      apiClient.get<ProcessesResponse>('/processes').catch(() => null),
    ]);
    if (treeRes?.ok) {
      const flat = flattenTree(treeRes.tree);
      setFiles(flat);
      if (!selectedPath && flat.length > 0) setSelectedPath(flat[0]);
    }
    if (cpRes?.ok && cpRes.checkpoints) {
      setCheckpoints(cpRes.checkpoints.map((cp) => ({
        id: cp.checkpoint_id ?? cp.label ?? 'unknown',
        label: cp.label ?? cp.checkpoint_id ?? 'checkpoint',
      })));
    }
    if (procRes?.ok && procRes.processes) {
      setProcesses(procRes.processes.map((p) => ({
        id: p.process_id ?? 'unknown',
        status: p.status ?? 'unknown',
      })));
    }
  };

  useEffect(() => { refreshAll(); }, []);

  useEffect(() => {
    if (!selectedPath) return;
    let active = true;

    apiClient.get<any>(`/workspace/file?path=${encodeURIComponent(selectedPath)}&limit=2000`)
      .then((r) => {
        if (!active) return;
        if (r.ok && r.file && r.file.content != null) {
          setFileContent(r.file.content);
        } else if (r.ok && r.content != null) {
          setFileContent(r.content);
        } else {
          setFileContent('Unable to load file.');
        }
      })
      .catch(() => setFileContent('Unable to load file.'));

    return () => { active = false; };
  }, [selectedPath]);

  useEffect(() => {
    apiClient.get<WorkspaceDiffResponse>(`/workspace/diff?checkpoint_id=${encodeURIComponent(selectedCheckpointId)}`)
      .then((r) => { if (r.ok && r.diff) setDiff(r.diff); else setDiff(''); })
      .catch(() => { setDiff(''); });
  }, [selectedCheckpointId]);

  const handleRollback = async (checkpointId: string) => {
    await apiClient.post('/workspace/rollback', { checkpoint_id: checkpointId });
    setSelectedCheckpointId(checkpointId);
    await refreshAll();
  };

  const handleKill = async (processId: string) => {
    await apiClient.post(`/processes/${processId}/kill`, {});
    await refreshAll();
  };

  const handleViewLog = async (processId: string) => {
    try {
      const res = await apiClient.get<ProcessLogResponse>(`/processes/${processId}/log`);
      if (res.ok && res.output) {
        setFileContent(`=== Process Log: ${processId} ===\n\n${res.output}`);
      }
    } catch {
      setFileContent('Failed to load process log.');
    }
  };

  const handleSearch = async () => {
    if (!searchQuery.trim()) return;
    try {
      const res = await apiClient.get<WorkspaceSearchResponse>(`/workspace/search?q=${encodeURIComponent(searchQuery)}`);
      if (res.ok && res.matches) {
        setSearchResults(res.matches.map((r) => ({
          path: r.path,
          snippet: r.content ?? `Line ${r.line ?? '?'}`,
        })));
      }
    } catch {
      setSearchResults([]);
    }
  };

  return (
    <div 
      className="workspace-layout" 
      style={{ 
        gridTemplateColumns: `${leftCollapsed ? '40px' : '280px'} minmax(0, 1fr) ${rightCollapsed ? '40px' : '300px'}`,
        transition: 'grid-template-columns 0.2s ease-out'
      }}
    >
      <div className="workspace-column workspace-column-left" style={{ overflowX: 'hidden' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
          <span style={{ fontSize: '0.8rem', fontWeight: 600, color: '#94a3b8', textTransform: 'uppercase' }}>
             {!leftCollapsed && "Workspace"}
          </span>
          <button 
            onClick={() => setLeftCollapsed(!leftCollapsed)} 
            style={{ background: 'none', border: 'none', color: '#a5b4fc', cursor: 'pointer', padding: '4px' }}
            title={leftCollapsed ? "Expand Workspace" : "Collapse Workspace"}
          >
            {leftCollapsed ? '▶' : '◀'}
          </button>
        </div>

        {!leftCollapsed && (
          <>
            {/* Search bar */}
            <div style={{ display: 'flex', gap: '6px', marginBottom: '8px' }}>
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') handleSearch(); }}
                placeholder="Search workspace…"
                style={{ flex: 1, minWidth: 0, padding: '8px 12px', borderRadius: '8px', border: '1px solid rgba(129,140,248,0.2)', background: 'rgba(0,0,0,0.2)', color: 'white', fontSize: '0.85rem' }}
              />
              <button type="button" onClick={handleSearch} style={{ flexShrink: 0, padding: '8px 12px', borderRadius: '8px', background: 'rgba(129,140,248,0.15)', border: '1px solid rgba(129,140,248,0.3)', color: '#a5b4fc', cursor: 'pointer', fontSize: '0.85rem' }}>🔍</button>
            </div>

            {searchResults.length > 0 && (
              <div style={{ marginBottom: '8px', maxHeight: '150px', overflow: 'auto', background: 'rgba(0,0,0,0.1)', borderRadius: '8px', padding: '6px' }}>
                {searchResults.map((r, i) => (
                  <div key={i} style={{ padding: '4px 8px', cursor: 'pointer', fontSize: '0.8rem', color: '#a5b4fc', borderRadius: '4px' }} onClick={() => setSelectedPath(r.path)}>
                    <strong>{r.path.split('/').pop()}</strong> <span style={{ color: '#64748b' }}>{r.snippet}</span>
                  </div>
                ))}
              </div>
            )}

            <FileTree files={files} selectedPath={selectedPath} onSelect={setSelectedPath} />
            <CheckpointList checkpoints={checkpoints} onRollback={handleRollback} />
          </>
        )}
      </div>

      <div className="workspace-column workspace-column-main">
        <FileViewer path={selectedPath} content={fileContent} onContentChange={setFileContent} />
        <DiffViewer diff={diff} />
      </div>

      <div className="workspace-column workspace-column-right" style={{ overflowX: 'hidden' }}>
        <div style={{ display: 'flex', justifyContent: rightCollapsed ? 'center' : 'space-between', alignItems: 'center', marginBottom: '8px' }}>
           <button 
            onClick={() => setRightCollapsed(!rightCollapsed)} 
            style={{ background: 'none', border: 'none', color: '#a5b4fc', cursor: 'pointer', padding: '4px' }}
            title={rightCollapsed ? "Expand Tools" : "Collapse Tools"}
          >
            {rightCollapsed ? '◀' : '▶'}
          </button>
          {!rightCollapsed && (
            <span style={{ fontSize: '0.8rem', fontWeight: 600, color: '#94a3b8', textTransform: 'uppercase' }}>
              Environment
            </span>
          )}
        </div>

        {/* Instead of unmounting the Terminal (which kills xterm instances), we visually hide everything using css opacity and height when collapsed */}
        <div style={{ display: rightCollapsed ? 'none' : 'flex', flexDirection: 'column', gap: '12px', flex: 1, minHeight: 0 }}>
          <TerminalPanel />
          <ProcessPanel processes={processes} onKill={handleKill} onViewLog={handleViewLog} />
        </div>
      </div>
    </div>
  );
}
