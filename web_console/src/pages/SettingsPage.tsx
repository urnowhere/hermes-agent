import { useEffect, useState } from 'react';
import { apiClient } from '../lib/api';
import { SettingsForm, type SettingCategory } from '../components/settings/SettingsForm';
import { ModelPicker } from '../components/settings/ModelPicker';
import { ProviderManager } from '../components/settings/ProviderManager';
import { ApiKeyManager } from '../components/settings/ApiKeyManager';
import { ProfileManager } from '../components/settings/ProfileManager';
import { SystemManager } from '../components/settings/SystemManager';
import { PluginList } from '../components/settings/PluginList';
import { McpServerList } from '../components/settings/McpServerList';
import { ThemeSettings } from '../components/settings/ThemeSettings';
import { toastStore } from '../store/toastStore';

interface SettingsResponse {
  ok: boolean;
  settings?: Record<string, any>;
}

function buildCategories(s: Record<string, any>): SettingCategory[] {
  return [
    {
      id: 'general',
      label: 'General',
      fields: [
        { id: 'model.default', label: 'Default Model', type: 'text', value: String(s.model?.default || s.model?.name || (typeof s.model === 'string' ? s.model : '')) },
        { id: 'model.provider', label: 'Inference Provider', type: 'text', value: String(s.model?.provider || s.provider || 'auto') },
        { id: 'model.base_url', label: 'Custom Base URL', type: 'text', value: String(s.model?.base_url ?? '') },
        { id: 'timezone', label: 'Timezone (IANA)', type: 'text', value: String(s.timezone ?? ''), placeholder: 'e.g. America/New_York (empty = server local)' },
      ]
    },
    {
      id: 'behavior',
      label: 'Agent Behavior',
      fields: [
        { id: 'display.personality', label: 'Personality', type: 'select', value: String(s.display?.personality ?? 'kawaii'), options: ['helpful', 'concise', 'technical', 'creative', 'teacher', 'kawaii', 'catgirl', 'pirate', 'shakespeare', 'surfer', 'noir', 'uwu', 'philosopher', 'hype'] },
        { id: 'agent.reasoning_effort', label: 'Reasoning Effort', type: 'select', value: String(s.agent?.reasoning_effort ?? 'medium'), options: ['xhigh', 'high', 'medium', 'low', 'minimal', 'none'] },
        { id: 'agent.service_tier', label: 'Fast Mode / Priority Processing', type: 'select', value: String(s.agent?.service_tier ?? 'normal'), options: ['normal', 'fast'] },
        { id: 'agent.verbose', label: 'Verbose Logging', type: 'boolean', value: Boolean(s.agent?.verbose ?? false) },
        { id: 'agent.max_turns', label: 'Max Iterations per Turn', type: 'number', value: Number(s.agent?.max_turns ?? 25) },
        { id: 'agent.tool_use_enforcement', label: 'Tool Use Enforcement', type: 'text', value: String(s.agent?.tool_use_enforcement ?? 'auto'), placeholder: 'auto, true, false, or substrings' },
        { id: 'approvals.mode', label: 'Approval Mode', type: 'select', value: String(s.approvals?.mode ?? 'manual'), options: ['manual', 'smart', 'off'] },
        { id: 'approvals.timeout', label: 'Approval Timeout (s)', type: 'number', value: Number(s.approvals?.timeout ?? 60) },
        { id: 'prefill_messages_file', label: 'Prefill Messages File', type: 'text', value: String(s.prefill_messages_file ?? ''), placeholder: 'Path to JSON messages file' },
      ]
    },
    {
      id: 'optimization',
      label: 'Optimization',
      fields: [
        { id: 'compression.enabled', label: 'Context Compression', type: 'boolean', value: Boolean(s.compression?.enabled ?? true) },
        { id: 'compression.threshold', label: 'Compression Threshold (%)', type: 'text', value: String(s.compression?.threshold ?? '0.50') },
        { id: 'compression.target_ratio', label: 'Compression Target Ratio', type: 'text', value: String(s.compression?.target_ratio ?? '0.20') },
        { id: 'compression.protect_last_n', label: 'Protect Last N Recents', type: 'number', value: Number(s.compression?.protect_last_n ?? 20) },
        { id: 'memory.memory_enabled', label: 'Episodic Memory', type: 'boolean', value: Boolean(s.memory?.memory_enabled ?? true) },
        { id: 'memory.user_profile_enabled', label: 'User Profile Learning', type: 'boolean', value: Boolean(s.memory?.user_profile_enabled ?? true) },
        { id: 'memory.memory_char_limit', label: 'Memory Char Limit', type: 'number', value: Number(s.memory?.memory_char_limit ?? 2200) },
        { id: 'memory.user_char_limit', label: 'User Char Limit', type: 'number', value: Number(s.memory?.user_char_limit ?? 1375) },
        { id: 'memory.provider', label: 'Memory Provider', type: 'select', value: String(s.memory?.provider ?? 'built-in'), options: ['built-in', 'honcho'] },
        { id: 'session_store_max_age_days', label: 'Session Retention (Days)', type: 'number', value: Number(s.session_store_max_age_days ?? 90) },
        { id: 'checkpoints.enabled', label: 'File Checkpoints', type: 'boolean', value: Boolean(s.checkpoints?.enabled ?? true) },
        { id: 'checkpoints.max_snapshots', label: 'Max Checkpoints', type: 'number', value: Number(s.checkpoints?.max_snapshots ?? 50) },
        { id: 'smart_model_routing.enabled', label: 'Smart Model Routing', type: 'boolean', value: Boolean(s.smart_model_routing?.enabled ?? false) },
      ]
    },
    {
      id: 'terminal',
      label: 'Terminal Sandbox',
      fields: [
        { id: 'terminal.backend', label: 'Terminal Backend', type: 'select', value: String(s.terminal?.backend ?? 'local'), options: ['local', 'docker', 'ssh', 'modal', 'daytona', 'singularity'] },
        { id: 'terminal.timeout', label: 'Terminal Timeout (s)', type: 'number', value: Number(s.terminal?.timeout ?? 180) },
        { id: 'terminal.persistent_shell', label: 'Persistent Shell', type: 'boolean', value: Boolean(s.terminal?.persistent_shell ?? true) },
        { id: 'terminal.docker_image', label: 'Docker Image', type: 'text', value: String(s.terminal?.docker_image ?? 'nikolaik/python-nodejs:python3.11-nodejs20') },
        { id: 'terminal.container_cpu', label: 'Container CPU', type: 'number', value: Number(s.terminal?.container_cpu ?? 1) },
        { id: 'terminal.container_memory', label: 'Container Memory (MB)', type: 'number', value: Number(s.terminal?.container_memory ?? 5120) },
        { id: 'terminal.container_disk', label: 'Container Disk (MB)', type: 'number', value: Number(s.terminal?.container_disk ?? 51200) },
        { id: 'terminal.container_persistent', label: 'Container Persistent FS', type: 'boolean', value: Boolean(s.terminal?.container_persistent ?? true) },
        { id: 'terminal.docker_mount_cwd_to_workspace', label: 'Mount CWD to Workspace', type: 'boolean', value: Boolean(s.terminal?.docker_mount_cwd_to_workspace ?? false) },
      ]
    },
    {
      id: 'browser',
      label: 'Browser Automation',
      fields: [
        { id: 'browser.backend', label: 'Browser Backend', type: 'select', value: String(s.browser?.backend ?? 'playwright'), options: ['playwright', 'camoufox', 'browserbase'] },
        { id: 'browser.inactivity_timeout', label: 'Inactivity Timeout (s)', type: 'number', value: Number(s.browser?.inactivity_timeout ?? 120) },
        { id: 'browser.command_timeout', label: 'Command Timeout (s)', type: 'number', value: Number(s.browser?.command_timeout ?? 30) },
        { id: 'browser.record_sessions', label: 'Record Sessions (WebM)', type: 'boolean', value: Boolean(s.browser?.record_sessions ?? false) },
        { id: 'browser.allow_private_urls', label: 'Allow Private URLs (Localhost)', type: 'boolean', value: Boolean(s.browser?.allow_private_urls ?? false) },
      ]
    },
    {
      id: 'display',
      label: 'Display & Privacy',
      fields: [
        { id: 'display.skin', label: 'CLI Theme Skin', type: 'select', value: String(s.display?.skin ?? 'default'), options: ['default', 'ares', 'mono', 'slate'] },
        { id: 'display.tool_progress', label: 'Tool Progress Display', type: 'select', value: String(s.display?.tool_progress ?? 'all'), options: ['off', 'new', 'all', 'verbose'] },
        { id: 'display.streaming', label: 'Stream Tokens', type: 'boolean', value: Boolean(s.display?.streaming ?? false) },
        { id: 'display.show_reasoning', label: 'Show Reasoning', type: 'boolean', value: Boolean(s.display?.show_reasoning ?? false) },
        { id: 'display.show_cost', label: 'Show Cost', type: 'boolean', value: Boolean(s.display?.show_cost ?? false) },
        { id: 'display.compact', label: 'Compact Mode', type: 'boolean', value: Boolean(s.display?.compact ?? false) },
        { id: 'display.bell_on_complete', label: 'Bell on Complete', type: 'boolean', value: Boolean(s.display?.bell_on_complete ?? false) },
        { id: 'display.tool_preview_length', label: 'Tool Preview Length (0=full)', type: 'number', value: Number(s.display?.tool_preview_length ?? 0) },
        { id: 'privacy.redact_pii', label: 'Redact PII', type: 'boolean', value: Boolean(s.privacy?.redact_pii ?? false) },
        { id: 'security.redact_secrets', label: 'Redact Secrets in Context', type: 'boolean', value: Boolean(s.security?.redact_secrets ?? true) },
        { id: 'security.tirith_enabled', label: 'Pre-exec Tirith Scan', type: 'boolean', value: Boolean(s.security?.tirith_enabled ?? true) },
      ]
    },
    {
      id: 'advanced',
      label: 'Advanced Integration',
      fields: [
        { id: 'credential_pool', label: 'Credential Pool Config (JSON)', type: 'textarea', value: typeof s.credential_pool === 'object' ? JSON.stringify(s.credential_pool, null, 2) : String(s.credential_pool ?? ''), placeholder: '{\n  "provider": ["key1", "key2"]\n}' },
        { id: 'toolsets', label: 'Active Toolsets (comma separated)', type: 'text', value: Array.isArray(s.toolsets) ? s.toolsets.join(', ') : '', placeholder: 'e.g. hermes-cli, web, vision' },
        { id: 'fallback_providers', label: 'Fallback Providers (comma-separated)', type: 'text', value: Array.isArray(s.fallback_providers) ? s.fallback_providers.join(', ') : '', placeholder: 'e.g. openrouter, anthropic, openai' },
        { id: 'stt.enabled', label: 'Auto Speech-to-Text', type: 'boolean', value: Boolean(s.stt?.enabled ?? true) },
        { id: 'stt.provider', label: 'STT Provider', type: 'select', value: String(s.stt?.provider ?? 'local'), options: ['local', 'groq', 'openai'] },
        { id: 'tts.provider', label: 'TTS Provider', type: 'select', value: String(s.tts?.provider ?? 'edge'), options: ['edge', 'elevenlabs', 'openai', 'neutts'] },
        { id: 'delegation.model', label: 'Subagent Model', type: 'text', value: String(s.delegation?.model ?? ''), placeholder: 'Empty = inherit parent model' },
        { id: 'delegation.provider', label: 'Subagent Provider', type: 'text', value: String(s.delegation?.provider ?? ''), placeholder: 'Empty = inherit parent provider' },
        { id: 'delegation.max_iterations', label: 'Subagent Max Iterations', type: 'number', value: Number(s.delegation?.max_iterations ?? 50) },
        { id: 'file_read_max_chars', label: 'File Read Max Chars', type: 'number', value: Number(s.file_read_max_chars ?? 100000) },
        { id: 'cron.wrap_response', label: 'Cron Wrap Response', type: 'boolean', value: Boolean(s.cron?.wrap_response ?? true) },
      ]
    },
  ];
}

