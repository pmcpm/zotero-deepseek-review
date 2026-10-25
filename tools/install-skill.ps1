param(
    [string]$SkillsRoot,
    [switch]$Update
)
$ErrorActionPreference = 'Stop'
$skillName = 'zotero-deepseek-review'
$source = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "../skills/$skillName")).Path
if (-not $SkillsRoot) {
    $codexRoot = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
    $SkillsRoot = Join-Path $codexRoot 'skills'
}
$skillsPath = [IO.Path]::GetFullPath($SkillsRoot)
$destination = [IO.Path]::GetFullPath((Join-Path $skillsPath $skillName))
if ($destination -eq $source) { throw 'Source and destination are identical.' }
if (-not $destination.StartsWith($skillsPath.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Destination must stay within the requested skills directory.'
}
$backup = $null
if (Test-Path -LiteralPath $destination) {
    if (-not $Update) { throw 'Skill already exists. Use -Update to back it up and update.' }
    if ((Get-Item -LiteralPath $destination).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw 'Refusing to update a linked skill directory; choose its actual skills root.'
    }
    $backupRoot = Join-Path (Split-Path -Parent $skillsPath) 'skill-backups'
    $backup = Join-Path $backupRoot ($skillName + '-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0,8))
    New-Item -ItemType Directory -Path $backup -Force | Out-Null
    Copy-Item -LiteralPath $destination -Destination (Join-Path $backup $skillName) -Recurse
}
New-Item -ItemType Directory -Path $destination -Force | Out-Null
# Copy maintained source only. Never copy caches, task records or binary artifacts.
$files = Get-ChildItem -LiteralPath $source -File -Recurse | Where-Object {
    $_.FullName -notmatch '[\\/]__pycache__[\\/]' -and $_.Extension -notin @('.pyc','.pyo','.xpi')
}
foreach ($file in $files) {
    $relative = $file.FullName.Substring($source.Length).TrimStart('\', '/')
    $target = Join-Path $destination $relative
    New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
    Copy-Item -LiteralPath $file.FullName -Destination $target -Force
}
[pscustomobject]@{ skill = $skillName; installed = $destination; backup = $backup; files = @($files).Count; defaultProvider = 'cstcloud' } | ConvertTo-Json
