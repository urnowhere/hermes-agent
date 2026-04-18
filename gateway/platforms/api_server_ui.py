import json


def get_api_server_ui_html(
    api_base_url: str = "/v1",
    requires_api_key: bool = False,
    default_model: str = "hermes-agent",
) -> str:
    """Return a lightweight single-file browser UI for the local API server."""
    config = {
        "apiBaseUrl": api_base_url,
        "requiresApiKey": requires_api_key,
        "defaultModel": default_model,
    }
    config_json = json.dumps(config)
    template = '''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Hermes Browser UI</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #0b1020;
      --panel: rgba(18, 25, 45, 0.92);
      --panel-2: rgba(28, 37, 66, 0.95);
      --border: rgba(140, 170, 255, 0.2);
      --text: #edf2ff;
      --muted: #9ca9c8;
      --accent: #7aa2ff;
      --accent-2: #5eead4;
      --danger: #fb7185;
      --shadow: 0 18px 40px rgba(0,0,0,0.35);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background:
        radial-gradient(circle at top, rgba(122,162,255,0.18), transparent 30%),
        linear-gradient(180deg, #08101f 0%, #090d18 100%);
      color: var(--text);
    }
    .shell {
      max-width: 1200px;
      margin: 0 auto;
      padding: 24px;
      display: grid;
      grid-template-columns: 320px minmax(0, 1fr);
      gap: 24px;
      min-height: 100vh;
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 20px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(18px);
    }
    .sidebar { padding: 20px; display: flex; flex-direction: column; gap: 16px; }
    .brand h1 { margin: 0; font-size: 1.5rem; }
    .brand p, .hint, .status-line { color: var(--muted); margin: 0; line-height: 1.45; }
    .status-chip {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 8px 12px;
      border-radius: 999px;
      background: rgba(94, 234, 212, 0.08);
      color: var(--accent-2);
      border: 1px solid rgba(94, 234, 212, 0.2);
      font-size: 0.92rem;
      width: fit-content;
    }
    .status-chip.offline {
      color: #fda4af;
      background: rgba(251, 113, 133, 0.08);
      border-color: rgba(251, 113, 133, 0.22);
    }
    label { display: block; font-size: 0.92rem; margin-bottom: 6px; color: #cbd5f5; }
    input, textarea, button { font: inherit; }
    input, textarea {
      width: 100%;
      background: rgba(8, 15, 31, 0.9);
      color: var(--text);
      border: 1px solid rgba(140, 170, 255, 0.18);
      border-radius: 14px;
      padding: 12px 14px;
      outline: none;
      transition: border-color 0.2s ease, box-shadow 0.2s ease;
    }
    input:focus, textarea:focus {
      border-color: rgba(122,162,255,0.75);
      box-shadow: 0 0 0 3px rgba(122,162,255,0.15);
    }
    textarea { min-height: 96px; resize: vertical; }
    .controls { display: flex; flex-direction: column; gap: 14px; }
    .row { display: flex; gap: 10px; }
    .row > * { flex: 1; }
    button {
      border: 0;
      border-radius: 14px;
      padding: 12px 14px;
      cursor: pointer;
      background: linear-gradient(135deg, var(--accent), #8b5cf6);
      color: white;
      font-weight: 600;
      transition: transform 0.12s ease, opacity 0.2s ease;
    }
    button:hover { transform: translateY(-1px); }
    button:disabled { cursor: wait; opacity: 0.65; transform: none; }
    button.secondary {
      background: rgba(122, 162, 255, 0.08);
      color: var(--text);
      border: 1px solid rgba(140, 170, 255, 0.18);
    }
    .chat {
      display: grid;
      grid-template-rows: auto minmax(0, 1fr) auto;
      min-height: calc(100vh - 48px);
      overflow: hidden;
    }
    .chat-header {
      padding: 20px 22px;
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: center;
    }
    .messages {
      padding: 20px;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 14px;
      background: linear-gradient(180deg, rgba(10,15,28,0.25), rgba(10,15,28,0.05));
    }
    .message {
      border: 1px solid rgba(140,170,255,0.14);
      border-radius: 18px;
      padding: 14px 16px;
      max-width: min(90%, 880px);
      box-shadow: 0 10px 30px rgba(0,0,0,0.18);
      white-space: pre-wrap;
      word-break: break-word;
    }
    .message.user { align-self: flex-end; background: rgba(30,58,138,0.6); }
    .message.assistant { align-self: flex-start; background: rgba(22,33,62,0.92); }
    .message.tool { align-self: flex-start; background: rgba(16,42,67,0.92); }
    .message.system { align-self: center; background: rgba(76,29,149,0.22); color: #ddd6fe; }
    .meta { font-size: 0.78rem; color: var(--muted); margin-bottom: 8px; text-transform: uppercase; letter-spacing: 0.08em; }
    .composer {
      border-top: 1px solid var(--border);
      padding: 18px 20px 20px;
      display: flex;
      flex-direction: column;
      gap: 12px;
      background: rgba(9, 14, 26, 0.9);
    }
    .composer-actions { display: flex; gap: 10px; justify-content: space-between; align-items: center; }
    .composer-actions .right { display: flex; gap: 10px; }
    .footer-note { color: var(--muted); font-size: 0.86rem; }
    code {
      background: rgba(255,255,255,0.08);
      padding: 2px 6px;
      border-radius: 8px;
    }
    .hidden { display: none !important; }
    @media (max-width: 960px) {
      .shell { grid-template-columns: 1fr; padding: 16px; }
      .chat { min-height: 75vh; }
      .message { max-width: 100%; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <aside class="panel sidebar">
      <div class="brand">
        <h1>Hermes</h1>
        <p>Local browser GUI for the built-in API server.</p>
      </div>

      <div id="health-chip" class="status-chip">Checking connection…</div>
      <p id="status-line" class="status-line">Point your browser at this server and chat with Hermes directly.</p>

      <div class="controls">
        <div>
          <label for="conversation">Conversation ID</label>
          <input id="conversation" placeholder="browser-chat" />
        </div>

        <div>
          <label for="model">Model label</label>
          <input id="model" placeholder="hermes-agent" />
        </div>

        <div id="api-key-wrap" class="hidden">
          <label for="api-key">API key</label>
          <input id="api-key" type="password" placeholder="Bearer token for this server" />
        </div>

        <div>
          <label for="instructions">Optional instructions</label>
          <textarea id="instructions" placeholder="You are Hermes in browser mode. Be concise."></textarea>
        </div>

        <div class="row">
          <button id="new-chat" class="secondary" type="button">New chat</button>
          <button id="save-settings" class="secondary" type="button">Save settings</button>
        </div>
      </div>

      <p class="hint">This UI talks to <code>/v1/responses</code> on the same server and keeps state by conversation name.</p>
    </aside>

    <main class="panel chat">
      <div class="chat-header">
        <div>
          <strong>Browser Chat</strong>
          <p class="hint">Hermes can use tools through the existing backend. Tool calls appear inline.</p>
        </div>
        <div class="footer-note" id="usage">No messages yet.</div>
      </div>

      <section id="messages" class="messages"></section>

      <form id="composer" class="composer">
        <textarea id="prompt" placeholder="Ask Hermes anything… Shift+Enter for a newline."></textarea>
        <div class="composer-actions">
          <div class="footer-note" id="composer-note">Ready.</div>
          <div class="right">
            <button id="clear-chat" class="secondary" type="button">Clear messages</button>
            <button id="send" type="submit">Send</button>
          </div>
        </div>
      </form>
    </main>
  </div>

  <script>
    const CONFIG = __CONFIG_JSON__;
    const storageKey = 'hermes-browser-ui';
    const state = {
      busy: false,
      conversation: null,
      usage: null,
      messages: [],
    };

    const $ = (id) => document.getElementById(id);
    const els = {
      apiKeyWrap: $('api-key-wrap'),
      apiKey: $('api-key'),
      composer: $('composer'),
      composerNote: $('composer-note'),
      conversation: $('conversation'),
      healthChip: $('health-chip'),
      instructions: $('instructions'),
      messages: $('messages'),
      model: $('model'),
      newChat: $('new-chat'),
      prompt: $('prompt'),
      saveSettings: $('save-settings'),
      send: $('send'),
      statusLine: $('status-line'),
      usage: $('usage'),
      clearChat: $('clear-chat'),
    };

    function randomConversationId() {
      if (window.crypto && crypto.randomUUID) {
        return `chat-${crypto.randomUUID().slice(0, 8)}`;
      }
      return `chat-${Math.random().toString(36).slice(2, 10)}`;
    }

    function loadSettings() {
      try {
        const saved = JSON.parse(localStorage.getItem(storageKey) || '{}');
        els.conversation.value = saved.conversation || randomConversationId();
        els.instructions.value = saved.instructions || '';
        els.model.value = saved.model || CONFIG.defaultModel;
        els.apiKey.value = saved.apiKey || '';
      } catch (err) {
        els.conversation.value = randomConversationId();
        els.model.value = CONFIG.defaultModel;
      }
    }

    function saveSettings() {
      const payload = {
        conversation: els.conversation.value.trim(),
        instructions: els.instructions.value,
        model: els.model.value.trim(),
        apiKey: els.apiKey.value,
      };
      localStorage.setItem(storageKey, JSON.stringify(payload));
      setComposerNote('Settings saved locally in your browser.');
    }

    function setComposerNote(text, isError = false) {
      els.composerNote.textContent = text;
      els.composerNote.style.color = isError ? 'var(--danger)' : 'var(--muted)';
    }

    function setBusy(busy) {
      state.busy = busy;
      els.send.disabled = busy;
      els.prompt.disabled = busy;
      els.conversation.disabled = busy;
      els.model.disabled = busy;
      els.instructions.disabled = busy;
      els.apiKey.disabled = busy;
      els.newChat.disabled = busy;
      els.saveSettings.disabled = busy;
      els.clearChat.disabled = busy;
      setComposerNote(busy ? 'Hermes is thinking…' : 'Ready.');
    }

    function addMessage(role, content, meta = '') {
      state.messages.push({ role, content, meta });
      renderMessages();
    }

    function renderMessages() {
      els.messages.innerHTML = '';
      if (!state.messages.length) {
        const empty = document.createElement('div');
        empty.className = 'message system';
        empty.textContent = 'Start a conversation to bring Hermes into the browser.';
        els.messages.appendChild(empty);
      }
      for (const msg of state.messages) {
        const node = document.createElement('article');
        node.className = `message ${msg.role}`;
        const meta = document.createElement('div');
        meta.className = 'meta';
        meta.textContent = msg.meta || msg.role;
        const body = document.createElement('div');
        body.textContent = msg.content;
        node.append(meta, body);
        els.messages.appendChild(node);
      }
      els.messages.scrollTop = els.messages.scrollHeight;
    }

    function updateUsage(usage) {
      state.usage = usage || null;
      if (!usage) {
        els.usage.textContent = 'No messages yet.';
        return;
      }
      const total = usage.total_tokens ?? 0;
      const input = usage.input_tokens ?? 0;
      const output = usage.output_tokens ?? 0;
      els.usage.textContent = `Tokens: total ${total} · in ${input} · out ${output}`;
    }

    function extractAssistantText(output) {
      if (!Array.isArray(output)) return '';
      const messageItem = [...output].reverse().find((item) => item.type === 'message');
      if (!messageItem || !Array.isArray(messageItem.content)) return '';
      return messageItem.content
        .filter((part) => part && part.type === 'output_text')
        .map((part) => part.text || '')
        .join('\n\n')
        .trim();
    }

    function summarizeToolItems(output) {
      if (!Array.isArray(output)) return [];
      const items = [];
      for (const item of output) {
        if (item.type === 'function_call') {
          items.push(`Tool call: ${item.name || 'unknown'}\n${item.arguments || ''}`.trim());
        } else if (item.type === 'function_call_output') {
          items.push(`Tool result (${item.call_id || 'call'}):\n${String(item.output || '')}`.trim());
        }
      }
      return items;
    }

    async function checkHealth() {
      try {
        const response = await fetch('/health');
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const payload = await response.json();
        els.healthChip.textContent = 'Connected';
        els.healthChip.classList.remove('offline');
        els.statusLine.textContent = `Server status: ${payload.status} · platform: ${payload.platform}`;
      } catch (error) {
        els.healthChip.textContent = 'Offline';
        els.healthChip.classList.add('offline');
        els.statusLine.textContent = `Health check failed: ${error.message}`;
      }
    }

    function resetConversation(clearVisuals = true) {
      const nextId = randomConversationId();
      els.conversation.value = nextId;
      state.conversation = nextId;
      if (clearVisuals) {
        state.messages = [];
        renderMessages();
        updateUsage(null);
      }
      saveSettings();
    }

    async function sendMessage(promptText) {
      const conversation = els.conversation.value.trim() || randomConversationId();
      const instructions = els.instructions.value.trim();
      const model = els.model.value.trim() || CONFIG.defaultModel;
      const apiKey = els.apiKey.value.trim();
      els.conversation.value = conversation;
      state.conversation = conversation;

      addMessage('user', promptText, `conversation ${conversation}`);
      setBusy(true);

      const headers = { 'Content-Type': 'application/json' };
      if (apiKey) headers.Authorization = `Bearer ${apiKey}`;

      try {
        const response = await fetch(`${CONFIG.apiBaseUrl}/responses`, {
          method: 'POST',
          headers,
          body: JSON.stringify({
            model,
            input: promptText,
            instructions: instructions || undefined,
            conversation,
            store: true,
          }),
        });

        const data = await response.json().catch(() => ({ error: { message: 'Invalid JSON response' } }));
        if (!response.ok) {
          throw new Error(data?.error?.message || `Request failed with HTTP ${response.status}`);
        }

        for (const toolText of summarizeToolItems(data.output)) {
          addMessage('tool', toolText, 'tool activity');
        }
        addMessage('assistant', extractAssistantText(data.output) || '(No message returned)', data.id || 'assistant');
        updateUsage(data.usage);
        saveSettings();
      } catch (error) {
        addMessage('system', error.message || String(error), 'request failed');
        setComposerNote(error.message || String(error), true);
      } finally {
        setBusy(false);
      }
    }

    els.composer.addEventListener('submit', async (event) => {
      event.preventDefault();
      const promptText = els.prompt.value.trim();
      if (!promptText || state.busy) return;
      els.prompt.value = '';
      await sendMessage(promptText);
    });

    els.prompt.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        els.composer.requestSubmit();
      }
    });

    els.newChat.addEventListener('click', () => resetConversation(true));
    els.clearChat.addEventListener('click', () => {
      state.messages = [];
      renderMessages();
      updateUsage(null);
      setComposerNote('Messages cleared in this browser tab.');
    });
    els.saveSettings.addEventListener('click', saveSettings);

    if (CONFIG.requiresApiKey) {
      els.apiKeyWrap.classList.remove('hidden');
    }

    loadSettings();
    state.conversation = els.conversation.value.trim();
    renderMessages();
    checkHealth();
  </script>
</body>
</html>
'''
    return template.replace('__CONFIG_JSON__', config_json)
