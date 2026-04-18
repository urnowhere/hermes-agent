import { type FC } from 'react';

export interface ToolInfo {
  name: string;
  toolset: string;
  description: string;
  parameters: Record<string, unknown>;
  requires_env: string[];
  is_available: boolean;
}

interface ToolCardProps {
  tool: ToolInfo;
  isExpanded: boolean;
  onToggleExpand: () => void;
}

export const ToolCard: FC<ToolCardProps> = ({ tool, isExpanded, onToggleExpand }) => {
  return (
    <div style={{
      borderBottom: '1px solid rgba(255,255,255,0.03)',
    }}>
      <div
        onClick={onToggleExpand}
        style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          padding: '10px 16px',
          cursor: 'pointer',
          transition: 'background 0.15s',
        }}
      >
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <div style={{
              width: '6px', height: '6px', borderRadius: '50%', flexShrink: 0,
              background: tool.is_available ? '#22c55e' : '#475569',
              boxShadow: tool.is_available ? '0 0 4px rgba(34,197,94,0.4)' : 'none',
            }} />
            <code style={{ color: '#e2e8f0', fontSize: '0.85rem' }}>{tool.name}</code>
          </div>
          <p style={{
            margin: '3px 0 0 14px', color: '#64748b', fontSize: '0.75rem',
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          }}>
            {tool.description || 'No description'}
          </p>
        </div>
        {tool.requires_env.length > 0 && (
          <div style={{ display: 'flex', gap: '4px', flexShrink: 0, marginLeft: '8px' }}>
            {tool.requires_env.map(env => (
              <span key={env} style={{
                fontSize: '0.65rem', padding: '1px 4px', borderRadius: '3px',
                background: tool.is_available ? 'rgba(34,197,94,0.1)' : 'rgba(239,68,68,0.1)',
                color: tool.is_available ? '#86efac' : '#f87171',
              }}>
                {env}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* Expanded detail */}
      {isExpanded && (
        <div style={{
          padding: '8px 16px 12px 30px',
          background: 'rgba(0,0,0,0.1)',
          fontSize: '0.8rem',
        }}>
          <p style={{ margin: '0 0 8px 0', color: '#94a3b8', lineHeight: 1.5 }}>
            {tool.description}
          </p>
          {tool.requires_env.length > 0 && (
            <div style={{ marginBottom: '8px' }}>
              <span style={{ color: '#64748b', fontSize: '0.75rem' }}>Required: </span>
              {tool.requires_env.map(env => (
                <code key={env} style={{
                  color: tool.is_available ? '#86efac' : '#f87171',
                  background: 'rgba(0,0,0,0.2)', padding: '1px 6px',
                  borderRadius: '4px', fontSize: '0.75rem', marginRight: '4px',
                }}>
                  {env}
                </code>
              ))}
            </div>
          )}
          {tool.parameters && Object.keys(tool.parameters).length > 0 && (
            <details style={{ color: '#64748b' }}>
              <summary style={{ cursor: 'pointer', fontSize: '0.75rem', marginBottom: '4px' }}>
                Parameters
              </summary>
              <pre style={{
                margin: 0, padding: '8px', borderRadius: '6px',
                background: 'rgba(0,0,0,0.2)', color: '#94a3b8',
                fontSize: '0.7rem', overflow: 'auto', maxHeight: '200px',
              }}>
                {JSON.stringify(tool.parameters, null, 2)}
              </pre>
            </details>
          )}
        </div>
      )}
    </div>
  );
};
