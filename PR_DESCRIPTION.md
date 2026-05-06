## Problem
PR #20322 added staleness detection for the @hermes/ink bundle, but used the
wrong output filename (`ink-bundle.js`). The esbuild config actually outputs
`entry-exports.js`, making the stale-check always trigger a rebuild.

Additionally, when a user navigates away from the /chat page in the dashboard
and comes back, the WebSocket PTY connection spawns a new TUI process without
a session resume ID, causing the embedded chat to show "forging session…" and
refuse keyboard input.

## Changes
1. Replace all `ink-bundle.js` references with `entry-exports.js` in
   `_tui_ink_bundle_exists` and `_hermes_ink_bundle_stale`.

2. Add `"packages"` to the os.walk skip set in `_tui_build_needed` so it
   doesn't redundantly walk into `packages/hermes-ink/src/` — staleness
   for that sub-package is already handled by `_hermes_ink_bundle_stale`.

3. In `_resolve_chat_argv` (web server PTY handler), auto-resume the most
   recent TUI session when no explicit resume ID is provided. Uses the
   existing `_resolve_last_session(source="tui")` helper.

4. Update tests: rename helpers to use `entry-exports.js`, add coverage for
   the bundle-present case.

## How to test
- Start `hermes dashboard --tui`
- Open /chat, have a conversation
- Navigate to /sessions (or any other tab)
- Navigate back to /chat
- Keyboard input should work immediately, resuming the existing session
