#Requires -Version 5.1
<#
.SYNOPSIS
  Install the Rotatrix Maya plugin and Maya profile.

.DESCRIPTION
  Run from the repository root (or double-click install.bat):

    powershell -ExecutionPolicy Bypass -File .\install.ps1

  Copies plug-ins, scripts, lib, and the ZBrush startup script into Maya's
  modules folder, writes a .mod file so Maya finds them, points
  ZBRUSH_PLUGIN_PATH at the ZBrush plugin, merges the Maya and ZBrush
  profiles into Rotatrix's user config, and restarts Rotatrix if it is
  running so those profiles load.

.PARAMETER NoRestartRotatrix
  Write the profile to disk but do not restart a running Rotatrix app.
  You will then need to click Apply in Config, or quit and relaunch Rotatrix.
#>
[CmdletBinding()]
param(
    [switch]$NoRestartRotatrix
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Resolve-MayaProfilePath {
    $candidates = @(
        (Join-Path $Root "MayaProfile.YAML"),
        (Join-Path $Root "MayaProfile.yaml"),
        (Join-Path $Root "profiles\maya.yaml")
    )
    foreach ($path in $candidates) {
        if (Test-Path -LiteralPath $path) {
            return $path
        }
    }
    throw "Could not find MayaProfile.YAML in $Root"
}

function Get-MayaModulesDir {
    if ($env:MAYA_APP_DIR) {
        return (Join-Path $env:MAYA_APP_DIR "modules")
    }
    $docs = [Environment]::GetFolderPath("MyDocuments")
    if (-not $docs) {
        $docs = Join-Path $env:USERPROFILE "Documents"
    }
    return (Join-Path $docs "maya\modules")
}

function Copy-PluginTree {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$InstallRoot
    )
    $src = Join-Path $Root $Name
    $dst = Join-Path $InstallRoot $Name
    if (-not (Test-Path -LiteralPath $src)) {
        throw "Missing source folder: $src"
    }
    New-Item -ItemType Directory -Force -Path $dst | Out-Null
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & robocopy $src $dst /E /NFL /NDL /NJH /NJS /NC /NS /NP /XD __pycache__ /XF *.pyc | Out-Null
    $code = $LASTEXITCODE
    $ErrorActionPreference = $prevEap
    if ($code -ge 8) {
        throw "Failed to copy $Name (robocopy exit $code)"
    }
}

function Read-Utf8Text {
    param([Parameter(Mandatory = $true)][string]$Path)
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
        return [System.Text.Encoding]::UTF8.GetString($bytes, 3, $bytes.Length - 3)
    }
    return [System.Text.Encoding]::UTF8.GetString($bytes)
}

function Write-Utf8Text {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Text
    )
    $utf8 = New-Object System.Text.UTF8Encoding $false
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    [System.IO.File]::WriteAllText($Path, $Text.TrimEnd() + "`n", $utf8)
}

function Get-YamlIndentedBlock {
    param(
        [Parameter(Mandatory = $true)][string]$Text,
        [Parameter(Mandatory = $true)][int]$Indent,
        [Parameter(Mandatory = $true)][string]$Key
    )
    $normalized = $Text -replace "`r`n", "`n" -replace "`r", "`n"
    $lines = $normalized -split "`n", -1
    $prefix = (" " * $Indent) + $Key + ":"
    $start = -1
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i].StartsWith($prefix) -and (
            $lines[$i].Length -eq $prefix.Length -or
            $lines[$i][$prefix.Length] -match "[\s#]"
        )) {
            $start = $i
            break
        }
    }
    if ($start -lt 0) {
        return $null
    }
    $end = $lines.Count
    for ($i = $start + 1; $i -lt $lines.Count; $i++) {
        $line = $lines[$i]
        if ($line -match "^[\s]*$" -or $line -match "^[\s]*#") {
            continue
        }
        $leading = ($line -replace "[^ ].*$", "").Length
        if ($leading -le $Indent) {
            $end = $i
            break
        }
    }
    return ($lines[$start..($end - 1)] -join "`n").TrimEnd()
}

