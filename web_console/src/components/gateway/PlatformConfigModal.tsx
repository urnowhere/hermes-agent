import { useState, useEffect } from 'react';
import { apiClient } from '../../lib/api';
import { toastStore } from '../../store/toastStore';

interface PlatformConfigModalProps {
  platformId: string;
  onClose: () => void;
  onSaved: () => void;
}

export function PlatformConfigModal({ platformId, onClose, onSaved }: PlatformConfigModalProps) {
  const [config, setConfig] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    async function loadConfig() {
      const res = await apiClient.get<any>(`/gateway/platforms/${platformId}/config`);
      if (res?.ok) {
        setConfig(res.config || {});
      } else {
        toastStore.error(`Failed to load ${platformId} configuration.`);
      }
      setLoading(false);
    }
    loadConfig();
  }, [platformId]);

  const handleSave = async () => {
    setSaving(true);
    const res = await apiClient.patch<any>(`/gateway/platforms/${platformId}/config`, config);
    setSaving(false);
    if (res?.ok) {
      toastStore.success(`${platformId} configuration saved.`);
      if (res.reload_required) {
        toastStore.info('Please restart Hermes for changes to take effect.');
      }
      onSaved();
    } else {
      toastStore.error(res?.error?.message || `Failed to save ${platformId} configuration.`);
    }
  };

  const getFieldsForPlatform = () => {
    let fields = [
      { key: 'token', label: 'Bot Token', type: 'password', placeholder: 'Enter API Token' },
    ];
    
    if (platformId === 'telegram') {
      fields.push({ key: 'home_channel', label: 'Home Channel ID', type: 'text', placeholder: 'Optional' });
      fields.push({ key: 'master_user', label: 'Master User ID', type: 'text', placeholder: 'Optional' });
      fields.push({ key: 'webhook_url', label: 'Webhook URL (Overrides Polling)', type: 'text', placeholder: 'https://...' });
      fields.push({ key: 'mention_behavior', label: 'Group Mention Behavior', type: 'text', placeholder: 'always | mentioned | regex' });
      fields.push({ key: 'regex_trigger', label: 'Regex Trigger', type: 'text', placeholder: 'Optional regex pattern' });
    } else if (platformId === 'discord') {
      fields.push({ key: 'home_channel', label: 'Home Channel ID', type: 'text', placeholder: 'Optional' });
      fields.push({ key: 'client_id', label: 'Client ID', type: 'text', placeholder: 'Optional' });
    } else if (platformId === 'slack') {
      fields.push({ key: 'app_token', label: 'App Token (xapp-...)', type: 'password', placeholder: 'Optional' });
      fields.push({ key: 'signing_secret', label: 'Signing Secret', type: 'password', placeholder: 'Optional' });
    } else if (platformId === 'feishu') {
      fields = []; // Remove standard token
      fields.push({ key: 'app_id', label: 'App ID', type: 'text', placeholder: 'cli_...' });
      fields.push({ key: 'app_secret', label: 'App Secret', type: 'password', placeholder: 'Secret' });
      fields.push({ key: 'verification_token', label: 'Verification Token', type: 'password', placeholder: 'Optional' });
      fields.push({ key: 'encrypt_key', label: 'Encrypt Key', type: 'password', placeholder: 'Optional' });
    } else if (platformId === 'weixin') {
      fields = []; // Remove standard token
      fields.push({ key: 'token', label: 'WeChat Token', type: 'password', placeholder: 'Token' });
      fields.push({ key: 'account_id', label: 'Account ID', type: 'text', placeholder: 'gh_...' });
      fields.push({ key: 'base_url', label: 'Base URL', type: 'text', placeholder: 'Optional (e.g. https://api.weixin.qq.com)' });
    } else if (platformId === 'wecom') {
      fields = []; // Remove standard token
      fields.push({ key: 'bot_id', label: 'Bot ID', type: 'text', placeholder: 'Bot ID' });
      fields.push({ key: 'secret', label: 'Secret', type: 'password', placeholder: 'Secret' });
    } else if (platformId === 'mattermost') {
      fields.push({ key: 'url', label: 'Mattermost URL', type: 'text', placeholder: 'https://...' });
      fields.push({ key: 'bot_id', label: 'Bot ID', type: 'text', placeholder: 'Optional' });
      fields.push({ key: 'webhook_port', label: 'Webhook Port', type: 'text', placeholder: 'Optional' });
    } else if (platformId === 'matrix') {
      fields.push({ key: 'homeserver', label: 'Homeserver URL', type: 'text', placeholder: 'https://matrix.org' });
      fields.push({ key: 'username', label: 'Username', type: 'text', placeholder: '@bot:matrix.org' });
      fields.push({ key: 'password', label: 'Password (if no token)', type: 'password', placeholder: 'Optional' });
    } else if (platformId === 'homeassistant') {
      fields.push({ key: 'url', label: 'Home Assistant URL', type: 'text', placeholder: 'https://...' });
      fields.push({ key: 'agent_id', label: 'Conversation Agent ID', type: 'text', placeholder: 'Optional' });
    } else if (platformId === 'signal') {
      fields = [];
      fields.push({ key: 'http_url', label: 'Signal-CLI HTTP URL', type: 'text', placeholder: 'http://127.0.0.1:8080' });
      fields.push({ key: 'account', label: 'Phone Number Account', type: 'text', placeholder: '+1234567890' });
    } else if (platformId === 'bluebubbles') {
      fields = [];
      fields.push({ key: 'server_url', label: 'Server URL', type: 'text', placeholder: 'https://...' });
      fields.push({ key: 'password', label: 'Password', type: 'password', placeholder: 'Secret' });
    } else if (platformId === 'email') {
      fields = [];
      fields.push({ key: 'address', label: 'Email Address', type: 'text', placeholder: 'bot@example.com' });
      fields.push({ key: 'imap_host', label: 'IMAP Host', type: 'text', placeholder: 'imap.example.com' });
      fields.push({ key: 'imap_user', label: 'IMAP User', type: 'text', placeholder: 'bot@example.com' });
      fields.push({ key: 'imap_pass', label: 'IMAP Password', type: 'password', placeholder: 'Secret' });
      fields.push({ key: 'smtp_host', label: 'SMTP Host', type: 'text', placeholder: 'smtp.example.com' });
      fields.push({ key: 'smtp_user', label: 'SMTP User', type: 'text', placeholder: 'bot@example.com' });
      fields.push({ key: 'smtp_pass', label: 'SMTP Password', type: 'password', placeholder: 'Secret' });
    } else if (platformId === 'whatsapp' || platformId === 'sms' || platformId === 'webhook' || platformId === 'api_server' || platformId === 'cli') {
      fields = [];
    }
    
    return fields;
  };

  const fields = getFieldsForPlatform();

  const handleFieldChange = (key: string, value: any) => {
    setConfig(prev => ({ ...prev, [key]: value }));
  };

  const inputStyle = {
    width: '100%',
    padding: '8px 12px',
    borderRadius: '8px',
    border: '1px solid rgba(255, 255, 255, 0.1)',
    background: 'rgba(0, 0, 0, 0.2)',
    color: '#e2e8f0',
    fontSize: '0.9rem',
    marginBottom: '12px'
  };

  return (
    <div style={{
      position: 'fixed', top: 0, left: 0, width: '100vw', height: '100vh',
      background: 'rgba(0, 0, 0, 0.6)', backdropFilter: 'blur(4px)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100
    }}>
      <div style={{
        background: '#1e293b', border: '1px solid rgba(255, 255, 255, 0.1)',
        borderRadius: '16px', padding: '24px', width: '400px', maxWidth: '90vw'
      }}>
        <h3 style={{ margin: '0 0 16px', color: '#e2e8f0', textTransform: 'capitalize' }}>
          Configure {platformId}
        </h3>

        {loading ? (
          <p style={{ color: '#94a3b8' }}>Loading configuration...</p>
        ) : (
          <form onSubmit={e => { e.preventDefault(); handleSave(); }}>
            {fields.map(field => (
              <div key={field.key}>
                <label style={{ display: 'block', marginBottom: '6px', fontSize: '0.85rem', color: '#94a3b8' }}>
                  {field.label}
                </label>
                <input
                  type={field.type}
                  value={config[field.key] || ''}
                  placeholder={field.placeholder}
                  onChange={e => handleFieldChange(field.key, e.target.value)}
                  style={inputStyle}
                />
              </div>
            ))}
            
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '12px', marginTop: '16px' }}>
              <button
                type="button"
                onClick={onClose}
                style={{
                  padding: '8px 16px', borderRadius: '8px', background: 'transparent',
                  border: '1px solid rgba(255, 255, 255, 0.1)', color: '#e2e8f0', cursor: 'pointer'
                }}
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={saving}
                style={{
                  padding: '8px 16px', borderRadius: '8px', background: 'rgba(56, 189, 248, 0.2)',
                  border: '1px solid rgba(56, 189, 248, 0.3)', color: '#38bdf8', cursor: 'pointer'
                }}
              >
                {saving ? 'Saving...' : 'Save'}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
