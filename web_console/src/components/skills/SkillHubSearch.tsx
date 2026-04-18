import { useState, useEffect } from 'react';
import { apiClient } from '../../lib/api';
import { toastStore } from '../../store/toastStore';
import { LoadingSpinner } from '../shared/LoadingSpinner';

interface HubSkill {
  name: string;
  description: string;
  source: string;
  trust_level: string;
  identifier: string;
  tags: string[];
}

interface SkillHubSearchProps {
  onInstallComplete: () => void;
}

const TRUST_COLORS: Record<string, { bg: string; color: string }> = {
  trusted: { bg: 'rgba(34, 197, 94, 0.15)', color: '#86efac' },
  verified: { bg: 'rgba(56, 189, 248, 0.15)', color: '#7dd3fc' },
  community: { bg: 'rgba(251, 191, 36, 0.15)', color: '#fde68a' },
  builtin: { bg: 'rgba(56, 189, 248, 0.15)', color: '#7dd3fc' },
  unknown: { bg: 'rgba(100, 116, 139, 0.15)', color: '#94a3b8' },
};

export function SkillHubSearch({ onInstallComplete }: SkillHubSearchProps) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<HubSkill[]>([]);
  const [loading, setLoading] = useState(true);
  const [installing, setInstalling] = useState<string | null>(null);
  const [hasSearched, setHasSearched] = useState(false);

  useEffect(() => {
    // Initial fetch for top / discovered skills
    fetchSkills('');
  }, []);

  const fetchSkills = async (searchQuery: string) => {
    setLoading(true);
    try {
      const res = await apiClient.get<{ ok: boolean; results: HubSkill[] }>(
        `/skills/hub/search?q=${encodeURIComponent(searchQuery)}`
      );
      if (res.ok) setResults(res.results || []);
    } catch (err) {
      toastStore.error('Search Failed', err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
      setHasSearched(true);
    }
  };

  const handleSearch = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    fetchSkills(query);
  };

  const handleInstall = async (skill: HubSkill) => {
    setInstalling(skill.identifier);
    try {
      const res = await apiClient.post<{ ok: boolean; installed: string; path: string }>(
        '/skills/hub/install',
        { identifier: skill.identifier }
      );
      if (res.ok) {
        toastStore.success('Skill Installed', `${skill.name} has been installed and is ready to load.`);
        onInstallComplete();
      }
    } catch (err) {
      toastStore.error('Installation Failed', err instanceof Error ? err.message : String(err));
    } finally {
      setInstalling(null);
    }
  };

  return (
    <section
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: '24px',
        animation: 'fadeInDown 0.3s ease-out forwards',
      }}
    >
      <form onSubmit={handleSearch} style={{ display: 'flex', gap: '8px' }}>
        <input
          type="text"
          placeholder="Search for skills, capabilities, tools..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={{
            flex: 1,
            padding: '12px 16px',
            background: 'rgba(0,0,0,0.2)',
            border: '1px solid rgba(255,255,255,0.1)',
            borderRadius: '12px',
            color: '#f8fafc',
            outline: 'none',
            fontSize: '1rem',
            transition: 'border-color 0.2s',
          }}
          onFocus={(e) => (e.target.style.borderColor = 'rgba(129, 140, 248, 0.5)')}
          onBlur={(e) => (e.target.style.borderColor = 'rgba(255, 255, 255, 0.1)')}
        />
        <button
          type="submit"
          disabled={loading}
          style={{
            padding: '0 24px',
            background: 'rgba(99, 102, 241, 0.15)',
            border: '1px solid rgba(99, 102, 241, 0.3)',
            color: '#818cf8',
            borderRadius: '12px',
            cursor: loading ? 'wait' : 'pointer',
            opacity: loading ? 0.7 : 1,
            fontWeight: 600,
            fontSize: '1rem',
            transition: 'all 0.2s',
          }}
        >
          {loading && query ? 'Searching...' : 'Search'}
        </button>
      </form>

      {loading && !query && (
        <div style={{ padding: '40px', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '16px' }}>
          <LoadingSpinner message="Fetching skills from the global registry... (This may take up to 10s)" />
        </div>
      )}

      {loading && query && (
         <div style={{ padding: '20px', display: 'flex', justifyContent: 'center' }}>
           <LoadingSpinner message="Searching..." />
         </div>
      )}

      {!loading && hasSearched && results.length === 0 && (
        <div style={{ padding: '40px', textAlign: 'center', color: '#64748b', background: 'rgba(0,0,0,0.1)', borderRadius: '16px' }}>
          <h3>No skills found matching "{query}"</h3>
          <p>Try using different keywords or checking a specific repo tap.</p>
        </div>
      )}

      {!loading && results.length > 0 && (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))',
            gap: '16px',
            alignItems: 'stretch',
          }}
        >
          {results.map((skill) => {
            const trustStyle = TRUST_COLORS[skill.trust_level] || TRUST_COLORS.unknown;
            const isInstalling = installing === skill.identifier;

            return (
              <div
                key={skill.identifier}
                style={{
                  padding: '20px',
                  background: 'rgba(255, 255, 255, 0.03)',
                  border: '1px solid rgba(255, 255, 255, 0.08)',
                  borderRadius: '16px',
                  display: 'flex',
                  flexDirection: 'column',
                  justifyContent: 'space-between',
                  gap: '16px',
                  transition: 'transform 0.2s, box-shadow 0.2s, background 0.2s',
                  cursor: 'default',
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.transform = 'translateY(-2px)';
                  e.currentTarget.style.boxShadow = '0 8px 24px rgba(0,0,0,0.2)';
                  e.currentTarget.style.background = 'rgba(255, 255, 255, 0.05)';
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.transform = 'none';
                  e.currentTarget.style.boxShadow = 'none';
                  e.currentTarget.style.background = 'rgba(255, 255, 255, 0.03)';
                }}
              >
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '12px' }}>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                      <strong style={{ color: '#f8fafc', fontSize: '1.2rem', lineHeight: 1.2 }}>{skill.name}</strong>
                      <span style={{ color: '#64748b', fontSize: '0.75rem', fontFamily: 'monospace' }}>{skill.identifier}</span>
                    </div>
                  </div>

                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', marginBottom: '16px' }}>
                    {skill.source === 'official' && (
                      <span style={{ fontSize: '0.7rem', fontWeight: 600, background: 'rgba(56, 189, 248, 0.15)', color: '#38bdf8', padding: '2px 8px', borderRadius: '12px' }}>
                        ★ Official
                      </span>
                    )}
                    <span style={{ fontSize: '0.7rem', fontWeight: 600, background: trustStyle.bg, color: trustStyle.color, padding: '2px 8px', borderRadius: '12px', textTransform: 'capitalize' }}>
                      {skill.trust_level}
                    </span>
                    {skill.tags?.slice(0, 3).map((tag) => (
                      <span key={tag} style={{ fontSize: '0.7rem', background: 'rgba(255,255,255,0.05)', color: '#94a3b8', padding: '2px 8px', borderRadius: '12px' }}>
                        {tag}
                      </span>
                    ))}
                    {(skill.tags?.length || 0) > 3 && (
                      <span style={{ fontSize: '0.7rem', color: '#64748b', padding: '2px 4px' }}>
                        +{skill.tags.length - 3}
                      </span>
                    )}
                  </div>

                  <p style={{ margin: 0, color: '#cbd5e1', fontSize: '0.9rem', lineHeight: 1.5, display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                    {skill.description}
                  </p>
                </div>

                <div style={{ paddingTop: '8px', borderTop: '1px solid rgba(255,255,255,0.05)', display: 'flex', justifyContent: 'flex-end' }}>
                  <button
                    onClick={() => handleInstall(skill)}
                    disabled={installing !== null}
                    style={{
                      padding: '8px 20px',
                      background: isInstalling ? 'rgba(34, 197, 94, 0.2)' : 'rgba(34, 197, 94, 0.1)',
                      border: '1px solid rgba(34, 197, 94, 0.3)',
                      color: '#86efac',
                      borderRadius: '8px',
                      cursor: installing !== null ? 'not-allowed' : 'pointer',
                      fontSize: '0.9rem',
                      fontWeight: 600,
                      width: '100%',
                      transition: 'all 0.2s',
                      opacity: installing !== null && !isInstalling ? 0.5 : 1,
                    }}
                  >
                    {isInstalling ? 'Installing...' : 'Install Skill'}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
