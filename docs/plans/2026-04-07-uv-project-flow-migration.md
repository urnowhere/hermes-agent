# UV Project-Flow Migration Plan

Date: 2026-04-07

## Goal

Migrate Hermes-owned project install and update flows away from `uv pip` and onto native `uv` project commands.

For this plan, "Hermes-owned project flows" means:

- source installs of the Hermes repo itself
- `hermes update`
- first-party installer scripts
- first-party docs that describe how to install, update, and extend the Hermes project environment

The target end state is:

- Hermes project installs use `uv sync`, not `uv pip install -e ".[...]"`.
- Hermes docs present the repo as a normal `uv` project with a locked `.venv`.
- `hermes update` uses `uv sync` semantics and remains resilient when optional extras fail on a given machine.
- Fresh installs standardize on `.venv`.
- Existing source installs using `venv` continue to work and can still be updated in place.

## Scope

### In Scope

- `hermes update` implementation in `hermes_cli/main.py`
- fresh-install scripts:
  - `scripts/install.sh`
  - `scripts/install.ps1`
  - `setup-hermes.sh`
- service/unit defaults that currently assume `venv`
- repo-owned install/update docs:
  - `README.md`
  - `CONTRIBUTING.md`
  - `website/docs/getting-started/installation.md`
  - `website/docs/getting-started/updating.md`
  - `website/docs/guides/use-mcp-with-hermes.md`
  - `website/docs/user-guide/features/mcp.md`
  - `website/docs/reference/faq.md`
  - any other first-party page that describes Hermes project environment setup
- first-party CI/test workflow definitions that codify Hermes source-install behavior, especially `.github/workflows/tests.yml`
- tests covering update/install behavior and env path selection

### Explicitly Out Of Scope

These should not be converted in this change:

- targeted interpreter installs that intentionally use `uv pip --python ...`
- arbitrary package installation outside the Hermes project
- third-party tool installation examples that should become `uv tool install` in a separate pass
- optional local package installs outside Hermes's locked dependency graph, especially `./tinker-atropos`
- Nix-specific bootstrapping under `nix/`
- skills/reference docs that intentionally discuss generic package installation patterns

Out-of-scope files likely include:

- `hermes_cli/memory_setup.py`
- `hermes_cli/tools_config.py` for the `tinker-atropos` install path
- `nix/devShell.nix`
- skill reference files under `skills/` and `optional-skills/`

## Why This Scope Boundary Exists

Native `uv` project commands are the right fit for Hermes because Hermes is already a normal `pyproject.toml` project with a checked-in `uv.lock`.

However, `uv sync` is a project-environment operation. It is not a drop-in replacement for:

- "install packages into this arbitrary interpreter"
- "install this unrelated local editable package into the current env"
- "incrementally add ad hoc packages outside the lockfile"

Those use cases still map more naturally to `uv pip` today unless the project structure changes.

This plan therefore optimizes for the high-value migration that is feasible now, without forcing packaging changes to unrelated workflows.

## Relevant UV Behavior

The implementation must be designed around these current `uv` semantics:

1. `uv sync` is a project operation.
   It uses `pyproject.toml` plus `uv.lock`.

2. `uv sync` is exact by default.
   It removes packages not selected by the current dependency set unless `--inexact` is used.

3. `uv sync` creates the project environment automatically if needed.
   The default project environment path is `.venv`.

4. `UV_PROJECT_ENVIRONMENT` is an official way to target a non-default project environment path.
   This is important for preserving existing `venv` installs during update.

5. `--all-extras` means every declared optional dependency.
   It is not the same as the repo's curated `[all]` extra.

6. The repo's `[all]` extra is intentionally narrower than "all extras".
   `matrix` is deliberately excluded from `[all]` in `pyproject.toml` because it breaks installs on modern macOS.

7. `uv run` auto-syncs the project environment and is appropriate for repo-local contributor commands.

## Locked Decisions

These decisions should be treated as part of the plan, not revisited mid-implementation:

1. The standard Hermes project install command becomes:
   `uv sync --locked --extra all`

2. The standard Hermes base-only install command becomes:
   `uv sync --locked`

