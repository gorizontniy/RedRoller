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
        --hidden-import web_panel `
        --hidden-import yc_ip_hunter `
        --hidden-import jwt `
        --collect-submodules cryptography `
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
    $ReadmeBase64 = "77u/UmVkcm9sbGVyCgrQl9Cw0L/Rg9GB0Log0LPQvtGC0L7QstC+0LPQviBleGU6CjEuINCh0LrQsNGH0LDQudGC0LUgUmVkcm9sbGVyLmV4ZSDRgdC+INGB0YLRgNCw0L3QuNGG0YsgR2l0SHViIFJlbGVhc2VzLgoyLiDQl9Cw0L/Rg9GB0YLQuNGC0LUgUmVkcm9sbGVyLmV4ZS4g0J/QsNC90LXQu9GMINC+0YLQutGA0L7QtdGC0YHRjyDQvdCwIGh0dHA6Ly8xMjcuMC4wLjE6ODc4Ny4KMy4g0JXRgdC70LggV2luZG93cyDQsdC70L7QutC40YDRg9C10YIg0YTQsNC50LssINC+0YLQutGA0L7QudGC0LUg0YHQstC+0LnRgdGC0LLQsCBSZWRyb2xsZXIuZXhlINC4INC90LDQttC80LjRgtC1IMKr0KDQsNC30LHQu9C+0LrQuNGA0L7QstCw0YLRjMK7LgoK0KfRgtC+INCd0JUg0L3Rg9C20L3QviDRgdGC0LDQstC40YLRjCDQtNC70Y8g0LPQvtGC0L7QstC+0LPQviBleGU6Ci0gUHl0aG9uLCBwaXAg0LggUHlJbnN0YWxsZXI7Ci0gWWFuZGV4IENsb3VkIENMSTsKLSBQeUpXVCDQuCBjcnlwdG9ncmFwaHkuCgrQp9GC0L4g0L3Rg9C20L3QviDQv9C+0LTQs9C+0YLQvtCy0LjRgtGMOgotIFdpbmRvd3MgMTAvMTE7Ci0gRWRnZSwgQ2hyb21lINC40LvQuCDQsdGA0LDRg9C30LXRgCDQv9C+INGD0LzQvtC70YfQsNC90LjRjjsKLSBZYW5kZXggQ2xvdWQg0LDQutC60LDRg9C90YIg0LggSlNPTi3QutC70Y7RhyDRgdC10YDQstC40YHQvdC+0LPQviDQsNC60LrQsNGD0L3RgtCwOwotIFRlbGVncmFtIGJvdCB0b2tlbiwg0LXRgdC70Lgg0L3Rg9C20L3RiyBUZWxlZ3JhbS3Rg9Cy0LXQtNC+0LzQu9C10L3QuNGPLgoK0JTQsNC90L3Ri9C1INC4INC70L7Qs9C4OgolTE9DQUxBUFBEQVRBJVxSZWRyb2xsZXJcLndlYi1ydW50aW1lCgrQkiDRjdGC0L7QuSDQv9Cw0L/QutC1INGF0YDQsNC90Y/RgtGB0Y8gU1FMaXRlLCBzZWNyZXQua2V5LCBydW50aW1lLWNvbmZpZywgc3RhdGUg0Lgg0LvQvtCz0LguINCd0LUg0YPQtNCw0LvRj9C50YLQtSBzZWNyZXQua2V5LCDQtdGB0LvQuCDRhdC+0YLQuNGC0LUg0YHQvtGF0YDQsNC90LjRgtGMINC00L7RgdGC0YPQvyDQuiDQt9Cw0YjQuNGE0YDQvtCy0LDQvdC90YvQvCDQutC70Y7Rh9Cw0Lwg0LIg0L/QsNC90LXQu9C4LgoK0JXRgdC70LggZXhlINC90LUg0LfQsNC/0YPRgdC60LDQtdGC0YHRjzoKMS4g0KPRgdGC0LDQvdC+0LLQuNGC0LUgUHl0aG9uIDMuOSsg0LggR2l0IGZvciBXaW5kb3dzLgoyLiDQktGL0L/QvtC70L3QuNGC0LU6CiAgIGdpdCBjbG9uZSBodHRwczovL2dpdGh1Yi5jb20vZ29yaXpvbnRuaXkvUmVkUm9sbGVyLmdpdAogICBjZCBSZWRSb2xsZXIKICAgcHkgLTMgLW0gdmVudiAudmVudgogICAuXC52ZW52XFNjcmlwdHNcQWN0aXZhdGUucHMxCiAgIHB5dGhvbiAtbSBwaXAgaW5zdGFsbCAtLXVwZ3JhZGUgcGlwCiAgIHB5dGhvbiAtbSBwaXAgaW5zdGFsbCAtciAuXGJpblxyZXF1aXJlbWVudHMudHh0CiAgIHB5dGhvbiAuXGJpblx3ZWJfcGFuZWxfbGF1bmNoZXIucHkKCtCX0LDQv9Cw0YHQvdC+0Lkg0LfQsNC/0YPRgdC6INC/0LDQvdC10LvQuCDQsdC10LcgZGVza3RvcC3QvtC60L3QsDoKICAgcHl0aG9uIC5cYmluXHdlYl9wYW5lbC5weSAtLWhvc3QgMTI3LjAuMC4xIC0tcG9ydCA4Nzg3Cg=="
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
