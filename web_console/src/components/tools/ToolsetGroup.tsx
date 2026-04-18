import { type FC } from 'react';
import { ToolCard, type ToolInfo } from './ToolCard';

export interface ToolsetInfo {
  key: string;
  label: string;
  description: string;
  tools: string[];
  tool_count: number;
  available: boolean;
  has_keys: boolean;
  enabled: boolean;
}

interface ToolsetGroupProps {
  setName: string;
  setTools: ToolInfo[];
  tsInfo?: ToolsetInfo;
  isCollapsed: boolean;
  isToggling: boolean;
  expandedTool: string | null;
  onToggleCollapse: (setName: string) => void;
  onToggleToolset: (setName: string, isEnabled: boolean) => void;
  onToggleExpandTool: (toolName: string) => void;
  onAddKeys: () => void;
}

export const ToolsetGroup: FC<ToolsetGroupProps> = ({
  setName,
  setTools,
  tsInfo,
  isCollapsed,
  isToggling,
  expandedTool,
  onToggleCollapse,
  onToggleToolset,
  onToggleExpandTool,
  onAddKeys,
}) => {
  const availInSet = setTools.filter(t => t.is_available).length;
  const isEnabled = tsInfo?.enabled ?? true;

  return (
    <div style={{
      background: 'rgba(255,255,255,0.03)',
      border: `1px solid ${isEnabled ? 'rgba(255,255,255,0.06)' : 'rgba(239,68,68,0.15)'}`,
      borderRadius: '14px',
      overflow: 'hidden',
      opacity: isEnabled ? 1 : 0.65,
      transition: 'opacity 0.2s, border-color 0.2s',
    }}>
      {/* Toolset header */}
      <div
        style={{
          padding: '12px 16px',
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          background: 'rgba(129, 140, 248, 0.04)',
          borderBottom: isCollapsed ? 'none' : '1px solid rgba(255,255,255,0.04)',
        }}
      >
        <div
          onClick={() => onToggleCollapse(setName)}
          style={{ display: 'flex', alignItems: 'center', gap: '10px', cursor: 'pointer', flex: 1 }}
        >
          <span style={{ color: '#64748b', fontSize: '0.8rem' }}>{isCollapsed ? '▸' : '▾'}</span>
          <span style={{ color: '#e2e8f0', fontWeight: 600, fontSize: '0.95rem' }}>
            {tsInfo?.label || setName}
          </span>
          <span style={{
            fontSize: '0.7rem', padding: '1px 6px', borderRadius: '4px',
            background: 'rgba(129,140,248,0.1)', color: '#a5b4fc',
          }}>
            {setTools.length} tool{setTools.length !== 1 ? 's' : ''}
          </span>
          <span style={{
            fontSize: '0.7rem', padding: '2px 8px', borderRadius: '4px',
            background: availInSet === setTools.length ? 'rgba(34,197,94,0.1)' : 'rgba(251,191,36,0.1)',
            color: availInSet === setTools.length ? '#86efac' : '#fde68a',
          }}>
            {availInSet}/{setTools.length} available
          </span>
          {tsInfo && !tsInfo.has_keys && (
            <button
              onClick={(e) => { e.stopPropagation(); onAddKeys(); }}
              style={{
                fontSize: '0.65rem', padding: '2px 8px', borderRadius: '4px',
                background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.2)',
                color: '#f87171', cursor: 'pointer'
              }}
            >
              Add Keys
            </button>
          )}
        </div>
        {/* Toggle switch */}
        {tsInfo && (
          <button
            onClick={(e) => { e.stopPropagation(); onToggleToolset(setName, isEnabled); }}
            disabled={isToggling}
            style={{
              background: isEnabled ? 'rgba(34,197,94,0.2)' : 'rgba(100,116,139,0.2)',
              border: `1px solid ${isEnabled ? 'rgba(34,197,94,0.3)' : 'rgba(100,116,139,0.3)'}`,
              color: isEnabled ? '#86efac' : '#94a3b8',
              padding: '4px 12px',
              borderRadius: '6px',
              fontSize: '0.75rem',
              fontWeight: 500,
              cursor: isToggling ? 'wait' : 'pointer',
              opacity: isToggling ? 0.6 : 1,
              transition: 'all 0.2s',
              flexShrink: 0,
            }}
          >
            {isToggling ? '...' : isEnabled ? 'Enabled' : 'Disabled'}
          </button>
        )}
      </div>

      {/* Tools in this set */}
      {!isCollapsed && (
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          {setTools.map(tool => (
            <ToolCard
              key={tool.name}
              tool={tool}
              isExpanded={expandedTool === tool.name}
              onToggleExpand={() => onToggleExpandTool(tool.name)}
            />
          ))}
        </div>
      )}
    </div>
  );
};
