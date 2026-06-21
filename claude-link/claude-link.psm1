# claude-link.psm1
# Share Claude Code's knowledge layer (memory, plans, settings, transcripts) across
# multiple local accounts/config-roots via directory junctions. Identity stays per-account.
#
#   Import-Module .\claude-link.psm1 -Force -DisableNameChecking
#   claude-link doctor
#   claude-link init            # dry-run plan (read-only)
#   claude-link init -Execute   # apply
#
# Phase 1: local junctions. Phase 2 (claude-link sync): bolt git/cloud on the same store.

#region constants -------------------------------------------------------------
$script:UserProfile  = $env:USERPROFILE
$script:DefaultRoot  = Join-Path $script:UserProfile '.claude'
$script:DefaultStore = Join-Path $script:UserProfile '.claude-shared'

# Directories shared by junction (memory + transcripts live inside 'projects').
$script:LinkedDirs   = @('projects','plans','commands','agents')

# settings.json keys that are portable across accounts/machines (merge-on-apply, not linked).
$script:SharedKeys   = @('effortLevel','autoUpdatesChannel','skipWorkflowUsageWarning',
                         'theme','model','enableWorkflows')

# Must NEVER be linked or synced — identity + machine-local state.
$script:Denylist     = @('.credentials.json','.claude.json','cache','shell-snapshots',
                         'daemon','daemon.log','sessions','ide','backups',
                         'policy-limits.json','remote-settings.json',
                         'mcp-needs-auth-cache.json','.last-cleanup','history.jsonl')
#endregion

#region helpers ---------------------------------------------------------------
function Write-CLLog {
    param(
        [string]$Message,
        [ValidateSet('info','ok','warn','err','plan')][string]$Level = 'info',
        [switch]$Dry
    )
    $tag    = switch ($Level) { 'ok' {'[ ok ]'} 'warn' {'[warn]'} 'err' {'[fail]'} 'plan' {'[plan]'} default {'[info]'} }
    $color  = switch ($Level) { 'ok' {'Green'}  'warn' {'Yellow'} 'err' {'Red'}    'plan' {'Cyan'}   default {'Gray'} }
    $prefix = if ($Dry) { '(dry-run) ' } else { '' }
    Write-Host "$tag $prefix$Message" -ForegroundColor $color
}

# Write UTF-8 WITHOUT BOM — Node's JSON.parse (settings.json) chokes on a BOM.
function Write-CLText {
    param([string]$Path,[string]$Text)
    $enc = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Text, $enc)
}

function Get-CLLink {
    param([string]$Path)
    $info = [pscustomobject]@{ Path = $Path; Exists = $false; IsJunction = $false; Target = $null }
    if (Test-Path -LiteralPath $Path) {
        $info.Exists = $true
        $item = Get-Item -LiteralPath $Path -Force
        if ($item.LinkType -eq 'Junction') {
            $info.IsJunction = $true
            $t = $item.Target
            if ($t -is [System.Array]) { $t = $t[0] }
            $info.Target = $t
        }
    }
    return $info
}

function New-CLJunction {
    param([string]$Link,[string]$Target)
    New-Item -ItemType Junction -Path $Link -Value $Target -ErrorAction Stop | Out-Null
}

# Remove ONLY the reparse point. Never recurses into the target (verified on PS 5.1).
function Remove-CLJunction {
    param([string]$Link)
    [System.IO.Directory]::Delete($Link, $false)
}

function Get-CLDirStats {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return [pscustomobject]@{ Files = 0; Bytes = [int64]0 } }
    $files = @(Get-ChildItem -LiteralPath $Path -Recurse -File -Force -ErrorAction SilentlyContinue)
    $sum = ($files | Measure-Object -Property Length -Sum).Sum
    if ($null -eq $sum) { $sum = 0 }
    [pscustomobject]@{ Files = $files.Count; Bytes = [int64]$sum }
}

function Format-CLBytes {
    param([int64]$Bytes)
    if ($Bytes -ge 1GB) { return ('{0:N2} GB' -f ($Bytes / 1GB)) }
    if ($Bytes -ge 1MB) { return ('{0:N1} MB' -f ($Bytes / 1MB)) }
    if ($Bytes -ge 1KB) { return ('{0:N0} KB' -f ($Bytes / 1KB)) }
    return "$Bytes B"
}

