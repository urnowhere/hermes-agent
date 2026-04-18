import { useEffect, useState } from 'react';
import { apiClient } from '../lib/api';
import { toastStore } from '../store/toastStore';
import { SkillList } from '../components/skills/SkillList';
import { SkillHubSearch } from '../components/skills/SkillHubSearch';
import { SkillEditor } from '../components/skills/SkillEditor';
import { SkillConfigPanel } from '../components/skills/SkillConfigPanel';

interface SkillSummary {
  name: string;
  description: string;
  source_type?: string;
  trust_level?: string;
}

interface SkillDetailResponse {
  ok: boolean;
  skill?: { name: string; description?: string; content?: string };
}

interface SkillsResponse {
  ok: boolean;
  skills: SkillSummary[];
}

interface SessionSkillsResponse {
  ok: boolean;
  skills?: Array<{ name: string }>;
}

export function SkillsPage() {
  const [skills, setSkills] = useState<Array<{ id: string; name: string; description: string; source_type?: string; trust_level?: string; loaded?: boolean }>>([]);
  const [detailContent, setDetailContent] = useState<string | null>(null);
  const [detailName, setDetailName] = useState('');
  const [latestSessionId, setLatestSessionId] = useState<string | null>(null);
  const [isEditorOpen, setIsEditorOpen] = useState(false);

  const refreshSkills = async () => {
    try {
      // Get the latest session ID for session-scoped skill operations
      const sessionsRes = await apiClient.get<{ ok: boolean; sessions?: Array<{ session_id: string }> }>('/sessions?limit=1').catch(() => null);
      const sessionId = sessionsRes?.sessions?.[0]?.session_id ?? null;
      setLatestSessionId(sessionId);

      const [skillsRes, sessionRes] = await Promise.all([
        apiClient.get<SkillsResponse>('/skills').catch(() => null),
        sessionId ? apiClient.get<SessionSkillsResponse>(`/skills/session/${sessionId}`).catch(() => null) : null,
      ]);

      const loadedNames = new Set(sessionRes?.skills?.map((s) => s.name) ?? []);

      if (skillsRes?.ok && skillsRes.skills.length > 0) {
        setSkills(
          skillsRes.skills.map((skill, index) => ({
            id: `skill-${index}`,
            name: skill.name,
            description: skill.description ?? 'No description',
            source_type: skill.source_type,
            trust_level: skill.trust_level,
            loaded: loadedNames.has(skill.name),
          }))
        );
      }
    } catch {
      // keep existing state
    }
  };

  useEffect(() => {
    refreshSkills();
  }, []);

  const handleLoad = async (name: string) => {
    if (!latestSessionId) {
      toastStore.error('No Session', 'Cannot load skill: No active session found.');
      return;
    }
    await apiClient.post(`/skills/${encodeURIComponent(name)}/load`, { session_id: latestSessionId });
    await refreshSkills();
  };

  const handleUnload = async (name: string) => {
    if (!latestSessionId) return;
    await apiClient.del(`/skills/session/${latestSessionId}/${encodeURIComponent(name)}`);
    await refreshSkills();
  };

  const handleViewDetail = async (name: string) => {
    try {
      const res = await apiClient.get<SkillDetailResponse>(`/skills/${encodeURIComponent(name)}`);
      if (res.ok && res.skill) {
        setDetailName(name);
        setDetailContent(res.skill.content ?? res.skill.description ?? 'No content available.');
      }
    } catch {
      setDetailContent('Failed to load skill details.');
    }
  };

  const [activeTab, setActiveTab] = useState<'storefront' | 'installed' | 'config'>('storefront');

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px', flex: 1, overflowY: 'auto', minHeight: 0, paddingRight: '8px', paddingBottom: '20px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <h1 style={{ margin: '0 0 8px 0', fontSize: '1.5rem', color: '#f8fafc' }}>Skills & Capabilities</h1>
          <p style={{ margin: 0, color: '#94a3b8', fontSize: '0.9rem' }}>
            Enhance your agent with official and community skills or create your own.
          </p>
        </div>
        {activeTab === 'installed' && (
          <button
            onClick={() => setIsEditorOpen(true)}
            style={{
              padding: '10px 16px',
              background: 'rgba(99, 102, 241, 0.15)',
              border: '1px solid rgba(99, 102, 241, 0.3)',
              color: '#818cf8',
              borderRadius: '10px',
              cursor: 'pointer',
              fontWeight: 500,
              transition: 'all 0.2s',
            }}
          >
            + Create Skill
          </button>
        )}
      </div>

      <div style={{ display: 'flex', borderBottom: '1px solid rgba(255, 255, 255, 0.1)', gap: '20px', marginBottom: '10px' }}>
        <button
          onClick={() => setActiveTab('storefront')}
          style={{
            padding: '10px 4px',
            background: 'none',
            border: 'none',
            borderBottom: activeTab === 'storefront' ? '2px solid #818cf8' : '2px solid transparent',
            color: activeTab === 'storefront' ? '#f8fafc' : '#94a3b8',
            fontSize: '1rem',
            fontWeight: activeTab === 'storefront' ? 600 : 400,
            cursor: 'pointer',
            transition: 'all 0.2s'
          }}
        >
          🌟 Skills Storefront
        </button>
        <button
          onClick={() => setActiveTab('installed')}
          style={{
            padding: '10px 4px',
            background: 'none',
            border: 'none',
            borderBottom: activeTab === 'installed' ? '2px solid #818cf8' : '2px solid transparent',
            color: activeTab === 'installed' ? '#f8fafc' : '#94a3b8',
            fontSize: '1rem',
            fontWeight: activeTab === 'installed' ? 600 : 400,
            cursor: 'pointer',
            transition: 'all 0.2s'
          }}
        >
          📦 Installed & Local
        </button>
        <button
          onClick={() => setActiveTab('config')}
          style={{
            padding: '10px 4px',
            background: 'none',
            border: 'none',
            borderBottom: activeTab === 'config' ? '2px solid #818cf8' : '2px solid transparent',
            color: activeTab === 'config' ? '#f8fafc' : '#94a3b8',
            fontSize: '1rem',
            fontWeight: activeTab === 'config' ? 600 : 400,
            cursor: 'pointer',
            transition: 'all 0.2s'
          }}
        >
          ⚙️ Configuration
        </button>
      </div>

      {activeTab === 'storefront' && (
        <SkillHubSearch onInstallComplete={refreshSkills} />
      )}

      {activeTab === 'installed' && (
        <SkillList
          title="Local & Installed Skills"
          description="Active skills available for this session."
          skills={skills}
          onLoad={handleLoad}
          onUnload={handleUnload}
          onViewDetail={handleViewDetail}
        />
      )}

      {activeTab === 'config' && (
        <SkillConfigPanel />
      )}

      {isEditorOpen && (
        <SkillEditor
          onClose={() => setIsEditorOpen(false)}
          onCreateComplete={refreshSkills}
        />
      )}

      {/* Skill Detail Modal */}
      {detailContent !== null && (
        <div style={{
          position: 'fixed', inset: 0, zIndex: 1000,
          background: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(8px)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          padding: '24px',
        }} onClick={() => setDetailContent(null)}>
          <div style={{
            background: 'rgba(15, 23, 42, 0.95)',
            border: '1px solid rgba(129, 140, 248, 0.2)',
            borderRadius: '20px',
            padding: '24px',
            maxWidth: '700px', width: '100%',
            maxHeight: '70vh', overflow: 'auto',
          }} onClick={(e) => e.stopPropagation()}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
              <h3 style={{ margin: 0, color: '#e2e8f0' }}>🧩 {detailName}</h3>
              <button type="button" onClick={() => setDetailContent(null)} style={{
                background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.1)',
                color: '#94a3b8', borderRadius: '8px', padding: '4px 10px', cursor: 'pointer',
              }}>✕ Close</button>
            </div>
            <pre style={{ color: '#cbd5e1', fontSize: '0.85rem', whiteSpace: 'pre-wrap', lineHeight: 1.6, margin: 0 }}>{detailContent}</pre>
          </div>
        </div>
      )}
    </div>
  );
}
