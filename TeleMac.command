#!/bin/bash
# Doppio clic su questo file per avviare TeleMac.
# La prima volta macOS potrebbe chiedere il permesso ad "Accessibilità" per il
# Terminale: vai in Impostazioni di Sistema > Privacy e sicurezza > Accessibilità
# e attivalo, poi rilancia questo file.
cd "$(dirname "$0")"
python3 telemac/server.py