function Read-CLFrontmatter {
    param([string]$Path)
    $res = @{ name = $null; description = $null }
    $raw = Get-Content -LiteralPath $Path -Raw -ErrorAction SilentlyContinue
    if ($null -eq $raw) { return [pscustomobject]$res }
    $m = [regex]::Match($raw, '(?s)^﻿?---\s*\r?\n(.*?)\r?\n---')
    if ($m.Success) {
        foreach ($line in ($m.Groups[1].Value -split "\r?\n")) {
            $kv = [regex]::Match($line, '^\s*(name|description)\s*:\s*(.+?)\s*$')
            if ($kv.Success) { $res[$kv.Groups[1].Value] = $kv.Groups[2].Value.Trim('"') }
        }
    }
    return [pscustomobject]$res
}

function Get-CLManifestPath { param([string]$Store) Join-Path $Store 'manifest.json' }

function Read-CLManifest {
    param([string]$Store)
    $p = Get-CLManifestPath $Store
    if (Test-Path -LiteralPath $p) { return (Get-Content -LiteralPath $p -Raw | ConvertFrom-Json) }
    return $null
}

function Write-CLManifest {
    param([string]$Store,[object]$Manifest)
    Write-CLText -Path (Get-CLManifestPath $Store) -Text ($Manifest | ConvertTo-Json -Depth 6)
}

function New-CLManifest {
    param([string]$Store,[string[]]$Roots)
    [pscustomobject]@{
        schema     = 1
        createdAt  = (Get-Date -Format 'o')
        store      = $Store
        linkedDirs = $script:LinkedDirs
        sharedKeys = $script:SharedKeys
        denylist   = $script:Denylist
        roots      = $Roots
    }
}
#endregion

#region settings --------------------------------------------------------------
function Invoke-CLSeedSettings {
    param([string]$Root,[string]$Store,[switch]$DryRun)
    $src = Join-Path $Root  'settings.json'
    $dst = Join-Path $Store 'settings.shared.json'
    if (-not (Test-Path -LiteralPath $src)) { Write-CLLog "no settings.json at root; skipping settings seed" 'warn'; return }
    $s = Get-Content -LiteralPath $src -Raw | ConvertFrom-Json
    $shared = [ordered]@{}
    foreach ($k in $script:SharedKeys) {
        if ($s.PSObject.Properties.Name -contains $k) { $shared[$k] = $s.$k }
    }
    if ($DryRun) { Write-CLLog "would seed settings.shared.json: $($shared.Keys -join ', ')" 'plan' -Dry; return }
    Write-CLText -Path $dst -Text ([pscustomobject]$shared | ConvertTo-Json -Depth 6)
    Write-CLLog "settings.shared.json seeded ($($shared.Keys -join ', '))" 'ok'
}

function Invoke-CLApply {
    param([string]$Store = $script:DefaultStore,[string[]]$Root,[switch]$DryRun)
    $man = Read-CLManifest $Store
    if ($null -eq $man) { Write-CLLog "no manifest in $Store — run 'claude-link init' first" 'err'; return }
    $sharedFile = Join-Path $Store 'settings.shared.json'
    if (-not (Test-Path -LiteralPath $sharedFile)) { Write-CLLog "no settings.shared.json — run init" 'err'; return }
    $shared = Get-Content -LiteralPath $sharedFile -Raw | ConvertFrom-Json
    $roots  = if ($Root) { $Root } else { @($man.roots) }
    foreach ($r in $roots) {
        $target = Join-Path $r 'settings.json'
        $cur = [ordered]@{}
        if (Test-Path -LiteralPath $target) {
            $obj = Get-Content -LiteralPath $target -Raw | ConvertFrom-Json
            foreach ($p in $obj.PSObject.Properties) { $cur[$p.Name] = $p.Value }
        }
        foreach ($p in $shared.PSObject.Properties) { $cur[$p.Name] = $p.Value }  # shared keys win
        if ($DryRun) { Write-CLLog "would write $target (keys: $($cur.Keys -join ', '))" 'plan' -Dry; continue }
        if (-not (Test-Path -LiteralPath $r)) { New-Item -ItemType Directory -Path $r -Force | Out-Null }
        Write-CLText -Path $target -Text ([pscustomobject]$cur | ConvertTo-Json -Depth 6)
        Write-CLLog "applied settings -> $target" 'ok'
    }
}
#endregion

