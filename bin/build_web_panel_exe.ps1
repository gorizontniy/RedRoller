param(
    [string]$Name = "Redroller",
    [string]$Entry = "web_panel_launcher.py"
)

$ErrorActionPreference = "Stop"

$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Resolve-Path (Join-Path $ScriptRoot "..")).Path

$BuildRoot = Join-Path $ProjectRoot ".pyinstaller-build"
$WorkPath = Join-Path $BuildRoot "$Name-$PID"
$SpecPath = Join-Path $BuildRoot "spec-$PID"
$TempDistPath = Join-Path $BuildRoot "dist-$PID"
$FinalDistPath = Join-Path $ProjectRoot "dist"
$ReleasePath = Join-Path $FinalDistPath "release"
$WebPath = (Resolve-Path (Join-Path $ScriptRoot "web")).Path
$ConfigExamplePath = (Resolve-Path (Join-Path $ScriptRoot "config.example.json")).Path
$HunterPath = (Resolve-Path (Join-Path $ScriptRoot "yc_ip_hunter.py")).Path
$WebPanelPath = (Resolve-Path (Join-Path $ScriptRoot "web_panel.py")).Path
$EntryPath = (Resolve-Path (Join-Path $ScriptRoot $Entry)).Path
$FinalExe = Join-Path $FinalDistPath "$Name.exe"
$RootExe = Join-Path $ProjectRoot "$Name.exe"
$LegacyRootExe = Join-Path $ProjectRoot "IP_ROTATOR.V1.exe"
$LegacyDistExe = Join-Path $FinalDistPath "IP_ROTATOR.V1.exe"

try {
    $PyInstallerReady = $false
    try {
        python -m PyInstaller --version *> $null
        $PyInstallerReady = ($LASTEXITCODE -eq 0)
    }
    catch {
        $PyInstallerReady = $false
    }

    if (-not $PyInstallerReady) {
        python -m pip install pyinstaller
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
    }

    python -m PyInstaller `
        --clean `
        --onefile `
        --noconsole `
        --workpath $WorkPath `
        --specpath $SpecPath `
        --distpath $TempDistPath `
        --name $Name `
        --add-data "$WebPath;web" `
        --add-data "$ConfigExamplePath;." `
        --add-data "$HunterPath;." `
        --add-data "$WebPanelPath;." `
        $EntryPath

    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    $BuiltExe = Join-Path $TempDistPath "$Name.exe"
    New-Item -ItemType Directory -Force -Path $FinalDistPath | Out-Null
    Copy-Item -LiteralPath $BuiltExe -Destination $FinalExe -Force
    Copy-Item -LiteralPath $BuiltExe -Destination $RootExe -Force
    foreach ($LegacyExe in @($LegacyRootExe, $LegacyDistExe)) {
        if ($LegacyExe -ne $RootExe -and (Test-Path -LiteralPath $LegacyExe)) {
            Remove-Item -LiteralPath $LegacyExe -Force
        }
    }
    Write-Host "Built $FinalExe"
    Write-Host "Copied launcher to $RootExe"

    if (Test-Path -LiteralPath $ReleasePath) {
        Remove-Item -LiteralPath $ReleasePath -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $ReleasePath | Out-Null
    Copy-Item -LiteralPath $BuiltExe -Destination (Join-Path $ReleasePath "$Name.exe") -Force
    $ReadmeBase64 = "UmVkcm9sbGVyCgrQl9Cw0L/Rg9GB0Lo6CjEuINCe0YLQutGA0L7QudGC0LUgUmVkcm9sbGVyLmV4ZS4KMi4g0JLRi9Cx0LXRgNC40YLQtSDRgNC10LbQuNC8INC60YDRg9GC0LrQuCDQsiDRhNC+0YDQvNC1INCw0LrQutCw0YPQvdGC0LAuCjMuINCX0LDQv9C+0LvQvdC40YLQtSDQtNCw0L3QvdGL0LUgWWFuZGV4IENsb3VkINC4INC90LDQttC80LjRgtC1ICLQmtCg0KPQotCY0KLQrCDQkdCh0YsiLgoK0JPQtNC1INC70LXQttCw0YIg0LTQsNC90L3Ri9C1OgolTE9DQUxBUFBEQVRBJVxSZWRyb2xsZXJcLndlYi1ydW50aW1lCgrQkiDRjdGC0L7QuSDQv9Cw0L/QutC1INGF0YDQsNC90Y/RgtGB0Y8gU1FMaXRlLCBzZWNyZXQua2V5LCBydW50aW1lLWNvbmZpZywgc3RhdGUg0Lgg0LvQvtCz0LguCtCd0LUg0YPQtNCw0LvRj9C50YLQtSBzZWNyZXQua2V5LCDQtdGB0LvQuCDRhdC+0YLQuNGC0LUg0YHQvtGF0YDQsNC90LjRgtGMINC00L7RgdGC0YPQvyDQuiDQt9Cw0YjQuNGE0YDQvtCy0LDQvdC90YvQvCDQutC70Y7Rh9Cw0LwuCgrQldGB0LvQuCDQv9GA0LjQu9C+0LbQtdC90LjQtSDQvdC1INC+0YLQutGA0YvQstCw0LXRgtGB0Y8sINC30LDQv9GD0YHRgtC40YLQtSBleGUg0LXRidGRINGA0LDQty4g0KPQttC1INC30LDQv9GD0YnQtdC90L3QsNGPINC/0LDQvdC10LvRjCDQsdGD0LTQtdGCINC/0LXRgNC10LjRgdC/0L7Qu9GM0LfQvtCy0LDQvdCwLgo="
    [IO.File]::WriteAllBytes(
        (Join-Path $ReleasePath "README.txt"),
        [Convert]::FromBase64String($ReadmeBase64)
    )
    Write-Host "Prepared $ReleasePath"
}
finally {
    foreach ($Path in @($WorkPath, $SpecPath, $TempDistPath)) {
        if (Test-Path -LiteralPath $Path) {
            Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    if (Test-Path -LiteralPath $BuildRoot) {
        $Remaining = Get-ChildItem -LiteralPath $BuildRoot -Force -ErrorAction SilentlyContinue
        if (-not $Remaining) {
            Remove-Item -LiteralPath $BuildRoot -Force -ErrorAction SilentlyContinue
        }
    }
}
