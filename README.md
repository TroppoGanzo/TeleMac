# TeleMac

Trasforma l'iPhone in un telecomando "ad aria" per il Mac collegato alla TV:
inclini il telefono per muovere il cursore, come i telecomandini a giroscopio
dei decoder Android TV — nessuna app da installare, nessun abbonamento.

## Come funziona

- **Sul Mac** gira un piccolo server scritto in Python (già incluso in macOS,
  nessuna installazione). Mostra un QR code nel Terminale.
- **Sull'iPhone** apri Safari, inquadri il QR, e aggiungi la pagina alla
  schermata Home: da lì si apre a **tutto schermo**, senza barre né zoom, come
  un'app vera.
- Il telefono e il Mac si parlano sulla **stessa rete Wi-Fi di casa**: nessun
  dato esce su internet, nessun server esterno.

## La prima volta

1. **Permesso di Accessibilità.** Apri *Impostazioni di Sistema → Privacy e
   sicurezza → Accessibilità* e attiva il Terminale (o l'app che userai per
   lanciare TeleMac). Senza questo permesso macOS non lascia muovere il mouse
   via codice.
2. **Avvia il server**: doppio clic su `TeleMac.command`. La prima volta
   genera un certificato HTTPS locale (richiede `openssl`, già presente su
   macOS) e stampa un QR code nel Terminale.
3. Se macOS chiede di accettare connessioni in entrata per il firewall,
   rispondi **Consenti**.
4. **Inquadra il QR** con la fotocamera dell'iPhone (Mac e iPhone devono
   essere sullo stesso Wi-Fi). Si apre Safari.
5. La prima volta il certificato è autofirmato: Safari avviserà che il sito
   non è verificato. Tocca **Mostra dettagli → visita questo sito web** (una
   volta sola).
6. Tocca **Condividi → Aggiungi a Home**. Da ora hai un'icona TeleMac sulla
   schermata Home.
7. Apri l'app da quell'icona (non da Safari): parte a schermo intero.
8. Nella scheda **Puntatore**, tocca "Attiva puntatore" e concedi il permesso
   al movimento quando richiesto da iOS.

Le volte successive basta avviare `TeleMac.command` e aprire l'icona
sull'iPhone: se il Mac ha lo stesso indirizzo di rete resta tutto collegato
da solo.

> Se l'indirizzo IP del Mac cambia (capita ogni tanto con il DHCP di casa),
> riavvia il server e reinquadra il QR una volta.

## Come si usa

- **Puntatore**: inclina il telefono per muovere il cursore. Il pulsante
  grande al centro è il clic sinistro (tienilo premuto per trascinare); sotto
  ci sono clic destro, indietro, play/pausa, volume e schermo intero.
- **Tastiera**: scrivi normalmente, il testo arriva dove hai il cursore sul
  Mac.
- **Altro**: cambio app, Mission Control, comandi da browser, spegnimento
  schermo, e le impostazioni di sensibilità del puntatore (se va nella
  direzione sbagliata, prova gli interruttori "inverti"/"scambia assi").

## Limiti onesti

- Safari su iPhone non dà alle pagine web l'accesso al Bluetooth né una vera
  vibrazione fisica al tocco (solo un piccolo effetto visivo sul pulsante).
- Niente Dynamic Island: è una funzione riservata alle app native, non alle
  pagine web.
- Serve che il server sia acceso sul Mac; non parte da solo (a meno di
  configurarlo come voce di login, vedi sotto).

## Avvio automatico al login (facoltativo)

Se vuoi che TeleMac parta da solo ogni volta che accendi il Mac, apri
*Impostazioni di Sistema → Generali → Elementi login* e aggiungi
`TeleMac.command` alla lista "Apri automaticamente all'accesso".

## Sviluppo

```
python3 -m unittest discover -s tests
```

Per provare l'interfaccia senza toccare davvero mouse/tastiera (utile anche
fuori da un Mac):

```
python3 telemac/server.py --dry-run
```

Struttura del progetto:

```
telemac/          server (Python, solo libreria standard)
web/               app per iPhone (HTML/CSS/JS)
tests/             test automatici
TeleMac.command    avvio con doppio clic
```