function Merge-MayaUserConfig {
    param(
        [string]$Existing,
        [Parameter(Mandatory = $true)][string]$MayaProfile,
        [string]$ProfileKey = "maya"
    )
    $mayaBlock = Get-YamlIndentedBlock -Text $MayaProfile -Indent 2 -Key $ProfileKey
    if (-not $mayaBlock) {
        throw "Profile YAML does not contain a profiles.$ProfileKey block"
    }

    $existingNorm = ""
    if ($Existing) {
        $existingNorm = ($Existing -replace "`r`n", "`n" -replace "`r", "`n").Trim()
    }
    if (-not $existingNorm -or $existingNorm -match "^#") {
        $bare = $existingNorm -replace "(?m)^#.*$", ""
        if (-not $bare.Trim()) {
            return ($MayaProfile -replace "`r`n", "`n" -replace "`r", "`n").TrimEnd() + "`n"
        }
    }

    $lines = @()
    if ($existingNorm) {
        $lines = [System.Collections.Generic.List[string]]::new()
        foreach ($line in ($existingNorm -split "`n", -1)) {
            [void]$lines.Add($line)
        }
    } else {
        $lines = [System.Collections.Generic.List[string]]::new()
    }

    $mayaPrefix = "  ${ProfileKey}:"
    $start = -1
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i].StartsWith($mayaPrefix) -and (
            $lines[$i].Length -eq $mayaPrefix.Length -or
            $lines[$i][$mayaPrefix.Length] -match "[\s#]"
        )) {
            $start = $i
            break
        }
    }

    $mayaLines = [string[]]($mayaBlock -split "`n", -1)
    if ($start -ge 0) {
        $end = $lines.Count
        for ($i = $start + 1; $i -lt $lines.Count; $i++) {
            $line = $lines[$i]
            if ($line -match "^[\s]*$" -or $line -match "^[\s]*#") {
                continue
            }
            $leading = ($line -replace "[^ ].*$", "").Length
            if ($leading -le 2) {
                $end = $i
                break
            }
        }
        $lines.RemoveRange($start, $end - $start)
        $lines.InsertRange($start, [string[]]$mayaLines)
        return (($lines -join "`n").TrimEnd() + "`n")
    }

    $profilesIndex = -1
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match "^profiles:\s*(#.*)?$") {
            $profilesIndex = $i
            break
        }
    }
    if ($profilesIndex -ge 0) {
        $insertAt = $profilesIndex + 1
        $lines.InsertRange($insertAt, [string[]]$mayaLines)
        return (($lines -join "`n").TrimEnd() + "`n")
    }

    if ($lines.Count -gt 0) {
        [void]$lines.Add("")
        [void]$lines.Add("profiles:")
        foreach ($line in $mayaLines) {
            [void]$lines.Add($line)
        }
        return (($lines -join "`n").TrimEnd() + "`n")
    }

    return ($MayaProfile -replace "`r`n", "`n" -replace "`r", "`n").TrimEnd() + "`n"
}

function Get-RotatrixProcess {
    Get-CimInstance Win32_Process -Filter "Name='Rotatrix.exe'" -ErrorAction SilentlyContinue |
        Select-Object -First 1
}

