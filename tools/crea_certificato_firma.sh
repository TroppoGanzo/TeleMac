#!/bin/bash
# Crea il certificato personale di firma del codice "Edoardo Ciarlo".
#
#   tools/crea_certificato_firma.sh                -> scrive firma-edoardo-ciarlo.p12
#                                                     e stampa i valori da mettere nei
#                                                     "secrets" di GitHub
#   tools/crea_certificato_firma.sh --portachiavi  -> lo importa nel portachiavi del Mac
#
# È un certificato "autofirmato": macOS mostra "Edoardo Ciarlo" come autore
# della firma e, finché l'app è firmata sempre con lo stesso certificato, il
# permesso di Accessibilità resta valido anche dopo gli aggiornamenti. Per non
# avere nemmeno l'avviso di Gatekeeper al primo avvio serve invece un
# certificato "Developer ID" del programma sviluppatori Apple (vedi README).
#
# La chiave privata è un segreto: non va mai messa nel repository.
set -euo pipefail

SIGNER="Edoardo Ciarlo"
MODE="${1:-}"
OPENSSL=/usr/bin/openssl
[ -x "$OPENSSL" ] || OPENSSL=openssl
LEGACY=()
if "$OPENSSL" version | grep -q "^OpenSSL 3"; then LEGACY=(-legacy); fi  # .p12 leggibile da macOS

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cat > "$WORK/cert.cnf" <<CNF
[req]
distinguished_name = dn
prompt = no
[dn]
CN = $SIGNER
O = $SIGNER
[ext]
basicConstraints = critical, CA:FALSE
keyUsage = critical, digitalSignature
extendedKeyUsage = critical, codeSigning
subjectKeyIdentifier = hash
CNF

"$OPENSSL" req -x509 -newkey rsa:2048 -sha256 -days 3650 -nodes \
  -config "$WORK/cert.cnf" -extensions ext \
  -keyout "$WORK/key.pem" -out "$WORK/cert.pem" 2>/dev/null
PASSWORD="$("$OPENSSL" rand -hex 16)"
"$OPENSSL" pkcs12 -export ${LEGACY[@]+"${LEGACY[@]}"} -name "$SIGNER" \
  -inkey "$WORK/key.pem" -in "$WORK/cert.pem" \
  -out "$WORK/firma.p12" -passout "pass:$PASSWORD"

if [ "$MODE" = "--portachiavi" ]; then
  security import "$WORK/firma.p12" -P "$PASSWORD" -T /usr/bin/codesign
  echo "Certificato \"$SIGNER\" aggiunto al portachiavi (Accesso Portachiavi → login → I miei certificati)."
  exit 0
fi

OUT="${MODE:-firma-edoardo-ciarlo.p12}"
cp "$WORK/firma.p12" "$OUT"
echo "Creato $OUT (tienilo al sicuro: contiene la chiave privata)."
echo
echo "Per far firmare l'app a GitHub sempre con questo certificato, in"
echo "Settings → Secrets and variables → Actions → New repository secret aggiungi:"
echo
echo "  MAC_SIGN_P12       = (il testo qui sotto, tutto su una riga)"
base64 < "$OUT" | tr -d '\n'
echo
echo
echo "  MAC_SIGN_PASSWORD  = $PASSWORD"
