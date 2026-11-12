<#
.SYNOPSIS
    Create Desktop and Start Menu shortcuts for My AI Assistant.

.DESCRIPTION
    Points both shortcuts at the packaged executable (dist\MyAIAssistant.exe by
    default) and uses the executable's own embedded icon. A legacy broken
    "My AI Assistant.url" on the Desktop (which only contained "URL=file:///")
    is renamed to "My AI Assistant.url.bak" instead of being deleted.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\create_shortcuts.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\create_shortcuts.ps1 -ExePath "C:\Apps\MyAIAssistant\MyAIAssistant.exe"
#>
[CmdletBinding()]
param(
    [string]$ExePath,
    [switch]$SkipDesktop,
    [switch]$SkipStartMenu
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $ExePath) {
    $ExePath = Join-Path $repoRoot 'dist\MyAIAssistant.exe'
}
$ExePath = [System.IO.Path]::GetFullPath($ExePath)

if (-not (Test-Path -LiteralPath $ExePath -PathType Leaf)) {
    throw "Executable not found: $ExePath  (run build.cmd first, or pass -ExePath)"
}

$exeDir = Split-Path -Parent $ExePath
$exeName = [System.IO.Path]::GetFileNameWithoutExtension($ExePath)
$shell = New-Object -ComObject WScript.Shell

function New-AppShortcut {
    param([string]$LinkPath)

    $parent = Split-Path -Parent $LinkPath
    if (-not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent | Out-Null
    }
    $shortcut = $shell.CreateShortcut($LinkPath)
    $shortcut.TargetPath = $ExePath
    $shortcut.WorkingDirectory = $exeDir
    $shortcut.IconLocation = "$ExePath,0"
    $shortcut.Description = 'My AI Assistant'
    $shortcut.WindowStyle = 1
    $shortcut.Save()
    Write-Host "Created: $LinkPath"
}

if (-not $SkipDesktop) {
    $desktop = [Environment]::GetFolderPath('Desktop')
    $legacy = Join-Path $desktop 'My AI Assistant.url'
    if (Test-Path -LiteralPath $legacy) {
        $backup = "$legacy.bak"
        if (Test-Path -LiteralPath $backup) { Remove-Item -LiteralPath $backup -Force }
        Move-Item -LiteralPath $legacy -Destination $backup
        Write-Host "Backed up legacy shortcut: $backup"
    }
    New-AppShortcut -LinkPath (Join-Path $desktop 'My AI Assistant.lnk')
}

if (-not $SkipStartMenu) {
    $programs = [Environment]::GetFolderPath('Programs')
    New-AppShortcut -LinkPath (Join-Path $programs 'My AI Assistant.lnk')
}

Write-Host "Target: $ExePath"
