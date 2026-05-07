#!/usr/bin/env bash
set -euo pipefail

NAME="${NAME:-Redroller}"
ENTRY="${ENTRY:-web_panel_launcher.py}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_ROOT/.." && pwd)"

BUILD_ROOT="$PROJECT_ROOT/.pyinstaller-build"
WORK_PATH="$BUILD_ROOT/$NAME-$$"
SPEC_PATH="$BUILD_ROOT/spec-$$"
TEMP_DIST_PATH="$BUILD_ROOT/dist-$$"
FINAL_DIST_PATH="$PROJECT_ROOT/dist"
RELEASE_PATH="$FINAL_DIST_PATH/release-macos"
STAGING_PATH="$BUILD_ROOT/dmg-$NAME-$$"

WEB_PATH="$SCRIPT_ROOT/web"
CONFIG_EXAMPLE_PATH="$SCRIPT_ROOT/config.example.json"
HUNTER_PATH="$SCRIPT_ROOT/yc_ip_hunter.py"
WEB_PANEL_PATH="$SCRIPT_ROOT/web_panel.py"
ENTRY_PATH="$SCRIPT_ROOT/$ENTRY"
FINAL_DMG="$FINAL_DIST_PATH/$NAME-macOS.dmg"

cleanup() {
    rm -rf "$WORK_PATH" "$SPEC_PATH" "$TEMP_DIST_PATH" "$STAGING_PATH"
    rmdir "$BUILD_ROOT" 2>/dev/null || true
}
trap cleanup EXIT

cd "$PROJECT_ROOT"
mkdir -p "$BUILD_ROOT" "$FINAL_DIST_PATH"

if ! "$PYTHON_BIN" -m PyInstaller --version >/dev/null 2>&1; then
    "$PYTHON_BIN" -m pip install pyinstaller
fi

"$PYTHON_BIN" -m PyInstaller \
    --clean \
    --windowed \
    --workpath "$WORK_PATH" \
    --specpath "$SPEC_PATH" \
    --distpath "$TEMP_DIST_PATH" \
    --name "$NAME" \
    --osx-bundle-identifier "io.github.gorizontniy.redroller" \
    --hidden-import web_panel \
    --hidden-import yc_ip_hunter \
    --hidden-import jwt \
    --collect-submodules cryptography \
    --add-data "$WEB_PATH:web" \
    --add-data "$CONFIG_EXAMPLE_PATH:." \
    --add-data "$HUNTER_PATH:." \
    --add-data "$WEB_PANEL_PATH:." \
    "$ENTRY_PATH"

rm -rf "$RELEASE_PATH"
mkdir -p "$RELEASE_PATH" "$STAGING_PATH"
cp -R "$TEMP_DIST_PATH/$NAME.app" "$RELEASE_PATH/$NAME.app"
cp -R "$TEMP_DIST_PATH/$NAME.app" "$STAGING_PATH/$NAME.app"
ln -s /Applications "$STAGING_PATH/Applications"

cat > "$RELEASE_PATH/README-macOS.txt" <<'EOF'
Redroller для macOS

1. Откройте Redroller-macOS.dmg.
2. Перетащите Redroller.app в Applications.
3. Запустите Redroller.app.

Если macOS Gatekeeper блокирует приложение без подписи:
Control-click по Redroller.app -> Open -> Open.

Локальные данные и логи:
~/Library/Application Support/Redroller/.web-runtime

Приложение открывает локальную панель:
http://127.0.0.1:8787
EOF
cp "$RELEASE_PATH/README-macOS.txt" "$STAGING_PATH/README-macOS.txt"

rm -f "$FINAL_DMG"
hdiutil create \
    -volname "$NAME" \
    -srcfolder "$STAGING_PATH" \
    -ov \
    -format UDZO \
    "$FINAL_DMG"

echo "Built $RELEASE_PATH/$NAME.app"
echo "Built $FINAL_DMG"
