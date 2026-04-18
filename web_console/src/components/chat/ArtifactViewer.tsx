import React from 'react';
import { UiState } from '../../lib/types';
import { closeArtifact } from '../../store/uiStore';

interface ArtifactViewerProps {
  uiState: UiState;
  setUiState: React.Dispatch<React.SetStateAction<UiState>>;
}

export function ArtifactViewer({ uiState, setUiState }: ArtifactViewerProps) {
  if (!uiState.artifactOpen || !uiState.artifactContent) return null;

  const { artifactType, artifactContent } = uiState;

  const handleClose = () => {
    setUiState(closeArtifact(uiState));
  };

  return (
    <div style={{
      position: 'fixed',
      top: 0,
      left: 0,
      right: 0,
      bottom: 0,
      zIndex: 1000,
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'flex-end',
      background: 'rgba(0,0,0,0.4)',
      backdropFilter: 'blur(4px)'
    }}>
      <div style={{
        width: '60vw',
        height: '100vh',
        background: '#1e1e2e',
        boxShadow: '-4px 0 24px rgba(0,0,0,0.5)',
        display: 'flex',
        flexDirection: 'column',
        animation: 'slideInRight 0.3s ease-out'
      }}>
        <div style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          padding: '16px 24px',
          borderBottom: '1px solid rgba(255,255,255,0.1)',
          background: 'rgba(0,0,0,0.2)'
        }}>
          <h2 style={{ margin: 0, fontSize: '1.2rem', color: '#e2e8f0', display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span>🎨</span> Artifact Canvas
            <span style={{ 
              fontSize: '0.75rem', 
              background: '#818cf8', 
              color: '#fff', 
              padding: '2px 6px', 
              borderRadius: '4px',
              textTransform: 'uppercase'
            }}>{artifactType}</span>
          </h2>
          <button 
            onClick={handleClose}
            style={{ 
              background: 'transparent', 
              border: 'none', 
              color: '#94a3b8', 
              fontSize: '1.5rem', 
              cursor: 'pointer',
              lineHeight: 1
            }}
          >
            ×
          </button>
        </div>
        
        <div style={{ flex: 1, padding: '24px', overflow: 'auto', background: '#fff' }}>
          {(artifactType === 'html' || artifactType === 'svg') ? (
            <iframe
              srcDoc={artifactContent}
              style={{ width: '100%', height: '100%', border: 'none', background: '#fff' }}
              title="Artifact Preview"
            />
          ) : (
            <div style={{ whiteSpace: 'pre-wrap', color: '#000', fontFamily: 'monospace' }}>
              {artifactContent}
            </div>
          )}
        </div>
      </div>
      <style>{`
        @keyframes slideInRight {
          from { transform: translateX(100%); }
          to { transform: translateX(0); }
        }
      `}</style>
    </div>
  );
}
