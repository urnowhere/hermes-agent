import { useState } from 'react';

const PERSONALITIES: Record<string, { icon: string, name: string, desc: string, sample: string }> = {
  helpful: { icon: '🤖', name: 'Helpful', desc: 'Standard AI assistant', sample: 'I can certainly help you with that. Here are the details you requested.' },
  concise: { icon: '⚡', name: 'Concise', desc: 'Short and to the point', sample: 'Here is the code.' },
  technical: { icon: '💻', name: 'Technical', desc: 'Detailed developer focus', sample: 'The system architecture relies on asynchronous event loops and typed interfaces.' },
  creative: { icon: '🎨', name: 'Creative', desc: 'Imaginative and expressive', sample: 'Let\'s explore a galaxy of possibilities for your feature!' },
  teacher: { icon: '✏️', name: 'Teacher', desc: 'Patient and educational', sample: 'Let\'s break this down step-by-step so you can understand the core concept.' },
  kawaii: { icon: '🌸', name: 'Kawaii', desc: 'Cute and enthusiastic', sample: 'Haii! (≧◡≦) I can totally help you with that!' },
  catgirl: { icon: '🐱', name: 'Catgirl', desc: 'Feline-infused helpfulness', sample: 'Nyaa~ I found the bug you were looking for, master! ^_^' },
  pirate: { icon: '🏴‍☠️', name: 'Pirate', desc: 'Swashbuckling responses', sample: 'Yarrr matey, here be the treasure ye requested!' },
  shakespeare: { icon: '📜', name: 'Shakespeare', desc: 'Elizabethan english', sample: 'Hark! The code thou seekest is writ thusly.' },
  surfer: { icon: '🏄', name: 'Surfer', desc: 'Chill and laid back', sample: 'Whoa dude, that\'s totally gnarly. Let\'s fix that bug.' },
  noir: { icon: '🕵️', name: 'Noir', desc: 'Gritty detective', sample: 'The bug was hiding in the shadows of line 42. I rooted it out.' },
  uwu: { icon: '🥺', name: 'UwU', desc: 'Excessively cute', sample: 'H-hewwo? (///w///) I wrote da code fow chu!' },
  philosopher: { icon: '🤔', name: 'Philosopher', desc: 'Deep and contemplative', sample: 'What is a bug, really, but a feature waiting to be understood?' },
  hype: { icon: '🔥', name: 'Hype', desc: 'High energy', sample: 'LET\'S GOOOO! EXACTLY WHAT WE NEEDED! WOO!' }
};

interface PersonalityPickerProps {
  value: string;
  options?: string[];
  onChange: (val: string) => void;
}

export function PersonalityPicker({ value, options = [], onChange }: PersonalityPickerProps) {
  // We'll show a grid of small cards.
  
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', width: '100%', gridColumn: '1 / -1' }}>
      <div style={{ 
        display: 'grid', 
        gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', 
        gap: '12px',
        maxHeight: '300px',
        overflowY: 'auto',
        padding: '4px',
      }}>
        {options.map((opt) => {
          const info = PERSONALITIES[opt] || { icon: '❓', name: opt, desc: 'Custom personality', sample: 'Hello.' };
          const isSelected = value === opt;
          
          return (
            <button
              key={opt}
              type="button"
              onClick={() => onChange(opt)}
              style={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'flex-start',
                textAlign: 'left',
                padding: '12px',
                borderRadius: '12px',
                background: isSelected ? 'rgba(129, 140, 248, 0.15)' : 'rgba(0,0,0,0.2)',
                border: `1px solid ${isSelected ? 'rgba(129, 140, 248, 0.5)' : 'rgba(255,255,255,0.1)'}`,
                cursor: 'pointer',
                transition: 'all 0.15s ease',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
                <span style={{ fontSize: '1.2rem' }}>{info.icon}</span>
                <strong style={{ color: isSelected ? '#a5b4fc' : '#e2e8f0', fontSize: '0.9rem' }}>{info.name}</strong>
              </div>
              <span style={{ color: '#94a3b8', fontSize: '0.75rem', lineHeight: 1.3 }}>{info.desc}</span>
            </button>
          );
        })}
      </div>
      
      {/* Sample preview box */}
      <div style={{
        marginTop: '8px',
        padding: '16px',
        background: 'rgba(255, 255, 255, 0.03)',
        border: '1px solid rgba(255, 255, 255, 0.08)',
        borderRadius: '12px',
        display: 'flex',
        gap: '12px',
        alignItems: 'flex-start'
      }}>
        <div style={{ 
          width: '32px', height: '32px', borderRadius: '50%', background: 'rgba(129, 140, 248, 0.1)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, border: '1px solid rgba(129, 140, 248, 0.3)',
          fontSize: '1.1rem'
        }}>
          {PERSONALITIES[value]?.icon || '❓'}
        </div>
        <div>
          <strong style={{ color: '#e2e8f0', fontSize: '0.85rem', display: 'block', marginBottom: '4px' }}>
            {PERSONALITIES[value]?.name || value} <span style={{ color: '#64748b', fontWeight: 'normal' }}>preview</span>
          </strong>
          <p style={{ margin: 0, color: '#cbd5e1', fontSize: '0.9rem', fontStyle: 'italic', lineHeight: 1.5 }}>
            "{PERSONALITIES[value]?.sample || 'Hello.'}"
          </p>
        </div>
      </div>
    </div>
  );
}