#region init / add ------------------------------------------------------------
function Invoke-CLInit {
    [CmdletBinding()]
    param(
        [string]$Root  = $script:DefaultRoot,
        [string]$Store = $script:DefaultStore,
        [switch]$Execute
    )
    $dry = -not $Execute
    Write-Host "=== claude-link init ===" -ForegroundColor Cyan
    Write-CLLog "root=$Root  store=$Store" 'info' -Dry:$dry
    if (-not (Test-Path -LiteralPath $Root)) { Write-CLLog "root not found: $Root" 'err'; return }
    Write-CLLog "TIP: close any running Claude Code sessions before -Execute (avoids file locks)." 'info'

    if (-not (Test-Path -LiteralPath $Store)) {
        if ($dry) { Write-CLLog "would create store dir: $Store" 'plan' -Dry }
        else { New-Item -ItemType Directory -Path $Store -Force | Out-Null; Write-CLLog "created store: $Store" 'ok' }
    } else { Write-CLLog "store exists: $Store" 'info' }

    foreach ($d in $script:LinkedDirs) {
        $rootDir  = Join-Path $Root  $d
        $storeDir = Join-Path $Store $d
        $rl = Get-CLLink $rootDir
        $sExists = Test-Path -LiteralPath $storeDir

        if ($rl.IsJunction) {
            if ($rl.Target -eq $storeDir) { Write-CLLog "$d : already linked -> store" 'ok' }
            else { Write-CLLog "$d : junction to a DIFFERENT target ($($rl.Target)); leaving as-is" 'warn' }
            continue
        }
        if (-not $rl.Exists -and -not $sExists) {
            if ($dry) { Write-CLLog "$d : would create empty store dir + junction" 'plan' -Dry }
            else { New-Item -ItemType Directory -Path $storeDir -Force | Out-Null; New-CLJunction $rootDir $storeDir; Write-CLLog "$d : created empty + linked" 'ok' }
            continue
        }
        if (-not $rl.Exists -and $sExists) {
            if ($dry) { Write-CLLog "$d : would junction -> existing store data" 'plan' -Dry }
            else { New-CLJunction $rootDir $storeDir; Write-CLLog "$d : linked to existing store data" 'ok' }
            continue
        }
        # rootDir is a REAL directory
        $stats = Get-CLDirStats $rootDir
        if ($sExists) {
            Write-CLLog "$d : real data in BOTH root and store ($($stats.Files) files / $(Format-CLBytes $stats.Bytes) in root) — skipping to avoid clobber. Resolve manually or use 'claude-link add -Merge'." 'warn'
            continue
        }
        if ($dry) {
            Write-CLLog "$d : would MIGRATE $($stats.Files) files / $(Format-CLBytes $stats.Bytes) root->store, verify, then swap root\$d to a junction (original kept as $d.pre-link-*; a dir locked by a live session is deferred)" 'plan' -Dry
            continue
        }
        # MIGRATE: rename-first (cheap lock check) -> copy -> verify -> junction. Never copies a locked dir.
        $stamp      = Get-Date -Format 'yyyyMMdd-HHmmss'
        $backupLeaf = "$d.pre-link-$stamp"
        $backupPath = Join-Path $Root $backupLeaf
        try { Rename-Item -LiteralPath $rootDir -NewName $backupLeaf -ErrorAction Stop }
        catch {
            Write-CLLog "$d : LOCKED / in use - deferred. Close ALL Claude Code sessions, then re-run 'claude-link init -Execute'." 'warn'
            continue
        }
        Write-CLLog "$d : migrating $($stats.Files) files / $(Format-CLBytes $stats.Bytes) ..." 'info'
        try {
            Copy-Item -LiteralPath $backupPath -Destination $storeDir -Recurse -Force -ErrorAction Stop
            $sStats = Get-CLDirStats $storeDir
            $bStats = Get-CLDirStats $backupPath
            if ($sStats.Files -ne $bStats.Files -or $sStats.Bytes -ne $bStats.Bytes) {
                throw "verify mismatch (backup $($bStats.Files)/$($bStats.Bytes) vs store $($sStats.Files)/$($sStats.Bytes))"
            }
            New-CLJunction $rootDir $storeDir
            Write-CLLog "$d : migrated + linked (verified $($sStats.Files) files). Original kept as $backupLeaf" 'ok'
        } catch {
            Write-CLLog "$d : migration failed ($($_.Exception.Message)); rolling back." 'err'
            if (Test-Path -LiteralPath $storeDir) { try { [System.IO.Directory]::Delete($storeDir, $true) } catch {} }
            if (-not (Test-Path -LiteralPath $rootDir)) { Rename-Item -LiteralPath $backupPath -NewName $d }
            continue
        }
    }

    Invoke-CLSeedSettings -Root $Root -Store $Store -DryRun:$dry

    if ($dry) {
        Write-CLLog "would write manifest.json (roots: $Root)" 'plan' -Dry
    } else {
        $man = Read-CLManifest $Store
        if ($null -eq $man) { $man = New-CLManifest -Store $Store -Roots @($Root) }
        elseif ($man.roots -notcontains $Root) { $man.roots += $Root }
        Write-CLManifest -Store $Store -Manifest $man
        Write-CLLog "manifest written" 'ok'
    }

    Write-Host ""
    if ($dry) {
        Write-CLLog "DRY RUN complete — nothing changed. Apply with:  claude-link init -Execute" 'info'
    } else {
        Write-CLLog "init complete. Verify with:  claude-link doctor" 'ok'
    }
}

