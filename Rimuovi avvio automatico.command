#!/bin/bash
# Doppio clic per disattivare l'avvio automatico di TeleMac al login.
cd "$(dirname "$0")"
python3 telemac/autostart.py uninstall
read -n 1 -s -r -p "Premi un tasto per chiudere"
echo