3. Contributor installs become:
   `uv sync --locked --extra all --extra dev`

4. Hermes update should use:
   `uv sync --locked --inexact --extra all`
   not exact sync

5. Fresh Hermes-managed environments should use `.venv`.
   Contributor and CI flows should use the same default.

6. Existing `venv` source installs remain supported.
   Update logic must preserve them instead of silently switching the environment out from under the user.

7. `tinker-atropos` remains an explicit carve-out for this pass.

## Current Repo Inventory

### Runtime Code Paths

- `hermes_cli/main.py`
  - `cmd_update()`
  - `_install_python_dependencies_with_optional_fallback()`
  - ZIP update path also refreshes Python deps

- `hermes_cli/gateway.py`
  - detects env dirs
  - systemd and launchd units still fall back to `venv`

### Install Scripts

- `scripts/install.sh`
- `scripts/install.ps1`
- `setup-hermes.sh`

These still use combinations of:

- `uv venv venv`
- `VIRTUAL_ENV=.../venv`
- `uv pip install -e ".[all]"`
- hardcoded `venv/bin/hermes` or `venv\Scripts\hermes.exe`

### Docs

Primary docs to migrate:

- `README.md`
- `CONTRIBUTING.md`
- `website/docs/getting-started/installation.md`
- `website/docs/getting-started/updating.md`
- `website/docs/guides/use-mcp-with-hermes.md`
- `website/docs/user-guide/features/mcp.md`
- `website/docs/reference/faq.md`

Also sweep for any other repo-owned page that still claims Hermes should be installed with `uv pip install -e ".[...]"`.

### Tests

Primary tests that will need updates:

- `tests/hermes_cli/test_update_autostash.py`
- `tests/hermes_cli/test_gateway_service.py`
- `tests/hermes_cli/test_update_gateway_restart.py`

Potentially affected:

- any tests that assert specific command strings for update/install flows
- any tests that assume `venv` is the default env directory when no env is detected

### CI

- `.github/workflows/tests.yml`

This workflow is part of the migration surface because it still codifies first-party install and test commands for the repo.

### Existing Guardrail

The repo already contains an important regression test:

- `tests/test_project_metadata.py`

That test encodes the invariant that `[all]` excludes `matrix`.

This migration must preserve that invariant in runtime behavior and docs.

## Desired End State

After this migration:

- source-install docs describe Hermes as a `uv sync` project
- `hermes update` no longer shells out to `uv pip install -e ".[all]"`
- update/install logic uses the curated `[all]` extra, not `--all-extras`
- first-party examples no longer tell users to export `VIRTUAL_ENV="$(pwd)/venv"` for standard Hermes project setup
- first-party examples prefer `.venv/bin/hermes` for fresh installs
- service generation still works for both `.venv` and legacy `venv`
- the codebase has a clear documented exception for out-of-scope `uv pip` use cases

## Migration Strategy

Implement this in phases. Do not try to change every mention of `uv pip` in one sweep.

### Phase 1: Centralize Project-Environment Selection

Add a helper in `hermes_cli/main.py` for selecting the project environment path used by Hermes-owned flows.

Suggested behavior:

1. If `PROJECT_ROOT / ".venv"` exists, use `.venv`.
2. Else if `PROJECT_ROOT / "venv"` exists, use `venv`.
3. Else default to `.venv`.

Suggested helper names:

- `_preferred_project_env_dir()`
- `_project_env_dir_for_sync()`

Requirements:

- It must not guess based on user shell activation.
- It must be deterministic from repo state.
- It must be reusable by both normal update and ZIP update paths.

### Phase 2: Replace Update-Time `uv pip` With `uv sync`

Replace `_install_python_dependencies_with_optional_fallback()` in `hermes_cli/main.py` with a `uv sync`-based implementation.

Do not preserve the old function shape if it makes the new behavior awkward. It is fine to replace it with a better-factored helper pair.

Recommended helper breakdown:

1. `_preferred_project_env_dir() -> Path`
2. `_run_uv_sync(extras: list[str], *, inexact: bool, locked: bool, env_dir: Path) -> None`
3. `_sync_python_dependencies_with_optional_fallback(...) -> None`

