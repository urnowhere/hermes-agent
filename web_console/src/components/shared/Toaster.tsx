import { useToasts, toastStore, type Toast } from '../../store/toastStore';

const ICONS = {
  info: 'ℹ️',
  success: '✅',
  error: '❌',
  warning: '⚠️'
};

const BORDERS = {
  info: 'rgba(56, 189, 248, 0.4)',
  success: 'rgba(34, 197, 94, 0.4)',
  error: 'rgba(239, 68, 68, 0.4)',
  warning: 'rgba(251, 191, 36, 0.4)'
};

const BGS = {
  info: 'rgba(15, 23, 42, 0.95)',
  success: 'rgba(15, 23, 42, 0.95)',
  error: 'rgba(15, 23, 42, 0.95)',
  warning: 'rgba(15, 23, 42, 0.95)'
};

export function Toaster() {
  const toasts = useToasts();

  if (toasts.length === 0) return null;

  return (
    <div style={{
      position: 'fixed',
      bottom: '16px',
      right: '16px',
      display: 'flex',
      flexDirection: 'column',
      gap: '8px',
      zIndex: 9999,
      pointerEvents: 'none',
    }}>
      {toasts.map((toast) => (
        <div
          key={toast.id}
          style={{
            background: BGS[toast.type],
            border: `1px solid ${BORDERS[toast.type]}`,
            borderRadius: '12px',
            padding: '12px 16px',
            minWidth: '280px',
            maxWidth: '380px',
            boxShadow: '0 8px 32px rgba(0, 0, 0, 0.4), inset 0 1px 0 rgba(255, 255, 255, 0.05)',
            display: 'flex',
            alignItems: 'flex-start',
            gap: '12px',
            pointerEvents: 'auto',
            animation: 'slideInRight 0.2s ease-out forwards',
            backdropFilter: 'blur(10px)',
          }}
        >
          <div style={{ fontSize: '1.2rem', marginTop: '-2px' }}>
            {ICONS[toast.type]}
          </div>
          <div style={{ flex: 1 }}>
            <h4 style={{ margin: '0 0 4px', color: '#f8fafc', fontSize: '0.9rem', fontWeight: 600 }}>
              {toast.title}
            </h4>
            {toast.message && (
              <p style={{ margin: 0, color: '#94a3b8', fontSize: '0.8rem', lineHeight: 1.4 }}>
                {toast.message}
              </p>
            )}
          </div>
          <button
            onClick={() => toastStore.remove(toast.id)}
            style={{
              background: 'transparent',
              border: 'none',
              color: '#64748b',
              cursor: 'pointer',
              padding: '4px',
              margin: '-4px -4px 0 0',
              fontSize: '0.9rem',
            }}
            title="Dismiss"
          >
            ✕
          </button>
        </div>
      ))}
      <style>{`
        @keyframes slideInRight {
          from { transform: translateX(100%); opacity: 0; }
          to { transform: translateX(0); opacity: 1; }
        }
      `}</style>
    </div>
  );
}