function Restart-RotatrixApp {
    param([string]$ExePath)

    $running = @(Get-Process -Name "Rotatrix" -ErrorAction SilentlyContinue)
    if ($running.Count -eq 0) {
        if ($ExePath -and (Test-Path -LiteralPath $ExePath)) {
            Write-Host "Starting Rotatrix: $ExePath"
            Start-Process -FilePath $ExePath
            return $true
        }
        Write-Host "Rotatrix is not running. The profile will load the next time you start it."
        return $false
    }

    if (-not $ExePath) {
        $ExePath = $running[0].Path
    }
    Write-Host "Restarting Rotatrix so it loads the Maya and ZBrush profiles..."
    $running | Stop-Process -Force
    $deadline = (Get-Date).AddSeconds(15)
    while ((Get-Process -Name "Rotatrix" -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 200
    }
    Start-Sleep -Seconds 1
    if (-not $ExePath -or -not (Test-Path -LiteralPath $ExePath)) {
        throw "Rotatrix was stopped but its executable path could not be found. Start it from the Start menu."
    }
    Start-Process -FilePath $ExePath
    return $true
}

function Set-ZBrushPluginPath {
    param([Parameter(Mandatory = $true)][string]$PluginDir)
    $name = "ZBRUSH_PLUGIN_PATH"
    $current = [Environment]::GetEnvironmentVariable($name, "User")
    $parts = New-Object System.Collections.Generic.List[string]
    if ($current) {
        foreach ($piece in ($current -split ";")) {
            if ($piece -and ($piece.TrimEnd("\") -ne $PluginDir.TrimEnd("\"))) {
                $parts.Add($piece)
            }
        }
    }
    $parts.Insert(0, $PluginDir)
    [Environment]::SetEnvironmentVariable($name, ($parts -join ";"), "User")
}

function Publish-EnvChange {
    if (-not ("Win32.NativeMethods" -as [type])) {
        Add-Type -Namespace Win32 -Name NativeMethods -MemberDefinition @"
[System.Runtime.InteropServices.DllImport("user32.dll", SetLastError=true, CharSet=System.Runtime.InteropServices.CharSet.Auto)]
public static extern System.IntPtr SendMessageTimeout(System.IntPtr hWnd, int Msg, System.UIntPtr wParam, string lParam, int fuFlags, int uTimeout, out System.UIntPtr lpdwResult);
"@
    }
    $result = [UIntPtr]::Zero
    [Win32.NativeMethods]::SendMessageTimeout(
        [IntPtr]0xffff, 0x001A, [UIntPtr]::Zero, "Environment", 2, 5000, [ref]$result
    ) | Out-Null
}

# ── Maya plugin files ───────────────────────────────────────────────
Write-Step "Installing Maya plugin files"
$modulesDir = Get-MayaModulesDir
$installRoot = Join-Path $modulesDir "rotatrix_maya"
New-Item -ItemType Directory -Force -Path $installRoot | Out-Null

Copy-PluginTree -Name "plug-ins" -InstallRoot $installRoot
Copy-PluginTree -Name "scripts" -InstallRoot $installRoot
Copy-PluginTree -Name "lib" -InstallRoot $installRoot
Copy-PluginTree -Name "zbrush_plugin" -InstallRoot $installRoot

$modPath = Join-Path $modulesDir "rotatrix_maya.mod"
$modRoot = $installRoot.Replace("\", "/")
# One description per release. Maya skipped 2021. Each running Maya
# loads only the block whose MAYAVERSION matches, all pointing at the
# same files. Documents/maya/modules is on every version's module path.
$mayaVersions = @(2020, 2022, 2023, 2024, 2025, 2026, 2027)
$modBlocks = foreach ($version in $mayaVersions) {
@"
+ MAYAVERSION:$version rotatrix_maya 0.1.0 $modRoot
MAYA_PLUG_IN_PATH +:= plug-ins
MAYA_SCRIPT_PATH +:= scripts
PYTHONPATH +:= scripts
PYTHONPATH +:= lib

"@
}
$modText = $modBlocks -join ""
Set-Content -Path $modPath -Value $modText -Encoding ASCII
Write-Host "Maya module: $modPath"
Write-Host "Plugin files: $installRoot"

# ── Rotatrix Maya profile ───────────────────────────────────────────
Write-Step "Installing Rotatrix Maya profile"
$profileSrc = Resolve-MayaProfilePath
Write-Host "Source: $profileSrc"
$mayaProfile = Read-Utf8Text -Path $profileSrc

$configDir = Join-Path $env:LOCALAPPDATA "Rotatrix"
$configPath = Join-Path $configDir "config.yaml"
$existing = ""
if (Test-Path -LiteralPath $configPath) {
    $existing = Read-Utf8Text -Path $configPath
}
$merged = Merge-MayaUserConfig -Existing $existing -MayaProfile $mayaProfile

Write-Step "Installing Rotatrix ZBrush profile"
$zbrushSrc = Join-Path $Root "profiles\zbrush.yaml"
if (-not (Test-Path -LiteralPath $zbrushSrc)) {
    throw "Could not find profiles\zbrush.yaml in $Root"
}
Write-Host "Source: $zbrushSrc"
$zbrushProfile = Read-Utf8Text -Path $zbrushSrc
$merged = Merge-MayaUserConfig -Existing $merged -MayaProfile $zbrushProfile -ProfileKey "zbrush"
Write-Utf8Text -Path $configPath -Text $merged
Write-Host "User config: $configPath"

Write-Step "Pointing ZBrush at the plugin"
$zbrushPluginDir = Join-Path $installRoot "zbrush_plugin"
try {
    Set-ZBrushPluginPath -PluginDir $zbrushPluginDir
    Publish-EnvChange
    Write-Host "ZBRUSH_PLUGIN_PATH=$zbrushPluginDir"
} catch {
    Write-Host "Could not set ZBRUSH_PLUGIN_PATH: $($_.Exception.Message)" -ForegroundColor Yellow
    Write-Host "Set that user environment variable to: $zbrushPluginDir"
}

$rotatrixProc = Get-RotatrixProcess
$rotatrixExe = $null
if ($rotatrixProc) {
    $rotatrixExe = $rotatrixProc.ExecutablePath
} elseif (Test-Path -LiteralPath (Join-Path $env:LOCALAPPDATA "RotatrixApp\current\Rotatrix.exe")) {
    $rotatrixExe = Join-Path $env:LOCALAPPDATA "RotatrixApp\current\Rotatrix.exe"
}

if ($NoRestartRotatrix) {
    Write-Host "Skipped Rotatrix restart (-NoRestartRotatrix)."
    Write-Host "If Rotatrix is already running, click Apply in Config or quit and relaunch it."
} else {
    try {
        Restart-RotatrixApp -ExePath $rotatrixExe | Out-Null
    } catch {
        Write-Host "Could not restart Rotatrix: $($_.Exception.Message)" -ForegroundColor Yellow
        Write-Host "Quit Rotatrix from the tray icon and start it again so the Maya and ZBrush profiles load."
    }
}

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host "Restart Maya (2020 through 2027). Maya 2020 must be in Python 3 mode:"
Write-Host "  set MAYA_PYTHON_VERSION=3 before launching Maya 2020."
Write-Host "Pen camera: hold Win and touch a model view. Trackball connect needs Maya 2023+."
Write-Host "ZBrush: quit and start ZBrush so it loads the plugin."
Write-Host "Hold Win and touch the canvas. Twist is added to Z after the orbit stroke."
exit 0