Recommended implementation model:

1. Select env dir using the helper above.
2. Set `UV_PROJECT_ENVIRONMENT` to that path.
3. First try:
   `uv sync --locked --inexact --extra all --quiet`
4. If that fails:
   - print a user-facing warning
   - sync base only:
     `uv sync --locked --inexact --quiet`
   - then retry curated extras from `[all]` cumulatively

The cumulative retry is important.

Do not do this:

- base sync
- `--extra modal`
- then `--extra messaging`
- then `--extra mcp`

unless each retry includes the full successful extra set so far.

Recommended algorithm:

1. `selected = []`
2. for each extra from `_load_installable_optional_extras()`:
   - candidate = `selected + [extra]`
   - try `uv sync --locked --inexact` with every extra in `candidate`
   - if success, assign `selected = candidate`
   - else record that extra as failed

Why cumulative retry matters:

- it keeps the environment definition explicit
- it avoids depending on "extraneous package retention" as implicit state
- it makes the final environment equal to base + the successful extras list

This fallback may require one full `uv sync` attempt per candidate extra after the initial full-install failure.
That is acceptable here because correctness matters more than minimizing retries on a degraded path.
Do not "optimize" this into additive one-extra installs that stop describing the whole intended environment.

The helper that extracts installable extras from `[all]` can likely be kept, but it should be reviewed for naming and comments now that it drives `uv sync` instead of pip installs.

### Phase 3: Remove The `pip` Fallback From `hermes update`

Current `cmd_update()` falls back to `python -m pip` if `uv` is unavailable.

For the new Hermes-owned model, this fallback should be removed.

Target behavior:

- if `uv` is missing, `hermes update` fails with a clear message
- the message should explain that Hermes source installs are now managed via `uv`
- the message should tell the user how to reinstall or restore `uv`

Do not silently drop back to `pip`, because:

- it defeats the migration
- it bypasses the lockfile
- it encourages path drift between documented and actual behavior

### Phase 4: Apply The Same Logic To ZIP Update

The Windows ZIP update path in `hermes_cli/main.py` has its own dependency refresh block.

That block must call the same uv-sync helper used by the main update path.

Requirements:

- no duplicate dependency-sync logic
- no separate `pip` fallback hidden in the ZIP path
- same env selection rules as the normal update path

### Phase 5: Migrate Fresh Install Scripts

Update:

- `scripts/install.sh`
- `scripts/install.ps1`
- `setup-hermes.sh`

#### Linux/macOS install script requirements

For `scripts/install.sh`:

1. Change fresh venv creation from `venv` to `.venv`.
2. Replace `uv pip install -e ".[all]"` with `uv sync --locked --extra all`.
3. Replace base fallback install from `uv pip install -e "."` with `uv sync --locked`.
4. Update any recovery messages to mention `uv sync`, not `uv pip install`.
5. Update symlink target to `.venv/bin/hermes`.
6. Keep `tinker-atropos` messaging explicit and out-of-scope.

Important note:

The current shell installer has a "full install failed, try base install" behavior. That is still useful. Preserve the behavior, but map it to `uv sync`:

- first try `uv sync --locked --extra all`
- if that fails, warn and try `uv sync --locked`

Do not use `--all-extras`.

#### Windows install script requirements

For `scripts/install.ps1`:

1. Change fresh env path from `venv` to `.venv`.
2. Replace `uv pip install -e ".[all]"` with `uv sync --locked --extra all`.
3. Replace base fallback install with `uv sync --locked`.
4. Update PATH and `hermes.exe` target references to `.venv\Scripts`.
5. Leave `tinker-atropos` as an explicit exception.

#### Legacy helper script requirements

For `setup-hermes.sh`:

1. Align the script with `.venv` and `uv sync`.
2. Replace `--all-extras` with `--extra all`.
3. Keep the lockfile-first behavior.
4. Preserve the separate optional `tinker-atropos` step.

This file is especially important because it already partially mixes `uv sync` and old assumptions. It must not keep the bad `--all-extras` pattern.

### Phase 6: Align Service/Unit Fallbacks With `.venv`

