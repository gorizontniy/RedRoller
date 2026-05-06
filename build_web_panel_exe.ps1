param(
    [string]$Name = "IP_ROTATOR.V1",
    [string]$Entry = "web_panel_launcher.py"
)

$ErrorActionPreference = "Stop"

$BuildRoot = ".pyinstaller-build"
$WorkPath = Join-Path $BuildRoot "$Name-$PID"
$SpecPath = Join-Path $BuildRoot "spec-$PID"
$TempDistPath = Join-Path $BuildRoot "dist-$PID"
$FinalDistPath = "dist"
$ReleasePath = Join-Path $FinalDistPath "release"
$WebPath = (Resolve-Path "web").Path
$ConfigExamplePath = (Resolve-Path "config.example.json").Path
$HunterPath = (Resolve-Path "yc_ip_hunter.py").Path
$WebPanelPath = (Resolve-Path "web_panel.py").Path
$FinalExe = Join-Path $FinalDistPath "$Name.exe"

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
        $Entry

    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    $BuiltExe = Join-Path $TempDistPath "$Name.exe"
    New-Item -ItemType Directory -Force -Path $FinalDistPath | Out-Null
    Copy-Item -LiteralPath $BuiltExe -Destination $FinalExe -Force
    Write-Host "Built $FinalExe"

    if (Test-Path -LiteralPath $ReleasePath) {
        Remove-Item -LiteralPath $ReleasePath -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $ReleasePath | Out-Null
    Copy-Item -LiteralPath $BuiltExe -Destination (Join-Path $ReleasePath "$Name.exe") -Force
    @"
IP_ROTATOR.V1

Запуск:
1. Откройте IP_ROTATOR.V1.exe.
2. Заполните аккаунт Yandex Cloud в веб-панели.
3. Выберите режим крутки и нажмите "КРУТИТЬ БСы".

Где лежат данные:
%LOCALAPPDATA%\IP_ROTATOR.V1\.web-runtime

В этой папке хранятся SQLite, secret.key, runtime-config, state и логи.
Не удаляйте secret.key, если хотите сохранить доступ к зашифрованным ключам.

Если приложение не открывается, запустите exe ещё раз. Уже запущенная панель будет переиспользована.
"@ | Set-Content -LiteralPath (Join-Path $ReleasePath "README.txt") -Encoding UTF8
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
