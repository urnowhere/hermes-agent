import { useState } from 'react';

interface ClarifyPromptProps {
  pending: Array<{ id: string; message?: string }>;
  onClarify: (id: string, response: string) => void;
  onDeny: (id: string) => void;
}

export function ClarifyPrompt({ pending, onClarify, onDeny }: ClarifyPromptProps) {
  const [inputText, setInputText] = useState('');
  if (pending.length === 0) return null;

  const request = pending[0]; // Process one at a time

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (inputText.trim()) {
      onClarify(request.id, inputText.trim());
      setInputText('');
    }
  };

  return (
    <div style={{
      position: 'fixed',
      bottom: '140px',
      left: '50%',
      transform: 'translateX(-50%)',
      background: 'rgba(56, 189, 248, 0.15)',
      backdropFilter: 'blur(16px)',
      border: '1px solid rgba(56, 189, 248, 0.3)',
      boxShadow: '0 12px 40px rgba(0, 0, 0, 0.4), 0 0 0 1px rgba(56, 189, 248, 0.1)',
      padding: '20px 24px',
      borderRadius: '20px',
      zIndex: 100,
      minWidth: '400px',
      display: 'flex',
      flexDirection: 'column',
      gap: '12px',
      animation: 'modalScaleIn 0.3s cubic-bezier(0.16, 1, 0.3, 1)',
    }}>
      <h3 style={{ margin: 0, color: '#38bdf8', display: 'flex', alignItems: 'center', gap: '8px' }}>
        <span style={{ fontSize: '1.2rem' }}>💭</span> Question from Hermes
      </h3>
      <p style={{ margin: 0, color: '#bae6fd', lineHeight: 1.5 }}>
        {request.message || 'The agent needs more information to continue.'}
      </p>
      
      <form onSubmit={handleSubmit} style={{ display: 'flex', gap: '8px', marginTop: '4px' }}>
        <input 
          type="text" 
          value={inputText}
          onChange={(e) => setInputText(e.target.value)}
          placeholder="Type your response..." 
          style={{ flex: 1, padding: '10px 14px', borderRadius: '12px', border: '1px solid rgba(56, 189, 248, 0.3)', background: 'rgba(0, 0, 0, 0.2)', color: 'white' }} 
        />
        <button type="submit" style={{ background: 'rgba(56, 189, 248, 0.2)', color: '#bae6fd', border: '1px solid rgba(56, 189, 248, 0.3)', padding: '10px 16px', borderRadius: '12px', cursor: 'pointer', fontWeight: 500 }}>
          Reply
        </button>
        <button type="button" onClick={() => onDeny(request.id)} style={{ background: 'rgba(0, 0, 0, 0.3)', color: '#9ca3af', border: '1px solid rgba(255, 255, 255, 0.1)', padding: '10px 16px', borderRadius: '12px', cursor: 'pointer', fontWeight: 500 }}>
          Cancel
        </button>
      </form>
    </div>
  );
}