`hermes_cli/gateway.py` already detects `.venv` before `venv`, which is good.

However, some fallback strings still default to `PROJECT_ROOT / "venv"` when no env is detected.

Update those fallbacks to default to `.venv` instead.

Specific areas:

- systemd unit generation
- launchd plist generation
- any helper paths derived from "assume a project env exists"

Requirements:

- keep support for both detected `.venv` and detected `venv`
- only change the hardcoded default fallback

### Phase 7: Rewrite Docs Around The Native UV Model

This is not just a find-and-replace. The docs need a consistent mental model.

#### Core messaging

Use these statements consistently:

- Hermes is a `uv` project with a checked-in `uv.lock`.
- Standard installs use `uv sync`.
- The project environment lives at `.venv` by default.
- You do not need to manually activate the environment to use the installed `hermes` entry point.

#### Command replacements

Use these command forms:

- full project install:
  `uv sync --locked --extra all`
- base install:
  `uv sync --locked`
- contributor install:
  `uv sync --locked --extra all --extra dev`
- repo-local command execution where appropriate:
  `uv run python -m pytest tests/ -q`

#### Important doc warning

Where the docs talk about extras, explicitly explain:

- `--extra all` means the repo's curated `[all]` extra
- `--all-extras` is different and should not be used for Hermes install docs

#### Update docs

`website/docs/getting-started/updating.md` needs special care.

The page should say that `hermes update` syncs the project environment using `uv` and the lockfile.

Manual update examples should become:

```bash
cd /path/to/hermes-agent
git pull origin main
git submodule update --init --recursive
uv sync --locked --extra all
uv pip install -e "./tinker-atropos"  # optional carve-out, if needed
```

If the page explains fallback behavior, keep it accurate:

- Hermes prefers the curated full install
- if some optional extras fail, the update flow preserves base functionality and retries supported extras

#### MCP docs

Pages like `use-mcp-with-hermes.md`, `features/mcp.md`, and `reference/faq.md` currently imply that adding one extra is an incremental editable install.

Update them to reflect the `uv sync` model:

- standard `uv sync --locked --extra all` already includes MCP because the curated `[all]` extra includes `mcp`
- if you originally installed only base Hermes and now want MCP support, re-sync with the extras you want enabled
- example:
  `uv sync --locked --extra mcp`

Be careful with wording:

- `uv sync --locked --extra mcp` is correct for a user who wants only base + MCP
- it is not a universal additive command if the user actually wants other extras too
- existing installs should be re-synced with the full set of extras the user intends to keep, not treated as an incremental one-off add

Recommended phrasing:

"If you manage Hermes from the source checkout, re-run `uv sync` with the full set of extras you want installed. For example, for base + MCP only: `uv sync --locked --extra mcp`."

#### Contributor docs

In `README.md` and `CONTRIBUTING.md`, prefer `uv run` for commands that operate within the project environment.

Good pattern:

```bash
uv sync --locked --extra all --extra dev
uv run python -m pytest tests/ -q
```

Avoid requiring activation unless there is a good reason.

#### CI workflow expectations

First-party CI should model the same contributor path:

- install with:
  `uv sync --locked --extra all --extra dev`
- run tests with:
  `uv run python -m pytest ...`

Do not leave CI on a separate activation-centric `venv` flow after the docs and contributor guidance move to `uv sync` and `uv run`.

### Phase 8: Update Runtime Hints And Error Messages

After the main flows are migrated, update first-party hints that still reference the old Hermes project install path.

Examples:

- recovery text in installer scripts
- warnings in docs
- any first-party guidance that says "run `uv pip install -e '.[all]'`"
- any first-party hint that tells users to `uv pip` Hermes-owned project dependencies individually instead of re-syncing or repairing the Hermes install as a project

Do not change out-of-scope runtime hints that refer to:

- `tinker-atropos`
- arbitrary interpreter installs
- generic package installation

### Phase 9: Add Regression Tests

Tests are required. This migration changes runtime behavior, installer messaging, and path defaults.

#### Update command tests

In `tests/hermes_cli/test_update_autostash.py`:

