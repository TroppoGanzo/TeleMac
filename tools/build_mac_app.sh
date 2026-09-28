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
VERSION="${TELEMAC_VERSION:-4.0}"
BUILD="${TELEMAC_BUILD:-$(git -C "$ROOT" rev-list --count HEAD 2>/dev/null || echo 1)}"
MIN_MACOS="12.0"

echo "==> TeleMac $VERSION ($BUILD)"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

echo "==> Compilo l'app (Apple Silicon + Intel)"
for arch in arm64 x86_64; do
  swiftc -O -target "$arch-apple-macos$MIN_MACOS" \
    -o "$OUT/TeleMac-$arch" "$ROOT"/macos/TeleMac/*.swift
done
lipo -create "$OUT/TeleMac-arm64" "$OUT/TeleMac-x86_64" -output "$APP/Contents/MacOS/TeleMac"
rm -f "$OUT/TeleMac-arm64" "$OUT/TeleMac-x86_64"

sed -e "s/__VERSION__/$VERSION/" -e "s/__BUILD__/$BUILD/" "$ROOT/macos/Info.plist" > "$APP/Contents/Info.plist"
printf 'APPL????' > "$APP/Contents/PkgInfo"
cp "$ROOT/macos/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"

echo "==> Copio il server e le pagine"
for dir in telemac web; do
  rsync -a --exclude '__pycache__' --exclude '*.pyc' --exclude '.DS_Store' \
    "$ROOT/$dir/" "$APP/Contents/Resources/$dir/"
done

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
  --sign "$IDENTITY" "$APP"
codesign --verify --strict --verbose=2 "$APP"
codesign -dv "$APP" 2>&1 | grep -E "Identifier|Authority|TeamIdentifier" || true

if [ "$MAKE_DMG" = 1 ]; then
  echo "==> Creo TeleMac.dmg"
  STAGE="$OUT/dmg"
  rm -rf "$STAGE" "$OUT/TeleMac.dmg"
  mkdir -p "$STAGE"
  cp -R "$APP" "$STAGE/"
  ln -s /Applications "$STAGE/Applicazioni"
  hdiutil create -volname "TeleMac" -srcfolder "$STAGE" -ov -format UDZO "$OUT/TeleMac.dmg" >/dev/null
  rm -rf "$STAGE"
  codesign --force $TIMESTAMP ${KEYCHAIN_ARGS[@]+"${KEYCHAIN_ARGS[@]}"} --sign "$IDENTITY" "$OUT/TeleMac.dmg"
  echo "    $OUT/TeleMac.dmg"
fi

echo "==> Fatto: $APP"
