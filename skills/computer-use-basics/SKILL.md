---
name: computer-use-basics
description: Use the optional computer_use tool to operate a persistent desktop VM through screenshots, mouse, keyboard, scrolling, and noVNC takeover. Use when tasks require full desktop GUI control rather than terminal or DOM-only browser automation.
---

# Computer Use Basics

Use `computer_use` when a task requires full desktop control: graphical applications, visual browser workflows, file managers, system dialogs, or any interface that cannot be handled cleanly through terminal or standard browser tools.

## Core Loop

1. Start with `computer_use(action="screenshot", question="What is visible and what should I do next?")`.
2. Read the visual analysis and choose one small action.
3. Use `click`, `double_click`, `type`, `key`, `scroll`, `move`, or `drag`.
4. Take another screenshot to verify the result.
5. Repeat until complete or until the UI is blocked.

## Actions

- `screenshot`: capture and analyze the desktop.
- `click`: click at screen coordinates.
- `double_click`: double-click at screen coordinates.
- `type`: type text into the focused field.
- `key`: press a key or combo such as `Return`, `Escape`, `ctrl+l`, or `alt+F4`.
- `scroll`: scroll up or down.
- `move`: move the mouse without clicking.
- `drag`: drag from one coordinate to another.
- `screen_size`: get the current desktop resolution.
- `cursor_position`: get the current cursor position.
- `vnc_url`: get the noVNC URL for live user takeover.

## Good Practice

- Prefer terminal tools for CLI tasks.
- Prefer standard browser tools for DOM-accessible web tasks.
- Use `computer_use` for visual or OS-level tasks.
- Keep actions small and verify after each important step.
- When an exact click matters, request `screenshot` with `annotate=true` first. Use the grid to choose coordinates instead of guessing from an unmarked screenshot.
- For web pages with ads, cards, or dense feeds, avoid clicking large content bodies unless opening that item is the goal. Prefer clicking explicit buttons, links, inputs, or navigation controls.
- If a click opens the wrong page or tab, recover with `key` such as `alt+Left`, `ctrl+w`, or `Escape`, then take a screenshot before continuing.
- If a modal or popup appears, dismiss it deliberately and verify.
- If the same action fails twice, stop and report the state instead of looping.

## Human Takeover

If login, MFA, CAPTCHA, payment, account recovery, or sensitive personal information is required:

1. Get the VNC URL with `computer_use(action="vnc_url")`.
2. Ask the user to take over in noVNC.
3. Wait for the user to confirm they are done.
4. Resume with a fresh screenshot.

## Safety

The tool can operate a real desktop. Do not perform irreversible or public actions without explicit user approval. Do not assume coordinate guesses are correct; verify visually.