1. Replace expectations for `uv pip install -e ".[all]"` with `uv sync --locked --inexact --extra all`.
2. Replace expectations for base fallback install with `uv sync --locked --inexact`.
3. Add assertions for cumulative extra retries.
4. Add a test that the update path uses the curated extras list from `[all]`.
5. Add a test that `cmd_update()` fails clearly when `uv` is missing.

#### Env-path tests

Add or update tests for the env selection helper:

1. `.venv` preferred when present
2. `venv` used when `.venv` absent but `venv` present
3. `.venv` chosen as default when neither exists

#### Gateway/service tests

Update:

- `tests/hermes_cli/test_gateway_service.py`
- `tests/hermes_cli/test_update_gateway_restart.py`

to reflect:

- `.venv` as the default fallback
- continued acceptance of detected legacy `venv`

#### Runtime-hint tests

Update or add coverage for first-party remediation messaging changes.

Primary file:

- `tests/hermes_cli/test_doctor.py`

Requirements:

- verify Hermes-owned dependency repair hints no longer tell users to `uv pip install` individual project dependencies
- preserve explicit `tinker-atropos` carve-outs where they are intentionally retained

#### Metadata guardrail

Keep `tests/test_project_metadata.py`.

Consider adding one more regression test that asserts no Hermes-owned install helper or doc string uses `--all-extras` for the main project install path.

#### CI workflow coverage

Update `.github/workflows/tests.yml` so first-party automation matches the same install and execution model described in this plan:

- `uv sync --locked --extra all --extra dev`
- `uv run python -m pytest ...`

## Detailed File-by-File Execution Checklist

### `hermes_cli/main.py`

- add env selection helper
- add uv-sync helper(s)
- remove pip-style project install helper
- migrate normal update path to uv sync
- migrate ZIP update path to the same helper
- remove non-uv fallback
- update user-facing messages accordingly

### `hermes_cli/gateway.py`

- keep detection order: `.venv`, then `venv`
- change hardcoded fallback defaults from `venv` to `.venv`

### `hermes_cli/doctor.py`

- update Hermes-owned dependency repair hints so they point users toward repairing or re-syncing the Hermes project install
- do not rewrite intentional `tinker-atropos` carve-out guidance as part of this pass

### `scripts/install.sh`

- create `.venv`
- use `uv sync --locked --extra all`
- use `uv sync --locked` fallback
- update recovery text
- update symlink path

### `scripts/install.ps1`

- create `.venv`
- use `uv sync --locked --extra all`
- use `uv sync --locked` fallback
- update path references to `.venv\Scripts`

### `setup-hermes.sh`

- change `venv` to `.venv`
- remove `--all-extras`
- use `--extra all`
- keep optional `tinker-atropos`

### `README.md`

- contributor install commands
- RL note should remain as explicit carve-out if retained

### `CONTRIBUTING.md`

- contributor install commands
- remove `export VIRTUAL_ENV="$(pwd)/venv"` from standard path
- update symlink path if still shown

### `.github/workflows/tests.yml`

- replace legacy project install steps with `uv sync --locked --extra all --extra dev`
- replace activation-centric test execution with `uv run python -m pytest ...`
- keep CI aligned with the documented contributor workflow

### `website/docs/getting-started/installation.md`

- keep the new `uv sync` model
- replace any incorrect `--all-extras`
- explicitly distinguish `--extra all` from `--all-extras`
- keep `tinker-atropos` exception accurate

### `website/docs/getting-started/updating.md`

- rewrite update description around `uv sync`
- replace manual update commands
- ensure the page does not imply exact sync removes nothing

### `website/docs/guides/use-mcp-with-hermes.md`

- replace project-install examples with `uv sync`

### `website/docs/user-guide/features/mcp.md`

- replace source-install examples with `uv sync`
- update hardcoded env path example from `venv/bin/hermes` to `.venv/bin/hermes` where it describes a fresh standard source install

### `website/docs/reference/faq.md`

- replace MCP recovery instructions that still use project `uv pip install -e ".[mcp]"`

## Testing And Validation

### Automated Tests

Run at minimum:

