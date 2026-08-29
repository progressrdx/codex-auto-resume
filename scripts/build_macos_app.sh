#!/bin/zsh
set -euo pipefail

SCRIPT_DIR=${0:A:h}
PROJECT_ROOT=${SCRIPT_DIR:h}
TOOLS_DIR="$PROJECT_ROOT/.build/macos-tools"
PYINSTALLER_DIR="$PROJECT_ROOT/.build/pyinstaller"
SWIFT_DIR="$PROJECT_ROOT/.build/swift"
ICON_DIR="$PROJECT_ROOT/.build/AppIcon.iconset"
APP_DIR="$PROJECT_ROOT/dist/Codex Auto Resume.app"
ZIP_PATH="$PROJECT_ROOT/dist/Codex-Auto-Resume-macOS-arm64.zip"

if [[ "$APP_DIR" != "$PROJECT_ROOT/dist/Codex Auto Resume.app" ]]; then
  print -u2 "Refusing an unexpected output path"
  exit 2
fi

python3 -m venv "$TOOLS_DIR"
"$TOOLS_DIR/bin/python" -m pip install --quiet --disable-pip-version-check "pyinstaller==6.22.2"

rm -rf "$PYINSTALLER_DIR" "$SWIFT_DIR" "$ICON_DIR" "$APP_DIR" "$ZIP_PATH"
mkdir -p "$PYINSTALLER_DIR/spec" "$PROJECT_ROOT/dist"

"$TOOLS_DIR/bin/pyinstaller" \
  --noconfirm --clean --onedir \
  --name codex-auto-resume-cli \
  --paths "$PROJECT_ROOT" \
  --distpath "$PYINSTALLER_DIR/dist" \
  --workpath "$PYINSTALLER_DIR/work" \
  --specpath "$PYINSTALLER_DIR/spec" \
  "$PROJECT_ROOT/scripts/macos_cli_entry.py"

swift build --package-path "$PROJECT_ROOT/macos" -c release --build-path "$SWIFT_DIR"

mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources/Backend"
cp "$SWIFT_DIR/release/CodexAutoResumeApp" "$APP_DIR/Contents/MacOS/"
cp "$PROJECT_ROOT/macos/Info.plist" "$APP_DIR/Contents/"
cp -R "$PYINSTALLER_DIR/dist/codex-auto-resume-cli/." "$APP_DIR/Contents/Resources/Backend/"

mkdir -p "$ICON_DIR" "$PROJECT_ROOT/.build/icon-source"
qlmanage -t -s 1024 -o "$PROJECT_ROOT/.build/icon-source" "$PROJECT_ROOT/codex_resume/static/icon.svg" >/dev/null 2>&1
ICON_SOURCE="$PROJECT_ROOT/.build/icon-source/icon.svg.png"
for SIZE in 16 32 128 256 512; do
  sips -z "$SIZE" "$SIZE" "$ICON_SOURCE" --out "$ICON_DIR/icon_${SIZE}x${SIZE}.png" >/dev/null
  DOUBLE=$((SIZE * 2))
  sips -z "$DOUBLE" "$DOUBLE" "$ICON_SOURCE" --out "$ICON_DIR/icon_${SIZE}x${SIZE}@2x.png" >/dev/null
done
iconutil -c icns "$ICON_DIR" -o "$APP_DIR/Contents/Resources/AppIcon.icns"

codesign --force --deep --sign - "$APP_DIR"
ditto -c -k --keepParent "$APP_DIR" "$ZIP_PATH"
shasum -a 256 "$ZIP_PATH" > "$ZIP_PATH.sha256"

print "Built: $APP_DIR"
print "Archive: $ZIP_PATH"
print "Note: this local test build is ad-hoc signed, not notarized."
