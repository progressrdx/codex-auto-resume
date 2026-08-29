#!/bin/zsh
set -euo pipefail

SCRIPT_DIR=${0:A:h}
PROJECT_ROOT=${SCRIPT_DIR:h}
SOURCE_APP="$PROJECT_ROOT/dist/Codex Auto Resume.app"
RELEASE_DIR="$PROJECT_ROOT/dist/release"
RELEASE_APP="$RELEASE_DIR/Codex Auto Resume.app"
UPLOAD_ZIP="$RELEASE_DIR/Codex-Auto-Resume-notary-upload.zip"
FINAL_ZIP="$RELEASE_DIR/Codex-Auto-Resume-macOS-arm64.zip"

IDENTITY=${DEVELOPER_ID_APPLICATION:-}
NOTARY_PROFILE=${NOTARYTOOL_PROFILE:-}

if [[ -z "$IDENTITY" || -z "$NOTARY_PROFILE" ]]; then
  print -u2 "Set DEVELOPER_ID_APPLICATION and NOTARYTOOL_PROFILE first."
  print -u2 "Credentials must be stored in Keychain with xcrun notarytool store-credentials."
  exit 2
fi
if [[ ! -d "$SOURCE_APP" ]]; then
  print -u2 "Build the app first with ./scripts/build_macos_app.sh"
  exit 2
fi
if ! security find-identity -v -p codesigning | grep -Fq "\"$IDENTITY\""; then
  print -u2 "The requested Developer ID Application identity is not available in Keychain."
  exit 2
fi
if [[ "$RELEASE_DIR" != "$PROJECT_ROOT/dist/release" ]]; then
  print -u2 "Refusing an unexpected release path."
  exit 2
fi

rm -rf "$RELEASE_DIR"
mkdir -p "$RELEASE_DIR"
ditto "$SOURCE_APP" "$RELEASE_APP"

# The app contains a PyInstaller onedir runtime. Sign its nested code and the
# outer SwiftUI bundle with a secure timestamp and Hardened Runtime.
codesign --force --deep --options runtime --timestamp --sign "$IDENTITY" "$RELEASE_APP"
codesign --verify --deep --strict --verbose=2 "$RELEASE_APP"

ditto -c -k --keepParent "$RELEASE_APP" "$UPLOAD_ZIP"
xcrun notarytool submit "$UPLOAD_ZIP" --keychain-profile "$NOTARY_PROFILE" --wait
xcrun stapler staple "$RELEASE_APP"
xcrun stapler validate "$RELEASE_APP"
spctl --assess --type execute --verbose=2 "$RELEASE_APP"

ditto -c -k --keepParent "$RELEASE_APP" "$FINAL_ZIP"
shasum -a 256 "$FINAL_ZIP" > "$FINAL_ZIP.sha256"
print "Notarized release: $FINAL_ZIP"
