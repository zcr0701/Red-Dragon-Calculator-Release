param(
    [string]$Version = "1.0.4078.44"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$third = Join-Path $root "third_party\webview2"
$inc = Join-Path $third "include"
$bin = Join-Path $third "bin"

New-Item -ItemType Directory -Force -Path $inc, $bin | Out-Null

$tmp = Join-Path $env:TEMP "wv2_sdk"
New-Item -ItemType Directory -Force -Path $tmp | Out-Null

$nupkg = Join-Path $tmp "microsoft.web.webview2.$Version.nupkg"
if (-not (Test-Path $nupkg)) {
    Invoke-WebRequest "https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/$Version/microsoft.web.webview2.$Version.nupkg" -OutFile $nupkg -TimeoutSec 180
}

$zip = Join-Path $tmp "sdk.zip"
Copy-Item $nupkg $zip -Force
$dest = Join-Path $tmp "extracted_$Version"
if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
Expand-Archive $zip $dest

Get-ChildItem (Join-Path $dest "build\native\include") -Recurse -File -Filter *.h | ForEach-Object {
    Copy-Item $_.FullName (Join-Path $inc $_.Name) -Force
}

Get-ChildItem (Join-Path $dest "runtimes") -Recurse -File -Filter WebView2Loader.dll | ForEach-Object {
    Copy-Item $_.FullName (Join-Path $bin $_.Name) -Force
}

Write-Output ("Headers: {0}" -f (Get-ChildItem $inc -File).Count)
Write-Output ("Loader DLL: {0}" -f (Get-ChildItem $bin -File).Count)
