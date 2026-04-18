interface EmptyStateProps {
  icon?: string;
  title: string;
  description?: string;
  action?: {
    label: string;
    onClick: () => void;
  };
}

export function EmptyState({ icon = '📭', title, description, action }: EmptyStateProps) {
  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      padding: '40px 24px',
      textAlign: 'center',
      color: '#94a3b8',
    }}>
      <div style={{ fontSize: '2.5rem', marginBottom: '16px', opacity: 0.6 }}>{icon}</div>
      <h3 style={{ margin: '0 0 8px 0', color: '#e2e8f0', fontSize: '1rem', fontWeight: 600 }}>{title}</h3>
      {description && (
        <p style={{ margin: '0 0 20px 0', fontSize: '0.85rem', maxWidth: '320px', lineHeight: 1.5 }}>
          {description}
        </p>
      )}
      {action && (
        <button
          onClick={action.onClick}
          style={{
            padding: '8px 20px',
            borderRadius: '8px',
            background: 'rgba(129, 140, 248, 0.15)',
            border: '1px solid rgba(129, 140, 248, 0.3)',
            color: '#a5b4fc',
            cursor: 'pointer',
            fontSize: '0.85rem',
          }}
        >
          {action.label}
        </button>
      )}
    </div>
  );
}