```bash
uv sync --locked --extra all --extra dev
uv run python -m pytest tests/hermes_cli/test_update_autostash.py -q
uv run python -m pytest tests/hermes_cli/test_gateway_service.py -q
uv run python -m pytest tests/hermes_cli/test_update_gateway_restart.py -q
uv run python -m pytest tests/hermes_cli/test_doctor.py -q
uv run python -m pytest tests/test_project_metadata.py -q
```

Then run the full suite:

```bash
uv run python -m pytest tests/ -q
```

Use the same `uv sync` and `uv run` model locally and in CI. The important part is that the tests actually run against the intended project environment.

### Manual Smoke Tests

Perform these manually before closing the work:

1. Fresh source install on a clean checkout
   - no `.venv` present
   - run the Linux/macOS install flow
   - verify `.venv/bin/hermes` exists

2. Legacy source install update
   - create a repo with `venv` only
   - run `hermes update`
   - verify it continues using `venv`

3. `.venv` source install update
   - repo with `.venv`
   - run `hermes update`
   - verify `.venv` stays authoritative

4. Optional-extra failure path
   - simulate one extra failure
   - verify base install remains intact
   - verify successful extras stay selected cumulatively

5. Gateway unit generation
   - no active env, no detected env: fallback should use `.venv`
   - detected legacy `venv`: unit should still use `venv`

### Documentation Validation

After edits:

1. grep repo-owned docs for:
   - `uv pip install -e ".[`
   - `--all-extras`
   - `/venv/bin/hermes`

2. classify every remaining result as:
   - intentional exception
   - out-of-scope generic package instruction
   - bug to fix before merge

## Risks And Mitigations

### Risk: Exact sync removes unrelated packages

Mitigation:

- use `--inexact` in `hermes update`
- keep standard docs explicit about `uv sync` exactness

### Risk: Wrong extra set selected

Mitigation:

- use `--extra all`, not `--all-extras`
- keep and extend the metadata regression test

### Risk: Existing `venv` installs break

Mitigation:

- add a deterministic env-path helper
- preserve `venv` when it already exists
- only standardize fresh installs on `.venv`

### Risk: Hidden duplicate logic between normal update and ZIP update

Mitigation:

- make both paths call the same sync helper

### Risk: Docs become internally inconsistent

Mitigation:

- treat docs as one migration unit
- do a final grep-based sweep before merge

### Risk: Implementer accidentally removes the `tinker-atropos` path

Mitigation:

- keep it explicitly called out as out-of-scope
- add comments where necessary that it is intentionally retained

## Suggested Commit Breakdown

If the work is large, split it into logically reviewable commits:

1. runtime update helper refactor plus update-path tests
2. installer script migration plus any script-facing assertions and messages that change with that behavior
3. gateway/service fallback adjustments plus gateway/service tests
4. docs and CI migration plus final regression coverage and cleanup

Do not split runtime and test changes apart in a way that leaves the tree red.

## Follow-Ups Intentionally Deferred

These can be handled later, but not in this migration:

1. Convert `tinker-atropos` into a workspace or path dependency so it can participate in native `uv` project sync.
2. Migrate generic tool-install docs from `uv pip install modal` style to `uv tool install modal` where appropriate.
3. Revisit `nix/devShell.nix` and other Nix-managed flows separately.
4. Review whether contributor docs should move from optional-dependency `dev` to dependency groups in the future.
5. Audit retained `uv pip` references in skills and reference materials.

## Implementation Summary

An implementer executing this plan should:

1. keep the migration scoped to Hermes project flows
2. migrate update/install behavior to `uv sync`
3. preserve legacy `venv` installs
4. standardize fresh installs on `.venv`
5. use the curated `[all]` extra via `--extra all`
6. retain explicit exceptions for out-of-scope `uv pip` use cases
7. update tests and docs as first-class parts of the change

## Reference Sources

- UV project sync docs: https://docs.astral.sh/uv/concepts/projects/sync/
- UV project dependency docs: https://docs.astral.sh/uv/concepts/projects/dependencies/
- UV project config docs: https://docs.astral.sh/uv/concepts/projects/config/
- UV CLI reference: https://docs.astral.sh/uv/reference/cli/
- UV environment variables reference: https://docs.astral.sh/uv/reference/environment/
