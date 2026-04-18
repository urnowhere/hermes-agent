import React from 'react';

interface UsageMetrics {
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
  estimated_cost?: number; // Optional, if backend provides it
}

interface UsageBarProps {
  usage?: UsageMetrics;
}

function formatNumber(num: number | undefined): string {
  if (num === undefined) return '0';
  return num.toLocaleString();
}

export function UsageBar({ usage }: UsageBarProps) {
  if (!usage || Object.keys(usage).length === 0) {
    return null;
  }

  const { prompt_tokens, completion_tokens, total_tokens } = usage;

  return (
    <div
      style={{
        display: 'flex',
        gap: '16px',
        fontSize: '0.75rem',
        color: '#94a3b8',
        background: 'rgba(255, 255, 255, 0.03)',
        borderTop: '1px solid rgba(255, 255, 255, 0.05)',
        padding: '8px 24px',
        justifyContent: 'center',
        alignItems: 'center',
        flexWrap: 'wrap',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
        <span title="Prompt tokens">⬆️ In:</span>
        <strong style={{ color: '#e2e8f0' }}>{formatNumber(prompt_tokens)}</strong>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
        <span title="Completion tokens">⬇️ Out:</span>
        <strong style={{ color: '#e2e8f0' }}>{formatNumber(completion_tokens)}</strong>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
        <span title="Total tokens">📊 Total:</span>
        <strong style={{ color: '#a5b4fc' }}>{formatNumber(total_tokens)}</strong>
      </div>
    </div>
  );
}
