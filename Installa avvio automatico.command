#!/bin/bash
# Doppio clic per far partire TeleMac da solo a ogni accesso (LaunchAgent).
cd "$(dirname "$0")"
python3 telemac/autostart.py install
read -n 1 -s -r -p "Premi un tasto per chiudere"
echo