function Invoke-CLAdd {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Root,
        [string]$Store = $script:DefaultStore,
        [switch]$Execute,
        [switch]$Merge
    )
    $dry = -not $Execute
    Write-Host "=== claude-link add ===" -ForegroundColor Cyan
    $man = Read-CLManifest $Store
    if ($null -eq $man) { Write-CLLog "no store/manifest at $Store — run 'claude-link init' first" 'err'; return }

    if (-not (Test-Path -LiteralPath $Root)) {
        if ($dry) { Write-CLLog "would create new account root: $Root" 'plan' -Dry }
        else { New-Item -ItemType Directory -Path $Root -Force | Out-Null; Write-CLLog "created root: $Root" 'ok' }
    }
    foreach ($d in $script:LinkedDirs) {
        $rootDir  = Join-Path $Root  $d
        $storeDir = Join-Path $Store $d
        if (-not (Test-Path -LiteralPath $storeDir)) {
            if ($dry) { Write-CLLog "would create store\$d" 'plan' -Dry } else { New-Item -ItemType Directory -Path $storeDir -Force | Out-Null }
        }
        $rl = Get-CLLink $rootDir
        if ($rl.IsJunction) {
            if ($rl.Target -eq $storeDir) { Write-CLLog "$d : already linked" 'ok' } else { Write-CLLog "$d : junction to different target ($($rl.Target))" 'warn' }
            continue
        }
        if ($rl.Exists) {
            $stats = Get-CLDirStats $rootDir
            if (-not $Merge) {
                Write-CLLog "$d : real data ($($stats.Files) files) in $Root. Re-run with -Merge to fold it into the store, or move it aside. Skipping." 'warn'; continue
            }
            if ($dry) { Write-CLLog "$d : would MERGE $($stats.Files) files into store then link" 'plan' -Dry; continue }
            Copy-Item -Path (Join-Path $rootDir '*') -Destination $storeDir -Recurse -Force -ErrorAction SilentlyContinue
            $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
            Rename-Item -LiteralPath $rootDir -NewName "$d.pre-link-$stamp"
            New-CLJunction $rootDir $storeDir
            Write-CLLog "$d : merged + linked (original kept as $d.pre-link-$stamp)" 'ok'
            continue
        }
        if ($dry) { Write-CLLog "$d : would junction -> store" 'plan' -Dry }
        else { New-CLJunction $rootDir $storeDir; Write-CLLog "$d : linked" 'ok' }
    }

    if ($dry) { Write-CLLog "DRY RUN — apply with:  claude-link add -Root `"$Root`" -Execute" 'info'; return }
    if ($man.roots -notcontains $Root) { $man.roots += $Root; Write-CLManifest -Store $Store -Manifest $man }
    Invoke-CLApply -Store $Store -Root @($Root)
    Write-CLLog "added root $Root" 'ok'
}
#endregion

#region rebuild-index ---------------------------------------------------------
function Invoke-CLRebuildIndex {
    param([string]$Store = $script:DefaultStore,[switch]$DryRun)
    Write-Host "=== claude-link rebuild-index ===" -ForegroundColor Cyan
    $projects = Join-Path $Store 'projects'
    if (-not (Test-Path -LiteralPath $projects)) { Write-CLLog "no projects in store" 'warn'; return }
    $memDirs = @(Get-ChildItem -LiteralPath $projects -Directory -Recurse -Force -ErrorAction SilentlyContinue | Where-Object { $_.Name -eq 'memory' })
    if ($memDirs.Count -eq 0) { Write-CLLog "no memory dirs found" 'info'; return }
    foreach ($m in $memDirs) {
        $indexPath = Join-Path $m.FullName 'MEMORY.md'
        $titleMap = @{}
        if (Test-Path -LiteralPath $indexPath) {
            foreach ($line in (Get-Content -LiteralPath $indexPath)) {
                $mm = [regex]::Match($line, '^\s*-\s*\[(?<title>[^\]]+)\]\((?<file>[^)]+)\)')
                if ($mm.Success) { $titleMap[$mm.Groups['file'].Value] = $mm.Groups['title'].Value }
            }
        }
        $facts = @(Get-ChildItem -LiteralPath $m.FullName -File -Filter '*.md' | Where-Object { $_.Name -ne 'MEMORY.md' } | Sort-Object Name)
        $lines = New-Object System.Collections.Generic.List[string]
        $lines.Add('# Memory Index'); $lines.Add('')
        foreach ($f in $facts) {
            $fm = Read-CLFrontmatter $f.FullName
            $title = if ($titleMap.ContainsKey($f.Name)) { $titleMap[$f.Name] }
                     elseif ($fm.name) { (Get-Culture).TextInfo.ToTitleCase(($fm.name -replace '-', ' ')) }
                     else { [System.IO.Path]::GetFileNameWithoutExtension($f.Name) }
            $hook = if ($fm.description) { $fm.description } else { 'memory' }
            $lines.Add("- [$title]($($f.Name)) — $hook")
        }
        if ($DryRun) {
            Write-CLLog "would rewrite $indexPath ($($facts.Count) facts):" 'plan' -Dry
            $lines | ForEach-Object { Write-Host "      $_" -ForegroundColor DarkGray }
        } else {
            Write-CLText -Path $indexPath -Text (($lines -join "`r`n") + "`r`n")
            Write-CLLog "rebuilt index ($($facts.Count) facts): $indexPath" 'ok'
        }
    }
}
#endregion

#region doctor / unlink -------------------------------------------------------
function Invoke-CLDoctor {
    param([string]$Store = $script:DefaultStore,[string[]]$Root,[switch]$Unlink,[switch]$Execute)
    Write-Host "=== claude-link doctor ===" -ForegroundColor Cyan
    $man = Read-CLManifest $Store
    if ($null -eq $man) { Write-CLLog "no manifest at $Store (store not initialized) — checking roots standalone" 'warn' }
    else { Write-CLLog "store: $Store  (schema $($man.schema), created $($man.createdAt))" 'info' }
    $roots = if ($Root) { $Root } elseif ($man) { @($man.roots) } else { @($script:DefaultRoot) }

    if (Test-Path -LiteralPath $Store) {
        $violations = 0
        foreach ($bad in $script:Denylist) {
            $p = Join-Path $Store $bad
            if (Test-Path -LiteralPath $p) { Write-CLLog "DENYLIST VIOLATION: '$bad' is inside the store ($p) — must NOT be shared/synced!" 'err'; $violations++ }
        }
        if ($violations -eq 0) { Write-CLLog "denylist audit clean (no identity/machine files in store)" 'ok' }
    }

    foreach ($r in $roots) {
        Write-Host "--- root: $r ---" -ForegroundColor White
        if (-not (Test-Path -LiteralPath $r)) { Write-CLLog "root missing" 'err'; continue }
        foreach ($d in $script:LinkedDirs) {
            $rootDir  = Join-Path $r $d
            $storeDir = Join-Path $Store $d
            $rl = Get-CLLink $rootDir
            if (-not $rl.Exists) { Write-CLLog "$d : (none)" 'info'; continue }
            if ($rl.IsJunction) {
                if ($rl.Target -eq $storeDir) {
                    if (Test-Path -LiteralPath $rl.Target) { Write-CLLog "$d : junction -> store  OK" 'ok' }
                    else { Write-CLLog "$d : junction -> MISSING target ($($rl.Target))" 'err' }
                } else { Write-CLLog "$d : junction -> $($rl.Target) (not this store)" 'warn' }
            } else { Write-CLLog "$d : real dir (not linked)" 'warn' }
        }
    }
    if ($Unlink) { Write-Host ""; Invoke-CLUnlink -Store $Store -Root $roots -Execute:$Execute }
}

function Invoke-CLUnlink {
    param([string]$Store = $script:DefaultStore,[string[]]$Root,[switch]$Execute)
    $dry = -not $Execute
    $roots = if ($Root) { $Root } else { @($script:DefaultRoot) }
    Write-CLLog "unlink: turn junctions back into standalone folders (copies data out of store)" 'info' -Dry:$dry
    foreach ($r in $roots) {
        foreach ($d in $script:LinkedDirs) {
            $rootDir  = Join-Path $r $d
            $storeDir = Join-Path $Store $d
            $rl = Get-CLLink $rootDir
            if (-not $rl.IsJunction) { continue }
            if ($rl.Target -ne $storeDir) { Write-CLLog "$d in $r links elsewhere ($($rl.Target)); skipping" 'warn'; continue }
            $stats = Get-CLDirStats $storeDir
            if ($dry) { Write-CLLog "$d : would remove junction and copy back $($stats.Files) files / $(Format-CLBytes $stats.Bytes) from store" 'plan' -Dry; continue }
            $tmp = "$rootDir.unlinking"
            Copy-Item -LiteralPath $storeDir -Destination $tmp -Recurse -Force -ErrorAction Stop
            Remove-CLJunction $rootDir
            Rename-Item -LiteralPath $tmp -NewName $d
            Write-CLLog "$d in $r : now a standalone folder (copied from store)" 'ok'
        }
    }
    if ($dry) { Write-CLLog "DRY RUN — apply with:  claude-link doctor -Unlink -Execute" 'info' }
}
#endregion

#region sync (Phase 2) --------------------------------------------------------
function Invoke-CLSync {
    param([string]$Store = $script:DefaultStore,[string]$Message)
    Write-Host "=== claude-link sync ===" -ForegroundColor Cyan
    if (-not (Test-Path -LiteralPath (Join-Path $Store '.git'))) {
        Write-CLLog "store is not a git repo yet. To enable multi-machine sync:" 'warn'
        Write-Host "    cd `"$Store`"; git init; git add -A; git commit -m 'init'; git branch -M main" -ForegroundColor White
        Write-Host "    git remote add origin <url>; git push -u origin main" -ForegroundColor White
        Write-Host "  On each other machine: clone it to the same path, then 'claude-link init -Store ...' to link local roots." -ForegroundColor White
        Write-Host "  (Or skip git: put the store in OneDrive/Syncthing — junctions are transparent to cloud folders.)" -ForegroundColor White
        Write-Host "  NOTE: keep repo working-dirs at the same absolute path across machines so project keys (encoded cwd) line up." -ForegroundColor DarkYellow
        return
    }
    Invoke-CLRebuildIndex -Store $Store   # regenerate indexes before commit to avoid MEMORY.md conflicts
    $msg = if ($Message) { $Message } else { "claude-link sync $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" }
    Push-Location $Store
    try {
        git pull --rebase --autostash
        git add -A
        git commit -m $msg
        git push
        Write-CLLog "synced" 'ok'
    } finally { Pop-Location }
}
#endregion

