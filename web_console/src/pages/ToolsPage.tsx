import { useEffect, useState, useCallback } from 'react';
import { apiClient } from '../lib/api';
import { toastStore } from '../store/toastStore';
import { LoadingSpinner } from '../components/shared/LoadingSpinner';
import { EmptyState } from '../components/shared/EmptyState';
import { ToolsetGroup, type ToolsetInfo } from '../components/tools/ToolsetGroup';
import type { ToolInfo } from '../components/tools/ToolCard';
import { ApiKeyConfigModal } from '../components/settings/ApiKeyConfigModal';

interface ToolsResponse {
  ok: boolean;
  tools?: ToolInfo[];
}

interface ToolsetsResponse {
  ok: boolean;
  toolsets?: ToolsetInfo[];
  platform?: string;
}

export function ToolsPage() {
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [toolsets, setToolsets] = useState<ToolsetInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState('');
  const [collapsedSets, setCollapsedSets] = useState<Set<string>>(new Set());
  const [expandedTool, setExpandedTool] = useState<string | null>(null);
  const [togglingToolset, setTogglingToolset] = useState<string | null>(null);
  const [showKeyConfig, setShowKeyConfig] = useState(false);

  const fetchAll = useCallback(async () => {
    try {
      const [toolsRes, toolsetsRes] = await Promise.all([
        apiClient.get<ToolsResponse>('/tools'),
        apiClient.get<ToolsetsResponse>('/toolsets'),
      ]);
      if (toolsRes.ok && toolsRes.tools) setTools(toolsRes.tools);
      if (toolsetsRes.ok && toolsetsRes.toolsets) setToolsets(toolsetsRes.toolsets);
    } catch (err) {
      toastStore.error('Tools Load Failed', err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  const handleToggleToolset = async (tsKey: string, currentEnabled: boolean) => {
    setTogglingToolset(tsKey);
    try {
      const res = await apiClient.post<any>('/toolsets/toggle', {
        toolset: tsKey,
        enabled: !currentEnabled,
        platform: 'cli',
      });
      if (res.ok) {
        setToolsets(prev => prev.map(ts =>
          ts.key === tsKey ? { ...ts, enabled: !currentEnabled } : ts
        ));
        toastStore.success(`${tsKey} ${!currentEnabled ? 'enabled' : 'disabled'}`);
      } else {
        toastStore.error(res.error || 'Failed to toggle toolset');
      }
    } catch (err) {
      toastStore.error('Toggle failed', err instanceof Error ? err.message : String(err));
    } finally {
      setTogglingToolset(null);
    }
  };

  if (loading) return <LoadingSpinner message="Loading tools…" />;

  if (tools.length === 0) {
    return <EmptyState icon="🔧" title="No tools available" description="No tools are registered in the current agent instance." />;
  }

  // Group tools by toolset
  const toolsBySet = new Map<string, ToolInfo[]>();
  for (const tool of tools) {
    const set = tool.toolset || 'ungrouped';
    if (!toolsBySet.has(set)) toolsBySet.set(set, []);
    toolsBySet.get(set)!.push(tool);
  }

  // Build toolset enabled map from API data
  const toolsetEnabledMap = new Map<string, ToolsetInfo>();
  for (const ts of toolsets) {
    toolsetEnabledMap.set(ts.key, ts);
  }

  // Filter
  const query = filter.toLowerCase();
  const filteredSets = new Map<string, ToolInfo[]>();
  for (const [set, setTools] of toolsBySet) {
    const filtered = query
      ? setTools.filter(t => t.name.toLowerCase().includes(query) || t.description.toLowerCase().includes(query) || set.toLowerCase().includes(query))
      : setTools;
    if (filtered.length > 0) {
      filteredSets.set(set, filtered);
    }
  }

  const totalCount = tools.length;
  const availableCount = tools.filter(t => t.is_available).length;

  const toggleCollapse = (set: string) => {
    setCollapsedSets(prev => {
      const next = new Set(prev);
      if (next.has(set)) next.delete(set); else next.add(set);
      return next;
    });
  };

  const handleToggleExpandTool = (toolName: string) => {
    setExpandedTool(prev => prev === toolName ? null : toolName);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', flex: 1, overflowY: 'auto', minHeight: 0, paddingBottom: '30px', paddingRight: '12px' }}>
      {/* Header & Stats */}
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)',
        borderRadius: '16px', padding: '16px 20px',
      }}>
        <div>
          <h2 style={{ margin: 0, color: '#e2e8f0', fontSize: '1.1rem' }}>🔧 Tools & Toolsets</h2>
          <p style={{ margin: '4px 0 0', color: '#64748b', fontSize: '0.85rem' }}>
            {availableCount}/{totalCount} tools available · {toolsBySet.size} toolsets
            {toolsets.length > 0 && ` · ${toolsets.filter(ts => ts.enabled).length} enabled`}
          </p>
        </div>
        <input
          type="text"
          value={filter}
          onChange={e => setFilter(e.target.value)}
          placeholder="Filter tools…"
          style={{
            padding: '8px 14px', borderRadius: '10px', width: '220px',
            border: '1px solid rgba(129,140,248,0.2)', background: 'rgba(0,0,0,0.2)',
            color: 'white', fontSize: '0.85rem',
          }}
        />
      </div>

      {/* Toolset groups */}
      {Array.from(filteredSets.entries()).sort(([a], [b]) => a.localeCompare(b)).map(([setName, setTools]) => (
        <ToolsetGroup
          key={setName}
          setName={setName}
          setTools={setTools}
          tsInfo={toolsetEnabledMap.get(setName)}
          isCollapsed={collapsedSets.has(setName)}
          isToggling={togglingToolset === setName}
          expandedTool={expandedTool}
          onToggleCollapse={toggleCollapse}
          onToggleToolset={handleToggleToolset}
          onToggleExpandTool={handleToggleExpandTool}
          onAddKeys={() => setShowKeyConfig(true)}
        />
      ))}

      {filteredSets.size === 0 && filter && (
        <EmptyState icon="🔍" title="No matching tools" description={`No tools match "${filter}".`} />
      )}

      {showKeyConfig && (
        <ApiKeyConfigModal
          categories={['tool', 'provider']}
          onClose={() => setShowKeyConfig(false)}
          onSaved={() => {
            setShowKeyConfig(false);
            fetchAll();
          }}
        />
      )}
    </div>
  );
}

