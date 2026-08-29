[CmdletBinding()]
param(
    [string]$ShortcutName = $null,
    [string]$Endpoint = $null,
    [string]$RepoRoot = $null,
    [string]$HostName = "127.0.0.1",
    [int]$Port = 8876,
    [ValidateSet("active", "history", "evidence", "all")]
    [string]$Mode = "all",
    [int]$Limit = 120,
    [double]$PollSeconds = 2.0,
    [string]$IconLocation = "$env:SystemRoot\System32\imageres.dll,109",
    [switch]$UpdateLegacyShortcut,
    [switch]$SkipLegacyShortcutCleanup,
    [switch]$DryRun,
    [switch]$PassThru
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptRoot = $PSScriptRoot
if (-not $scriptRoot) {
    $scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
}
if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = (Resolve-Path (Join-Path $scriptRoot "..\..")).Path
}

function Resolve-PowerShellHost {
    $pwsh = Get-Command "pwsh" -ErrorAction SilentlyContinue
    if ($pwsh) {
        return $pwsh.Source
    }
    $powershell = Get-Command "powershell" -ErrorAction SilentlyContinue
    if ($powershell) {
        return $powershell.Source
    }
    throw "PowerShell was not found on PATH."
}

function Quote-ShortcutArgument {
    param([string]$Value)
    if ($Value -match '[\s"]') {
        return '"' + ($Value -replace '"', '\"') + '"'
    }
    return $Value
}

function Get-ProjectName {
    param([string]$Repository)

    return (Split-Path -Leaf (Resolve-Path $Repository).Path)
}

function Test-AutomaticEndpointValue {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $true
    }
    $normalized = $Value.Trim().ToLowerInvariant()
    return @("auto", "@auto", "default", "@default", "project", "@project", "project-workbench", "@project.workbench") -contains $normalized
}

function Save-WorkbenchShortcut {
    param(
        [string]$Path,
        [string]$TargetPath,
        [string]$ShortcutArguments,
        [string]$WorkingDirectory,
        [string]$ShortcutIconLocation
    )

    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($Path)
    $shortcut.TargetPath = $TargetPath
    $shortcut.Arguments = $ShortcutArguments
    $shortcut.WorkingDirectory = $WorkingDirectory
    $shortcut.IconLocation = $ShortcutIconLocation
    $shortcut.Description = "Open the live DB-backed shujuan roadmap workbench."
    $shortcut.Save()
}

$repo = (Resolve-Path $RepoRoot).Path
$projectName = Get-ProjectName -Repository $repo
if ([string]::IsNullOrWhiteSpace($ShortcutName)) {
    $ShortcutName = "$projectName Roadmap Workbench"
}
$launcher = (Resolve-Path (Join-Path $scriptRoot "open-shujuan-workbench.ps1")).Path
$desktop = [Environment]::GetFolderPath("Desktop")
$linkPath = Join-Path $desktop "$ShortcutName.lnk"
$legacyLinkPath = Join-Path $desktop "shujuan Live Workbench.lnk"
$target = Resolve-PowerShellHost
$argumentList = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", $launcher,
    "-RepoRoot", $repo,
    "-HostName", $HostName,
    "-Port", [string]$Port,
    "-Mode", $Mode,
    "-Limit", [string]$Limit,
    "-PollSeconds", [string]$PollSeconds
)
if (-not (Test-AutomaticEndpointValue -Value $Endpoint)) {
    $argumentList += @("-Endpoint", $Endpoint)
}
$arguments = ($argumentList | ForEach-Object { Quote-ShortcutArgument $_ }) -join " "
$shouldUpdateLegacy = (-not $SkipLegacyShortcutCleanup) -and ($ShortcutName -ne "shujuan Live Workbench") -and ($UpdateLegacyShortcut -or (Test-Path -LiteralPath $legacyLinkPath))

if (-not $DryRun) {
    Save-WorkbenchShortcut -Path $linkPath -TargetPath $target -ShortcutArguments $arguments -WorkingDirectory $repo -ShortcutIconLocation $IconLocation
    if ($shouldUpdateLegacy) {
        Save-WorkbenchShortcut -Path $legacyLinkPath -TargetPath $target -ShortcutArguments $arguments -WorkingDirectory $repo -ShortcutIconLocation $IconLocation
    }
}

$result = [pscustomobject]@{
    ok = $true
    dry_run = [bool]$DryRun
    shortcut_path = $linkPath
    target_path = $target
    arguments = $arguments
    working_directory = $repo
    icon_location = $IconLocation
    launcher = $launcher
    endpoint = $Endpoint
    endpoint_argument = if (Test-AutomaticEndpointValue -Value $Endpoint) { $null } else { $Endpoint }
    project_name = $projectName
    legacy_shortcut_path = $legacyLinkPath
    legacy_shortcut_updated = [bool]((-not $DryRun) -and $shouldUpdateLegacy)
    legacy_shortcut_would_update = [bool]$shouldUpdateLegacy
    port = $Port
    mode = $Mode
}

if ($PassThru -or $DryRun) {
    Write-Output ($result | ConvertTo-Json -Depth 5)
}
else {
    Write-Host "Created shortcut: $linkPath"
}
