# claude-link

Share Claude Code's **knowledge layer** — memory, plans, settings, transcripts — across
multiple local accounts (or machines), while each account keeps its **own identity and
credentials**. Windows-first (PowerShell + directory junctions, no admin required).

## The model

Claude Code stores everything under one root (`~/.claude` + the sibling `~/.claude.json`).
Only two things are account-specific; everything else is global / keyed by project path:

| Layer | What | claude-link does |
|---|---|---|
| **Identity** | `.claude.json` → `oauthAccount`, `userID`; `.credentials.json` | **never touches it** — stays per-account |
| **Knowledge** | `projects/` (transcripts + `memory/`), `plans/`, `commands/`, `agents/`, portable `settings.json` keys | **shares it** via one store every account points at |

Mechanism: a single store at `~/.claude-shared/`. Directory layers are **junctions** back to
the store (transparent to the app — hardcoded paths like
`…/projects/<cwd>/memory/MEMORY.md` keep resolving). `settings.json` is a file, so instead of
linking it the tool **merges** shared keys into each account's copy.

**Never shared / synced:** `.credentials.json`, `.claude.json`, `cache/`, `shell-snapshots/`,
`daemon*`, `sessions/`, `ide/`, `backups/`, `policy-limits.json`, `remote-settings.json`,
`mcp-needs-auth-cache.json`, `.last-cleanup`, `history.jsonl`. `doctor` audits the store and
fails loudly if any of these leak in.

## Install

```powershell
Import-Module .\claude-link.psm1 -Force -DisableNameChecking
```
> Keep the file's **UTF-8 BOM** — Windows PowerShell 5.1 reads BOM-less scripts as the ANSI
> codepage and will mangle non-ASCII characters. (PowerShell 7 doesn't care.)

To load it every session, add that line to your `$PROFILE`.

## Commands

```powershell
claude-link doctor          # read-only: show link state + audit the store. Always safe.
claude-link init            # DRY RUN: print the migration plan (nothing changes)
claude-link init -Execute   # migrate this root into the store + junction it back
```

Every mutating verb is **dry-run by default**; add `-Execute` to apply.

| Command | Purpose |
|---|---|
| `claude-link init [-Execute]` | Migrate `~/.claude`'s projects/plans/commands/agents into the store, replace with junctions. Copy → **verify (file count + bytes)** → swap; the original is kept as `projects.pre-link-<timestamp>`. |
| `claude-link add -Root <dir> [-Execute] [-Merge]` | Point another account's config dir at the store. `-Merge` folds that root's existing data in first. |
| `claude-link apply [-Root <dir>…]` | (Re)write each root's `settings.json` = local + shared keys (shared wins). |
| `claude-link rebuild-index [-DryRun]` | Regenerate every `memory/MEMORY.md` from its fact files — avoids index merge conflicts. |
| `claude-link doctor [-Unlink [-Execute]]` | Verify junctions + denylist audit. `-Unlink` reverses everything back to standalone folders. |
| `claude-link sync [-Message <m>]` | Phase 2: rebuild indexes, then `git pull/commit/push` the store. |
| `claude-as <profile> [args…]` | Set `CLAUDE_CONFIG_DIR=~/.claude-<profile>` (auto-linking on first use) and launch `claude`. |

## Running several accounts on one PC

After `claude-link init -Execute` once, spin up each additional account as its own config root —
each logs in independently, all share the one brain:

```powershell
claude-as work       # ~/.claude-work     (own credentials, shared memory/plans/settings)
claude-as personal   # ~/.claude-personal (own credentials, same shared store)
```

First use of a profile auto-runs `claude-link add` to junction it to the store, then launches
Claude Code with that profile's `CLAUDE_CONFIG_DIR`. No logout/login churn.

## Multiple machines (Phase 2)

The store is a self-contained, relative-path directory — make it a git repo or drop it in a
cloud-synced folder; junctions are transparent to both.

```powershell
cd ~/.claude-shared; git init; git add -A; git commit -m init
git remote add origin <url>; git push -u origin main
claude-link sync                      # rebuild indexes, pull --rebase, commit, push
```

Why this stays low-conflict: transcripts are UUID-named append-only files; memory facts are
one-file-each; `MEMORY.md` is **regenerated, never hand-merged**. **Caveat:** projects are keyed
by *encoded working-dir path*, so keep each repo at the **same absolute path** on every machine
or their memory/transcripts won't line up.

## Safety / reversibility

- `init`, `add`, `doctor -Unlink` are **dry-run unless `-Execute`**.
- Migration is **copy → verify → swap**; on a size/count mismatch it aborts that folder and
  leaves the original untouched. Originals are retained as `*.pre-link-<timestamp>` (delete once happy).
- `doctor -Unlink -Execute` copies data out of the store and restores plain folders.
- Close running Claude Code sessions before `-Execute` to avoid file locks.

## Status

Phase 1 (local junctions) implemented and validated by dry-run. Phase 2 (`sync`) is a working
git wrapper; multi-machine path-aliasing is not yet implemented (documented caveat above).
