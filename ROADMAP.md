# TeleMac per Mac — Road map

**Dove siamo (01/10/2026):** fase 2 in corso. La vecchia app (guscio Swift + server Python, compilata su Linux, sorgenti persi) è salvata come v1.0 in `~/Desktop/Versioni/TeleMac/`. Sto rifacendo tutto come progetto Xcode nativo, senza Python.

TeleMac trasforma l'iPhone in telecomando per il Mac (mouse, tastiera, tasti multimediali) sulla rete di casa.

## Fasi

1. **Capire la vecchia app** — fatto. Funzionava ma dipendeva dal Python di Xcode, era firmata con un'identità inventata e i sorgenti non esistono più.
2. **Rifarla nativa** — in corso. Un solo programma Swift: server sicuro (HTTPS + WebSocket), certificati, abbinamento con codice, controllo di mouse e tastiera, finestra con QR. Stessi file in `~/.telemac`, così l'iPhone già abbinato continua a funzionare.
3. **Provarla davvero** — test automatico del server (modalità prova, il Mac non si muove) e prova a mano con l'iPhone.
4. **Installarla e impacchettarla** — in `/Applications`, DMG nuovo, versione salvata.
5. **Firma Apple** — da fare solo se Edoardo apre un account Apple Developer (99 $/anno): serve per aprire l'app senza clic destro → Apri sugli altri Mac.

## Fuori programma
- Telecomando per Windows/Linux.
- App iPhone nativa (oggi è una pagina web aggiunta alla Home).
