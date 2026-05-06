param(
    [string]$Name = "IP_ROTATOR.V1",
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

try {
    if (-not (python -m PyInstaller --version 2>$null)) {
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
    Write-Host "Built $FinalExe"
    Write-Host "Copied launcher to $RootExe"

    if (Test-Path -LiteralPath $ReleasePath) {
        Remove-Item -LiteralPath $ReleasePath -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $ReleasePath | Out-Null
    Copy-Item -LiteralPath $BuiltExe -Destination (Join-Path $ReleasePath "$Name.exe") -Force
    $ReadmeBase64 = "SVBfUk9UQVRPUi5WMQoK0JfQsNC/0YPRgdC6OgoxLiDQntGC0LrRgNC+0LnRgtC1IElQX1JPVEFUT1IuVjEuZXhlLgoyLiDQl9Cw0L/QvtC70L3QuNGC0LUg0LDQutC60LDRg9C90YIgWWFuZGV4IENsb3VkINCyINCy0LXQsS3Qv9Cw0L3QtdC70LguCjMuINCS0YvQsdC10YDQuNGC0LUg0YDQtdC20LjQvCDQutGA0YPRgtC60Lgg0Lgg0L3QsNC20LzQuNGC0LUgItCa0KDQo9Ci0JjQotCsINCR0KHRiyIuCgrQk9C00LUg0LvQtdC20LDRgiDQtNCw0L3QvdGL0LU6CiVMT0NBTEFQUERBVEElXElQX1JPVEFUT1IuVjFcLndlYi1ydW50aW1lCgrQkiDRjdGC0L7QuSDQv9Cw0L/QutC1INGF0YDQsNC90Y/RgtGB0Y8gU1FMaXRlLCBzZWNyZXQua2V5LCBydW50aW1lLWNvbmZpZywgc3RhdGUg0Lgg0LvQvtCz0LguCtCd0LUg0YPQtNCw0LvRj9C50YLQtSBzZWNyZXQua2V5LCDQtdGB0LvQuCDRhdC+0YLQuNGC0LUg0YHQvtGF0YDQsNC90LjRgtGMINC00L7RgdGC0YPQvyDQuiDQt9Cw0YjQuNGE0YDQvtCy0LDQvdC90YvQvCDQutC70Y7Rh9Cw0LwuCgrQldGB0LvQuCDQv9GA0LjQu9C+0LbQtdC90LjQtSDQvdC1INC+0YLQutGA0YvQstCw0LXRgtGB0Y8sINC30LDQv9GD0YHRgtC40YLQtSBleGUg0LXRidGRINGA0LDQty4g0KPQttC1INC30LDQv9GD0YnQtdC90L3QsNGPINC/0LDQvdC10LvRjCDQsdGD0LTQtdGCINC/0LXRgNC10LjRgdC/0L7Qu9GM0LfQvtCy0LDQvdCwLgo="
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