#region public entry points ---------------------------------------------------
function Show-CLHelp {
@"
claude-link — share Claude Code's knowledge layer across local accounts (identity stays separate)

  claude-link init [-Root <dir>] [-Store <dir>] [-Execute]
        Migrate this root's projects/plans/commands/agents into the shared store and
        replace them with junctions. Default is a DRY RUN; add -Execute to apply.

  claude-link add -Root <dir> [-Execute] [-Merge]
        Point another account's config dir (its own CLAUDE_CONFIG_DIR) at the shared store.
        -Merge folds that root's existing data into the store first.

  claude-link apply [-Root <dir>...]
        (Re)write each root's settings.json = local + settings.shared.json (shared keys win).

  claude-link rebuild-index [-DryRun]
        Regenerate every memory/MEMORY.md from its fact files (conflict-free index).

  claude-link doctor [-Root <dir>...] [-Unlink [-Execute]]
        Verify junctions + audit the store for identity/machine files that must never sync.
        -Unlink reverses everything back to standalone folders (DRY RUN unless -Execute).

  claude-link sync [-Message <msg>]
        Phase 2: rebuild indexes, then git pull/commit/push the store (if it's a repo).

  claude-as <profile> [claude args...]
        Set CLAUDE_CONFIG_DIR=~/.claude-<profile> (auto-linking it on first use) and launch claude.

Shared by junction : $($script:LinkedDirs -join ', ')   |   Settings merged: $($script:SharedKeys -join ', ')
Never linked/synced: $($script:Denylist -join ', ')
"@ | Write-Host
}

# Parse "-Name value -Switch" tokens into a hashtable so it can be splatted as NAMED params.
# (Array-splatting binds positionally and would never set a [switch] like -Execute.)
function ConvertTo-CLParams {
    param([object[]]$Tokens)
    $known = @('Execute','DryRun','Merge','Unlink')   # switches take no value
    $h = @{}
    $i = 0
    while ($i -lt $Tokens.Count) {
        $t = [string]$Tokens[$i]
        $m = [regex]::Match($t, '^-{1,2}(?<name>.+)$')
        if (-not $m.Success) { $i++; continue }        # ignore stray bare args
        $name = $m.Groups['name'].Value
        $next = if (($i + 1) -lt $Tokens.Count) { [string]$Tokens[$i + 1] } else { $null }
        if ($known -contains $name -or $null -eq $next) { $h[$name] = $true; $i++ }
        else { $h[$name] = $next; $i += 2 }
    }
    return $h
}

function claude-link {
    [CmdletBinding()]
    param(
        [Parameter(Position = 0)][string]$Command = 'help',
        [Parameter(ValueFromRemainingArguments = $true)][object[]]$Rest
    )
    if ($null -eq $Rest) { $Rest = @() }
    $params = ConvertTo-CLParams $Rest
    switch ($Command.ToLower()) {
        'init'          { Invoke-CLInit @params }
        'add'           { Invoke-CLAdd @params }
        'apply'         { Invoke-CLApply @params }
        'rebuild-index' { Invoke-CLRebuildIndex @params }
        'doctor'        { Invoke-CLDoctor @params }
        'unlink'        { Invoke-CLUnlink @params }
        'sync'          { Invoke-CLSync @params }
        'help'          { Show-CLHelp }
        '-h'            { Show-CLHelp }
        '--help'        { Show-CLHelp }
        default         { Write-CLLog "unknown command '$Command'" 'err'; Show-CLHelp }
    }
}

function claude-as {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory, Position = 0)][string]$Profile,
        [Parameter(ValueFromRemainingArguments = $true)][string[]]$ClaudeArgs
    )
    $root = Join-Path $env:USERPROFILE ".claude-$Profile"
    if (-not (Test-Path -LiteralPath (Join-Path $root 'projects'))) {
        Write-CLLog "profile '$Profile' not onboarded — linking $root to shared store" 'info'
        Invoke-CLAdd -Root $root -Execute
    }
    $prev = $env:CLAUDE_CONFIG_DIR
    $env:CLAUDE_CONFIG_DIR = $root
    Write-CLLog "CLAUDE_CONFIG_DIR set -> $root" 'ok'
    $claude = Get-Command claude -ErrorAction SilentlyContinue
    if ($null -eq $claude) {
        Write-CLLog "'claude' not found on PATH. Env is set for THIS shell — launch Claude Code now to use this profile." 'warn'
        return
    }
    try { & $claude.Source @ClaudeArgs } finally { $env:CLAUDE_CONFIG_DIR = $prev }
}

Export-ModuleMember -Function 'claude-link','claude-as'
#endregion
