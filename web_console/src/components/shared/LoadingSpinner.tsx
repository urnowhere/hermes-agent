interface LoadingSpinnerProps {
  message?: string;
  size?: 'sm' | 'md' | 'lg';
}

const SIZES = {
  sm: { spinner: 16, font: '0.75rem', gap: '8px' },
  md: { spinner: 24, font: '0.85rem', gap: '12px' },
  lg: { spinner: 32, font: '1rem', gap: '16px' },
};

export function LoadingSpinner({ message, size = 'md' }: LoadingSpinnerProps) {
  const s = SIZES[size];

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      gap: s.gap,
      padding: '24px',
      color: '#64748b',
    }}>
      <div
        style={{
          width: s.spinner,
          height: s.spinner,
          border: `2px solid rgba(129, 140, 248, 0.2)`,
          borderTopColor: '#818cf8',
          borderRadius: '50%',
          animation: 'spin 0.8s linear infinite',
        }}
      />
      {message && <span style={{ fontSize: s.font }}>{message}</span>}
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}
