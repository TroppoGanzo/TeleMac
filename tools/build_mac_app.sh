#!/bin/bash
# Crea TeleMac.app (e, con --dmg, anche TeleMac.dmg) firmata da Edoardo Ciarlo.
#
# Gira su macOS con gli strumenti per sviluppatori (swiftc, codesign). Uso:
#   tools/build_mac_app.sh [--dmg] [cartella di uscita, predefinita: build]
#
# Firma: usa TELEMAC_SIGN_IDENTITY se c'è; se no cerca nel portachiavi un
# certificato "Developer ID Application: Edoardo Ciarlo" e poi "Edoardo
# Ciarlo". Se non trova niente crea (una volta sola) un certificato personale
# "Edoardo Ciarlo" con tools/crea_certificato_firma.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SIGNER="Edoardo Ciarlo"
MAKE_DMG=0
if [ "${1:-}" = "--dmg" ]; then MAKE_DMG=1; shift; fi
OUT="${1:-$ROOT/build}"
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
APP="$OUT/TeleMac.app"
VERSION="${TELEMAC_VERSION:-5.0}"
BUILD="${TELEMAC_BUILD:-$(git -C "$ROOT" rev-list --count HEAD 2>/dev/null || echo 1)}"
MIN_MACOS="12.0"

echo "==> TeleMac $VERSION ($BUILD)"
# Si compila e si firma in una cartella temporanea: se il progetto è sulla
# Scrivania, iCloud può aggiungere attributi (com.apple.FinderInfo) che fanno
# fallire swiftc o codesign.
SRC="$(mktemp -d)"
trap 'rm -rf "$SRC"' EXIT
STAGE_APP="$SRC/TeleMac.app"
mkdir -p "$STAGE_APP/Contents/MacOS" "$STAGE_APP/Contents/Resources"

echo "==> Compilo l'app (Apple Silicon + Intel)"
cp "$ROOT"/macos/TeleMac/*.swift "$SRC/"
for arch in arm64 x86_64; do
  swiftc -O -target "$arch-apple-macos$MIN_MACOS" -o "$SRC/TeleMac-$arch" "$SRC"/*.swift
done
lipo -create "$SRC/TeleMac-arm64" "$SRC/TeleMac-x86_64" -output "$STAGE_APP/Contents/MacOS/TeleMac"

sed -e "s/__VERSION__/$VERSION/" -e "s/__BUILD__/$BUILD/" "$ROOT/macos/Info.plist" > "$STAGE_APP/Contents/Info.plist"
printf 'APPL????' > "$STAGE_APP/Contents/PkgInfo"
cp "$ROOT/macos/AppIcon.icns" "$STAGE_APP/Contents/Resources/AppIcon.icns"

echo "==> Copio le pagine web"
rsync -a --exclude '.DS_Store' "$ROOT/web/" "$STAGE_APP/Contents/Resources/web/"
xattr -cr "$STAGE_APP"

# ---- firma ----
IDENTITY="${TELEMAC_SIGN_IDENTITY:-}"
KEYCHAIN_ARGS=()
if [ -n "${TELEMAC_KEYCHAIN:-}" ]; then KEYCHAIN_ARGS=(--keychain "$TELEMAC_KEYCHAIN"); fi
if [ -z "$IDENTITY" ]; then
  if security find-identity -v -p codesigning | grep -q "Developer ID Application: $SIGNER"; then
    IDENTITY="$(security find-identity -v -p codesigning | grep "Developer ID Application: $SIGNER" | head -1 | sed -E 's/.*"(.*)"/\1/')"
  elif security find-certificate -c "$SIGNER" >/dev/null 2>&1; then
    IDENTITY="$SIGNER"
  else
    echo "==> Nessun certificato \"$SIGNER\": ne creo uno personale nel portachiavi"
    "$ROOT/tools/crea_certificato_firma.sh" --portachiavi
    IDENTITY="$SIGNER"
  fi
fi

TIMESTAMP="--timestamp=none"
case "$IDENTITY" in
  "Developer ID Application:"*) TIMESTAMP="--timestamp" ;;  # serve per la notarizzazione Apple
esac

echo "==> Firmo come: $IDENTITY"
codesign --force --options runtime $TIMESTAMP ${KEYCHAIN_ARGS[@]+"${KEYCHAIN_ARGS[@]}"} \
  --sign "$IDENTITY" "$STAGE_APP"
codesign --verify --strict --verbose=2 "$STAGE_APP"
codesign -dv "$STAGE_APP" 2>&1 | grep -E "Identifier|Authority|TeamIdentifier" || true

rm -rf "$APP"
ditto "$STAGE_APP" "$APP"

if [ "$MAKE_DMG" = 1 ]; then
  echo "==> Creo TeleMac.dmg"
  STAGE="$SRC/dmg"
  rm -rf "$STAGE" "$OUT/TeleMac.dmg"
  mkdir -p "$STAGE"
  ditto "$STAGE_APP" "$STAGE/TeleMac.app"
  ln -s /Applications "$STAGE/Applicazioni"
  hdiutil create -volname "TeleMac" -srcfolder "$STAGE" -ov -format UDZO "$SRC/TeleMac.dmg" >/dev/null
  codesign --force $TIMESTAMP ${KEYCHAIN_ARGS[@]+"${KEYCHAIN_ARGS[@]}"} --sign "$IDENTITY" "$SRC/TeleMac.dmg"
  cp "$SRC/TeleMac.dmg" "$OUT/TeleMac.dmg"
  echo "    $OUT/TeleMac.dmg"
fi

echo "==> Fatto: $APP"
