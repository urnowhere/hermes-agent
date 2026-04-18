import { useState } from 'react';

interface SkillItem {
  id: string;
  name: string;
  description: string;
  source_type?: string;
  trust_level?: string;
  loaded?: boolean;
}

interface SkillListProps {
  title: string;
  description: string;
  skills: SkillItem[];
  onLoad?: (name: string) => Promise<void>;
  onUnload?: (name: string) => Promise<void>;
  onViewDetail?: (name: string) => void;
}

const TRUST_COLORS: Record<string, { bg: string; color: string }> = {
  trusted: { bg: 'rgba(34, 197, 94, 0.15)', color: '#86efac' },
  verified: { bg: 'rgba(56, 189, 248, 0.15)', color: '#7dd3fc' },
  community: { bg: 'rgba(251, 191, 36, 0.15)', color: '#fde68a' },
  unknown: { bg: 'rgba(100, 116, 139, 0.15)', color: '#94a3b8' },
};

export function SkillList({ title, description, skills, onLoad, onUnload, onViewDetail }: SkillListProps) {
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const btnStyle: React.CSSProperties = {
    background: 'rgba(255, 255, 255, 0.06)',
    border: '1px solid rgba(255, 255, 255, 0.1)',
    color: '#a5b4fc',
    padding: '5px 12px',
    borderRadius: '8px',
    cursor: 'pointer',
    fontSize: '0.8rem',
    transition: 'all 0.2s',
  };

  return (
    <section style={{
      background: 'rgba(255, 255, 255, 0.03)',
      border: '1px solid rgba(255, 255, 255, 0.06)',
      borderRadius: '16px',
      padding: '20px',
    }} aria-label={title}>
      <div style={{ marginBottom: '16px' }}>
        <h2 style={{ margin: 0, color: '#e2e8f0', fontSize: '1.1rem' }}>🧩 {title}</h2>
        <p style={{ margin: '4px 0 0', color: '#64748b', fontSize: '0.85rem' }}>{description}</p>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
        {skills.map((skill) => {
          const trustStyle = TRUST_COLORS[skill.trust_level ?? 'unknown'] ?? TRUST_COLORS.unknown;
          const isExpanded = expandedId === skill.id;

          return (
            <div key={skill.id} style={{
              padding: '14px 16px',
              background: 'rgba(255, 255, 255, 0.04)',
              border: `1px solid ${isExpanded ? 'rgba(129, 140, 248, 0.3)' : 'rgba(255, 255, 255, 0.06)'}`,
              borderRadius: '14px',
              transition: 'all 0.2s',
              cursor: 'pointer',
            }}
            onClick={() => setExpandedId(isExpanded ? null : skill.id)}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div style={{ flex: 1 }}>
                  <strong style={{ color: '#e2e8f0', fontSize: '0.95rem' }}>{skill.name}</strong>
                  <div style={{ display: 'flex', gap: '8px', marginTop: '4px' }}>
                    {skill.source_type && (
                      <span style={{ fontSize: '0.75rem', color: '#64748b', background: 'rgba(0,0,0,0.2)', padding: '1px 6px', borderRadius: '4px' }}>
                        {skill.source_type}
                      </span>
                    )}
                    {skill.trust_level && (
                      <span style={{ fontSize: '0.75rem', color: trustStyle.color, background: trustStyle.bg, padding: '1px 6px', borderRadius: '4px' }}>
                        {skill.trust_level}
                      </span>
                    )}
                    {skill.loaded && (
                      <span style={{ fontSize: '0.75rem', color: '#86efac', background: 'rgba(34, 197, 94, 0.15)', padding: '1px 6px', borderRadius: '4px' }}>
                        ● loaded
                      </span>
                    )}
                  </div>
                </div>
                <div style={{ display: 'flex', gap: '6px' }} onClick={(e) => e.stopPropagation()}>
                  {skill.loaded && onUnload ? (
                    <button type="button" onClick={() => onUnload(skill.name)} style={{ ...btnStyle, color: '#fca5a5' }}>Unload</button>
                  ) : onLoad ? (
                    <button type="button" onClick={() => onLoad(skill.name)} style={{ ...btnStyle, color: '#86efac' }}>Load</button>
                  ) : null}
                </div>
              </div>

              {isExpanded && (
                <div style={{ marginTop: '10px', paddingTop: '10px', borderTop: '1px solid rgba(255,255,255,0.06)' }}>
                  <p style={{ color: '#94a3b8', fontSize: '0.85rem', margin: 0, lineHeight: 1.5 }}>{skill.description}</p>
                  {onViewDetail && (
                    <button type="button" onClick={(e) => { e.stopPropagation(); onViewDetail(skill.name); }} style={{ ...btnStyle, marginTop: '8px' }}>
                      View full detail →
                    </button>
                  )}
                </div>
              )}
            </div>
          );
        })}
        {skills.length === 0 && (
          <p style={{ color: '#475569', textAlign: 'center', padding: '24px', fontSize: '0.9rem' }}>No skills installed.</p>
        )}
      </div>
    </section>
  );
}
