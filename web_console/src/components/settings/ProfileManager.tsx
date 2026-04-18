import { useState, useEffect, useRef } from 'react';
import { apiClient } from '../../lib/api';
import { getBackendUrl } from '../../store/backendStore';
import { toastStore } from '../../store/toastStore';

interface ProfileInfo {
  name: string;
  path: string;
  is_default: boolean;
  gateway_running: boolean;
  model: string | null;
  provider: string | null;
  has_env: boolean;
  skill_count: number;
  alias_path: string | null;
  is_active: boolean;
}

interface ProfilesResponse {
  ok: boolean;
  profiles: ProfileInfo[];
  active_profile: string;
}

export function ProfileManager() {
  const [profiles, setProfiles] = useState<ProfileInfo[]>([]);
  const [activeProfile, setActiveProfile] = useState<string>('default');
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [newProfileName, setNewProfileName] = useState('');
  const fileInputRef = useRef<HTMLInputElement>(null);

  const fetchProfiles = async () => {
    try {
      const res = await apiClient.get<ProfilesResponse>('/profiles');
      if (res?.ok) {
        setProfiles(res.profiles || []);
        setActiveProfile(res.active_profile || 'default');
      } else {
        toastStore.error('Failed to load profiles');
      }
    } catch (e) {
      console.error('Profile fetch error:', e);
      toastStore.error('Network error loading profiles');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchProfiles();
  }, []);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newProfileName.trim()) return;
    
    try {
      const res = await apiClient.post<any>('/profiles', { name: newProfileName.trim() });
      if (res?.ok) {
        toastStore.success(`Profile ${newProfileName} created!`);
        setNewProfileName('');
        setShowCreate(false);
        fetchProfiles();
      } else {
        toastStore.error(res?.error || 'Failed to create profile');
      }
    } catch (e) {
      console.error(e);
      toastStore.error('Error creating profile');
    }
  };

  const handleSetActive = async (name: string) => {
    if (name === activeProfile) return;
    
    if (!confirm(`Set ${name} as the active profile?\n\nNOTE: The gateway API must be restarted for this change to take effect for the web console.`)) {
      return;
    }

    try {
      const res = await apiClient.post<any>('/profiles/active', { name });
      if (res?.ok) {
        toastStore.success(`Active profile set to ${name}`);
        fetchProfiles();
        // Give them a moment to read the toast, then prompt reload
        setTimeout(() => {
          if (confirm('The Hermes Gateway must be restarted to use the new profile. Please restart the backend process, then click OK to reload the page.')) {
            window.location.reload();
          }
        }, 1500);
      } else {
        toastStore.error(res?.error || 'Failed to set active profile');
      }
    } catch (e) {
      console.error(e);
      toastStore.error('Error setting active profile');
    }
  };

  const handleDelete = async (name: string) => {
    if (name === 'default') {
      toastStore.error('Cannot delete the default profile');
      return;
    }
    
    if (!confirm(`Are you sure you want to permanently delete profile "${name}"? This will remove all memories, settings, and skills. This cannot be undone.`)) {
      return;
    }

    try {
      const res = await apiClient.del<any>(`/profiles/${name}`);
      if (res?.ok) {
        toastStore.success(`Profile ${name} deleted`);
        fetchProfiles();
      } else {
        toastStore.error(res?.error || 'Failed to delete profile');
      }
    } catch (e) {
      console.error(e);
      toastStore.error('Error deleting profile');
    }
  };

  const handleExport = (name: string) => {
    // Navigate to the file export API path to prompt standard browser download
    const url = `${getBackendUrl()}/api/gui/profiles/${name}/export`;
    window.open(url, '_blank');
  };

  const handleImport = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    try {
      const formData = new FormData();
      formData.append('file', file);
      // Optional: Ask for a renamed profile name? Left out for inference by backend
      
      setLoading(true);
      const res = await fetch(`${getBackendUrl()}/api/gui/profiles/import`, {
        method: 'POST',
        body: formData
      });
      const data = await res.json();
      
      if (data.ok) {
        toastStore.success(`Profile imported successfully`);
        fetchProfiles();
      } else {
        toastStore.error(data.error || 'Failed to import profile');
        setLoading(false);
      }
    } catch (err) {
      console.error(err);
      toastStore.error('Network error during profile import');
      setLoading(false);
    } finally {
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }
  };

  if (loading) {
    return <p style={{ color: '#94a3b8' }}>Loading profiles...</p>;
  }

  return (
    <section style={{
      background: 'rgba(30, 41, 59, 0.4)',
      border: '1px solid rgba(255, 255, 255, 0.05)',
      borderRadius: '16px',
      padding: '20px',
      marginBottom: '24px'
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '16px' }}>
        <div>
          <h2 style={{ margin: 0, color: '#e2e8f0', fontSize: '1.1rem' }}>👥 Profiles</h2>
          <p style={{ margin: '4px 0 0', color: '#64748b', fontSize: '0.85rem' }}>
            Manage isolated Hermes instances (memories, API keys, skills)
          </p>
        </div>
        <div style={{ display: 'flex', gap: '8px' }}>
          <input 
            type="file" 
            ref={fileInputRef} 
            onChange={handleImport} 
            accept=".tar.gz" 
            style={{ display: 'none' }} 
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            style={{
              padding: '6px 12px', borderRadius: '6px',
              background: 'transparent',
              border: '1px solid rgba(255, 255, 255, 0.2)',
              color: '#cbd5e1', cursor: 'pointer', fontSize: '0.85rem'
            }}
          >
            📥 Import
          </button>
          
          <button
            onClick={() => setShowCreate(true)}
            style={{
              padding: '6px 12px',
              borderRadius: '6px',
              background: 'rgba(56, 189, 248, 0.1)',
              border: '1px solid rgba(56, 189, 248, 0.2)',
              color: '#38bdf8',
              cursor: 'pointer',
              fontSize: '0.85rem'
            }}
          >
            + New Profile
          </button>
        </div>
      </div>

      {showCreate && (
        <div style={{
          background: 'rgba(0,0,0,0.2)', padding: '16px', borderRadius: '8px', 
          marginBottom: '16px', border: '1px solid rgba(255,255,255,0.05)'
        }}>
          <h3 style={{ margin: '0 0 12px', fontSize: '0.95rem', color: '#e2e8f0' }}>Create New Profile</h3>
          <form onSubmit={handleCreate} style={{ display: 'flex', gap: '12px' }}>
            <input
              type="text"
              value={newProfileName}
              onChange={e => setNewProfileName(e.target.value)}
              placeholder="Profile name (e.g. coding-assistant)"
              style={{
                flex: 1, padding: '8px 12px', borderRadius: '6px',
                border: '1px solid rgba(255, 255, 255, 0.1)', background: 'rgba(0, 0, 0, 0.3)',
                color: '#e2e8f0'
              }}
              pattern="^[a-z0-9][a-z0-9_-]{0,63}$"
              title="Lowercase, numbers, hyphens, and underscores only"
              required
            />
            <button type="submit" style={{
              padding: '8px 16px', borderRadius: '6px', background: '#38bdf8',
              border: 'none', color: '#0f172a', fontWeight: 'bold', cursor: 'pointer'
            }}>
              Create
            </button>
            <button type="button" onClick={() => setShowCreate(false)} style={{
              padding: '8px 16px', borderRadius: '6px', background: 'transparent',
              border: '1px solid rgba(255,255,255,0.1)', color: '#cbd5e1', cursor: 'pointer'
            }}>
              Cancel
            </button>
          </form>
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
        {profiles.map(p => (
          <div key={p.name} style={{
            padding: '16px',
            borderRadius: '12px',
            background: 'rgba(0,0,0,0.2)',
            border: `1px solid ${p.is_active ? 'rgba(56, 189, 248, 0.4)' : 'rgba(255,255,255,0.05)'}`,
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center'
          }}>
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
                <strong style={{ color: p.is_active ? '#38bdf8' : '#e2e8f0', fontSize: '1.05rem' }}>
                  {p.name}
                </strong>
                {p.is_default && <span style={{ fontSize: '0.7rem', padding: '2px 6px', background: 'rgba(255,255,255,0.1)', borderRadius: '4px', color: '#94a3b8' }}>DEFAULT</span>}
                {p.is_active && <span style={{ fontSize: '0.7rem', padding: '2px 6px', background: 'rgba(56, 189, 248, 0.2)', borderRadius: '4px', color: '#38bdf8', fontWeight: 'bold' }}>ACTIVE</span>}
              </div>
              <div style={{ display: 'flex', gap: '12px', fontSize: '0.8rem', color: '#64748b' }}>
                <span>📁 {p.path.split('/').pop() === '.hermes' && p.is_default ? '~/.hermes' : `~/.hermes/profiles/${p.name}`}</span>
                <span>🧩 {p.skill_count} skills</span>
                <span>🤖 {p.model || 'Auto'}</span>
              </div>
            </div>
            
            <div style={{ display: 'flex', gap: '8px' }}>
              {!p.is_active && (
                <button
                  onClick={() => handleSetActive(p.name)}
                  style={{
                    padding: '6px 12px', borderRadius: '6px', background: 'transparent',
                    border: '1px solid rgba(255,255,255,0.1)', color: '#e2e8f0',
                    cursor: 'pointer', fontSize: '0.8rem'
                  }}
                  title="Set as sticky active profile"
                >
                  Set Active
                </button>
              )}
              <button
                onClick={() => handleExport(p.name)}
                style={{
                  padding: '6px 12px', borderRadius: '6px', background: 'transparent',
                  border: '1px solid rgba(255,255,255,0.1)', color: '#cbd5e1',
                  cursor: 'pointer', fontSize: '0.8rem'
                }}
                title="Export Profile"
              >
                Export
              </button>
              {!p.is_default && (
                <button
                  onClick={() => handleDelete(p.name)}
                  style={{
                    padding: '6px 12px', borderRadius: '6px', background: 'transparent',
                    border: '1px solid rgba(239, 68, 68, 0.3)', color: '#ef4444',
                    cursor: 'pointer', fontSize: '0.8rem'
                  }}
                  title="Delete Profile"
                >
                  Delete
                </button>
              )}
            </div>
          </div>
        ))}
        {profiles.length === 0 && (
          <p style={{ color: '#64748b', fontStyle: 'italic', margin: 0, padding: '16px', textAlign: 'center' }}>
            No profiles found.
          </p>
        )}
      </div>
    </section>
  );
}
