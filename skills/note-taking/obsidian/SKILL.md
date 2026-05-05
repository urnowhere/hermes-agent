---
name: obsidian
description: Read, search, and create notes in the Obsidian vault.
---

# Obsidian Vault

**Location:** Set via `OBSIDIAN_VAULT_PATH` environment variable (e.g. in `~/.hermes/.env`).

If unset, defaults to `~/Documents/Obsidian Vault`.

**Excluding directories:** Set `OBSIDIAN_EXCLUDE_DIRS` to a colon-separated list of directory names to skip during listing and search (e.g. `archive:templates`). Useful when the vault contains symlinked or large directories that should not be enumerated.

Note: Vault paths may contain spaces - always quote them.

## Read a note

```bash
VAULT="${OBSIDIAN_VAULT_PATH:-$HOME/Documents/Obsidian Vault}"
cat "$VAULT/Note Name.md"
```

## List notes

```bash
VAULT="${OBSIDIAN_VAULT_PATH:-$HOME/Documents/Obsidian Vault}"

# Build find exclude args from OBSIDIAN_EXCLUDE_DIRS (colon-separated directory names)
FIND_EXCLUDES=()
if [ -n "$OBSIDIAN_EXCLUDE_DIRS" ]; then
  IFS=: read -ra _EXCL <<< "$OBSIDIAN_EXCLUDE_DIRS"
  for _d in "${_EXCL[@]}"; do FIND_EXCLUDES+=( -not -path "$VAULT/$_d/*" ); done
fi

# All notes
find "$VAULT" -name "*.md" -type f "${FIND_EXCLUDES[@]}"

# In a specific folder
ls "$VAULT/Subfolder/"
```

## Search

```bash
VAULT="${OBSIDIAN_VAULT_PATH:-$HOME/Documents/Obsidian Vault}"

# Build exclude args from OBSIDIAN_EXCLUDE_DIRS (colon-separated directory names)
FIND_EXCLUDES=()
GREP_EXCLUDES=()
if [ -n "$OBSIDIAN_EXCLUDE_DIRS" ]; then
  IFS=: read -ra _EXCL <<< "$OBSIDIAN_EXCLUDE_DIRS"
  for _d in "${_EXCL[@]}"; do
    FIND_EXCLUDES+=( -not -path "$VAULT/$_d/*" )
    GREP_EXCLUDES+=( --exclude-dir="$_d" )
  done
fi

# By filename
find "$VAULT" -name "*.md" -iname "*keyword*" "${FIND_EXCLUDES[@]}"

# By content
grep -rli "keyword" "$VAULT" --include="*.md" "${GREP_EXCLUDES[@]}"
```

## Create a note

```bash
VAULT="${OBSIDIAN_VAULT_PATH:-$HOME/Documents/Obsidian Vault}"
cat > "$VAULT/New Note.md" << 'ENDNOTE'
# Title

Content here.
ENDNOTE
```

## Append to a note

```bash
VAULT="${OBSIDIAN_VAULT_PATH:-$HOME/Documents/Obsidian Vault}"
echo "
New content here." >> "$VAULT/Existing Note.md"
```

## Wikilinks

Obsidian links notes with `[[Note Name]]` syntax. When creating notes, use these to link related content.
