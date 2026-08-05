param(
    [string]$Dotnet = "$env:USERPROFILE\.dotnet\dotnet.exe",
    [string]$HdtRoot = "$env:LOCALAPPDATA\HearthstoneDeckTracker"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

$appDir = Get-ChildItem $HdtRoot -Directory -Filter "app-*" |
    Sort-Object Name -Descending |
    Select-Object -First 1

if (-not $appDir) {
    throw "HDT app directory not found under $HdtRoot"
}

$hdtDir = $appDir.FullName
Write-Output "HDT dir: $hdtDir"

& $Dotnet build "$PSScriptRoot\RedDragonStateExport.csproj" `
    -c Release `
    -p:HDT_DIR="$hdtDir"

if ($LASTEXITCODE -ne 0) {
    throw "Build failed"
}

# HDT syncs plugins from the roaming dir into the app dir at startup.
# A plugin placed directly in the app dir gets deleted by SyncPlugins.
$pluginDir = Join-Path $env:APPDATA "HearthstoneDeckTracker\Plugins"
New-Item -ItemType Directory -Force -Path $pluginDir | Out-Null

Copy-Item -Force "$PSScriptRoot\bin\Release\RedDragonStateExport.dll" (Join-Path $pluginDir "RedDragonStateExport.dll")
Write-Output "Plugin copied to: $pluginDir\RedDragonStateExport.dll"
