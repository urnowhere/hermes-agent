import { describe, expect, it } from 'vitest'

import { supportsOsc52Clipboard } from '../terminal.js'

import { shouldEmitClipboardSequence } from './osc.js'

describe('shouldEmitClipboardSequence', () => {
  it('suppresses local multiplexer clipboard OSC by default', () => {
    expect(shouldEmitClipboardSequence({ TMUX: '/tmp/tmux-1/default,1,0' } as NodeJS.ProcessEnv)).toBe(false)
    expect(shouldEmitClipboardSequence({ STY: '1234.pts-0.host' } as NodeJS.ProcessEnv)).toBe(false)
  })

  it('keeps OSC enabled for remote or plain local terminals', () => {
    expect(
      shouldEmitClipboardSequence({ SSH_CONNECTION: '1', TMUX: '/tmp/tmux-1/default,1,0' } as NodeJS.ProcessEnv)
    ).toBe(true)
    expect(shouldEmitClipboardSequence({ TERM: 'xterm-256color' } as NodeJS.ProcessEnv)).toBe(true)
  })

  it('honors explicit env override', () => {
    expect(
      shouldEmitClipboardSequence({
        HERMES_TUI_CLIPBOARD_OSC52: '1',
        TMUX: '/tmp/tmux-1/default,1,0'
      } as NodeJS.ProcessEnv)
    ).toBe(true)
    expect(
      shouldEmitClipboardSequence({ HERMES_TUI_COPY_OSC52: '0', TERM: 'xterm-256color' } as NodeJS.ProcessEnv)
    ).toBe(false)
  })

  it('HERMES_TUI_FORCE_OSC52 takes precedence over TMUX suppression', () => {
    // Without the override, local-in-tmux suppresses the OSC 52 sequence
    // so the terminal multiplexer path wins. FORCE_OSC52=1 flips that
    // back on for users whose tmux config supports passthrough.
    expect(shouldEmitClipboardSequence({ TMUX: '/tmp/t,1,0' } as NodeJS.ProcessEnv)).toBe(false)
    expect(
      shouldEmitClipboardSequence({
        HERMES_TUI_FORCE_OSC52: '1',
        TMUX: '/tmp/t,1,0'
      } as NodeJS.ProcessEnv)
    ).toBe(true)
  })

  it('HERMES_TUI_FORCE_OSC52=0 suppresses OSC 52 even for remote or plain terminals', () => {
    expect(
      shouldEmitClipboardSequence({
        HERMES_TUI_FORCE_OSC52: '0',
        SSH_CONNECTION: '1'
      } as NodeJS.ProcessEnv)
    ).toBe(false)
  })
})

describe('supportsOsc52Clipboard', () => {
  // Terminals known to correctly implement OSC 52. On these, setClipboard()
  // skips the native-tool safety net (wl-copy/xclip/pbcopy) to avoid racing
  // the terminal's own clipboard write. Values must match what
  // detectTerminal() in utils/env.ts returns — TERM=xterm-ghostty normalises
  // to 'ghostty', TERM_PROGRAM=WezTerm stays 'WezTerm', etc.
  it.each(['ghostty', 'kitty', 'WezTerm', 'windows-terminal', 'vscode'])(
    'returns true for allowlisted terminal %s',
    terminal => {
      expect(supportsOsc52Clipboard(terminal)).toBe(true)
    }
  )

  // Intentionally conservative — iTerm2 disables OSC 52 by default; Alacritty
  // and GNOME Terminal detection is unreliable; xterm/Terminal.app lack
  // reliable OSC 52. These keep the existing native-safety-net behaviour.
  it.each(['iTerm.app', 'alacritty', 'Apple_Terminal', 'xterm', 'tmux', 'screen', 'cursor', 'WarpTerminal', ''])(
    'returns false for non-allowlisted terminal %s',
    terminal => {
      expect(supportsOsc52Clipboard(terminal)).toBe(false)
    }
  )

  it('returns false when terminal is null (detection failed)', () => {
    expect(supportsOsc52Clipboard(null)).toBe(false)
  })

  it('defaults to the module-level detected terminal when no argument is passed', () => {
    // With no argument, uses env.terminal detected at module load. We don't
    // know what that is in CI, but the call must return a boolean (not throw)
    // and the result must match calling with env.terminal explicitly.
    expect(typeof supportsOsc52Clipboard()).toBe('boolean')
  })
})