export function SettingsPage() {
  const [categories, setCategories] = useState<SettingCategory[] | null>(null);
  const [loading, setLoading] = useState(true);

  const refreshSettings = async () => {
    try {
      const res = await apiClient.get<SettingsResponse>('/settings');
      if (res?.ok && res.settings) {
        setCategories(buildCategories(res.settings));
      }
    } catch (err) {
      toastStore.error('Settings Load Failed', err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refreshSettings();
  }, []);

  const handleSave = async (updates: Record<string, any>) => {
    const payload: any = {};
    for (const [key, val] of Object.entries(updates)) {
      // Handle special array fields that are edited as comma-separated strings
      if (key === 'fallback_providers' || key === 'toolsets') {
        payload[key] = typeof val === 'string'
          ? val.split(',').map((s: string) => s.trim()).filter(Boolean)
          : val;
        continue;
      }
      
      if (key === 'credential_pool' && typeof val === 'string') {
        try {
          payload[key] = val.trim() ? JSON.parse(val) : {};
        } catch {
          toastStore.error('Form Output Error', 'Invalid JSON payload within Credential Pool.');
          return;
        }
        continue;
      }
      const parts = key.split('.');
      let current = payload;
      for (let i = 0; i < parts.length - 1; i++) {
        current[parts[i]] = current[parts[i]] || {};
        current = current[parts[i]];
      }
      current[parts[parts.length - 1]] = val;
    }
    
    try {
      await apiClient.patch('/settings', payload);
      toastStore.success('Settings Saved');
      await refreshSettings();
    } catch (err) {
      toastStore.error('Save Failed', err instanceof Error ? err.message : String(err));
      // Revert optimism if refreshSettings wasn't called successfully
      await refreshSettings();
    }
  };

  if (loading) {
    return (
      <div style={{ 
        display: 'flex', 
        alignItems: 'center', 
        justifyContent: 'center', 
        height: '200px', 
        color: '#64748b' 
      }}>
        Loading settings…
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', maxWidth: '800px', margin: '0 auto', flex: 1, overflowY: 'auto', minHeight: 0, paddingBottom: '30px', width: '100%' }}>
      <ThemeSettings />
      <ModelPicker />
      <ProviderManager />
      {categories && <SettingsForm categories={categories} onSave={handleSave} />}
      <ApiKeyManager />
      <ProfileManager />
      <SystemManager />
      <PluginList />
      <McpServerList />
    </div>
  );
}
