# TeleMac

Trasforma l'iPhone in un telecomando "ad aria" per il Mac collegato alla TV. Muovi il
telefono e il cursore lo segue, come con i telecomandi delle smart TV. In più hai i
tasti per video, volume e tastiera, e la vibrazione a ogni tocco.

Non serve nessuna app dall'App Store e nessun abbonamento. Tutto passa dal Wi-Fi di
casa, senza server su internet.

**Prova subito, senza Mac:** https://troppoganzo.github.io/TeleMac/ (aprila da Safari
sull'iPhone). È la modalità demo: il cursore si muove su uno schermo finto, così senti
come risponde il giroscopio.

---

## Come funziona

```
iPhone (app nella schermata Home)  ──Wi-Fi di casa, HTTPS──▶  TeleMac.app sul Mac  ──▶  mouse e tastiera
```

- Sul Mac c'è **TeleMac.app**, un'app normale con la sua finestra: lì compaiono il QR
  da inquadrare e il codice per abbinare l'iPhone. Niente Terminale, niente icona di
  Python nel Dock: il piccolo server in Python gira nascosto dentro l'app e si ferma
  quando la chiudi.
- Sull'iPhone l'app è una pagina web aggiunta alla schermata Home. Si apre a tutto
  schermo come un'app vera: niente barre, niente zoom.
- Il collegamento è cifrato con un certificato creato dal tuo Mac. Il certificato vale
  solo per gli indirizzi della rete di casa.
- Ogni iPhone si abbina una volta sola, con un codice di 6 cifre che compare nella
  finestra di TeleMac sul Mac. Funziona come con l'Apple TV.
- L'app è **firmata da Edoardo Ciarlo** (vedi [Firma](#firma)).

## Installazione sul Mac

1. Scarica **TeleMac.dmg** dall'ultima release:
   https://github.com/TroppoGanzo/TeleMac/releases/latest
2. Aprilo e trascina **TeleMac** nella cartella **Applicazioni**.
3. Apri TeleMac. La prima volta macOS potrebbe dire che non può verificare lo
   sviluppatore: chiudi l'avviso, poi vai su *Impostazioni di Sistema → Privacy e
   sicurezza* e in fondo tocca **Apri comunque**. Si fa una volta sola (il perché è
   spiegato in [Firma](#firma)).
4. macOS chiede il permesso di **Accessibilità** per TeleMac: nella finestra di
   TeleMac tocca *Apri Impostazioni* e attiva **TeleMac**. Senza questo permesso il
   Mac ignora i comandi.
5. Se macOS chiede di usare i "dispositivi sulla rete locale" o di accettare
   "connessioni in entrata", rispondi **Consenti**.

TeleMac usa il Python di macOS, che arriva con gli "strumenti per sviluppatori". Se
mancano, la finestra di TeleMac lo dice e ha un pulsante per installarli (gratis,
pochi minuti).

Se avevi la versione precedente con l'avvio automatico dal Terminale, TeleMac.app
lo toglie da sola al primo avvio. I vecchi file `.command` non servono più.

## Collegare l'iPhone (una volta sola, circa 3 minuti)

Con l'iPhone sulla stessa rete Wi-Fi del Mac:

1. Inquadra con la fotocamera il **QR nella finestra di TeleMac**: si apre la pagina
   di configurazione.
2. **Installa il certificato**: tocca il pulsante, poi apri *Impostazioni*. In alto trovi
   *Profilo scaricato*: tocca *Installa*.
3. **Attiva la fiducia**: *Impostazioni → Generali → Info → Impostazioni certificati
   attendibili* → attiva **TeleMac CA**.
4. Torna alla pagina di configurazione, tocca **Verifica** e poi **Apri TeleMac**.
5. In Safari tocca *Condividi → Aggiungi alla schermata Home*.
6. Apri TeleMac dall'icona e tocca **Mostra il codice sul Mac**. La finestra di TeleMac
   viene in primo piano (quindi sulla TV) con un codice di 6 cifre: scrivilo sul telefono.

Fatto. Da ora in poi basta aprire l'icona: l'iPhone resta abbinato anche quando riavvii il Mac.

> Se preferisci non passare dalla pagina web per il certificato, puoi mandarlo con
> **AirDrop**: nella finestra di TeleMac tocca *Profilo del certificato → Mostra nel
> Finder* e manda all'iPhone il file **`TeleMac.mobileconfig`**. Poi prosegui dal passo 3.

## La finestra di TeleMac

- **QR** da inquadrare con l'iPhone; quando un iPhone chiede di abbinarsi, al suo
  posto compare il **codice** con il tempo che resta.
- **Accessibilità**: dice se il permesso c'è e, se manca, apre le Impostazioni giuste.
- **Apri all'accensione del Mac**: TeleMac parte da solo al login, senza aprire la
  finestra (su macOS 13 o successivi).
- **iPhone abbinati**: l'elenco e il pulsante *Scollega tutti*.
- Chiudere la finestra **non** spegne il telecomando: TeleMac resta nel Dock. Per
  spegnerlo davvero usa *TeleMac → Esci* (⌘Q).

## Come si usa

**Telecomando**, fatto come quello dell'Apple TV:

- **Puntatore**: muovi il telefono e il cursore lo segue, sia tenendolo piatto sia
  dritto. Il **pulsante d'accensione** in alto a destra lo accende e spegne (verde =
  acceso) e si ricorda lo stato. La prima volta iOS chiede il permesso al movimento:
  rispondi *Consenti*.
- **Cerchio in basso**, alla portata del pollice:
  - **centro** = clic; tenendo premuto trascini. Mentre il dito è sul centro il
    cursore si ferma, così il clic arriva dove miravi. A puntatore spento il centro
    è *OK* (Invio);
  - **frecce** = frecce della tastiera: nei video vai avanti/indietro, nelle pagine
    scorri. Si illumina solo quella che tocchi;
  - **manopola del volume**: tieni il dito fermo sull'anello per un attimo. Il cerchio
    si trasforma e compare un puntino che segue il dito. Gira come la manopola di
    una radio: in senso orario alzi, in senso antiorario abbassi.
- **Sopra il cerchio quattro tasti**: Altro, Play/Pausa, Tastiera, Clic destro.
- **Stabilizzazione**: un filtro toglie il tremolio della mano senza rallentare i
  movimenti veri. Si regola in *Altro → Stabilizzazione*.
- **Cursore grande sul Mac**: mentre usi il puntatore il cursore del Mac diventa
  più grande, così lo vedi dal divano. Quando spegni il puntatore o chiudi l'app
  torna com'era. Si regola in *Altro → Cursore* (Normale / Grande / Enorme).

**Tastiera**

Quello che scrivi arriva al Mac dove si trova il cursore. Dopo Invio torni da solo al
telecomando. Da qui hai anche Invio,
Cancella, Tab, Esc, Spotlight, Barra degli indirizzi e Cerca.

**Altro**

- Video: Indietro (Esc), Schermo intero, Muto, Precedente/Successivo.
- Cambia app, Mission Control, comandi del browser, spegni schermo.
- **Impostazioni**:
  - sensibilità del puntatore;
  - inversione degli assi e ricalibrazione del giroscopio;
  - tasto Play (multimediale o Spazio);
  - vibrazione;
  - *Dimentica questo Mac*.

In alto una pillola nera ti avvisa di quello che succede (collegato, volume, errori).
Quando l'app è aperta a schermo intero la pillola esce dalla Dynamic Island.

## Problemi comuni

| Problema | Soluzione |
|---|---|
| "Mac non raggiungibile" | TeleMac è aperto sul Mac? Mac e iPhone sono sullo stesso Wi-Fi? |
| La pagina dice che il sito non è sicuro | Manca il passo 3 del collegamento (fiducia nel certificato). |
| Il puntatore non si muove | Chiudi l'app dal multitasking, riaprila, tocca un tasto e rispondi **Consenti** quando iOS chiede l'accesso al movimento. |
| Il cursore va nella direzione sbagliata | *Altro → Impostazioni → Inverti orizzontale/verticale*. |
| Il cursore è troppo lento o veloce | *Altro → Impostazioni → Sensibilità*. |
| Il Mac non reagisce ai comandi | Manca il permesso di Accessibilità: la finestra di TeleMac lo segnala e ha il pulsante per le Impostazioni. Se dopo un aggiornamento non va più, togli TeleMac dall'elenco di Accessibilità e riattivalo. |
| "TeleMac è già acceso" | Un'altra copia (per esempio avviata dal Terminale) usa già la rete: chiudila e tocca *Riprova*. |
| Voglio scollegare tutti gli iPhone | Finestra di TeleMac → *iPhone abbinati → Scollega tutti*. |
| Serve il registro per capire un errore | `~/Library/Logs/TeleMac.log` (c'è anche il pulsante *Apri il registro*). |

## Firma

TeleMac.app è firmata con il certificato di **Edoardo Ciarlo**. Lo vedi in *TeleMac →
Informazioni su TeleMac*, oppure con `codesign -dv --verbose=2 /Applications/TeleMac.app`
(riga *Authority=Edoardo Ciarlo*).

- **Firma personale (quella attuale).** Il certificato "Edoardo Ciarlo" è creato da
  noi, non da Apple. La firma serve a due cose: dire chi ha fatto l'app e far sì che
  macOS riconosca TeleMac anche dopo un aggiornamento, così il permesso di
  Accessibilità resta valido. Gatekeeper però conosce solo i certificati rilasciati
  da Apple, quindi al primo avvio serve *Apri comunque* (una volta sola).
- **Firma riconosciuta da Apple (facoltativa).** Con il programma sviluppatori Apple
  (99 €/anno) si ottiene un certificato *Developer ID Application: Edoardo Ciarlo*.
  Basta metterlo nei secrets del repository (vedi sotto) e aggiungere quelli per la
  notarizzazione: da quel momento l'app si apre con un doppio clic, senza avvisi.

**Come far firmare l'app a GitHub** (la compila il workflow *App per Mac* su un Mac
di GitHub e pubblica il DMG nelle release):

1. Crea il certificato una volta sola: `tools/crea_certificato_firma.sh` (su Mac o
   Linux). Stampa due valori.
2. Su GitHub: *Settings → Secrets and variables → Actions → New repository secret*
   e aggiungi **`MAC_SIGN_P12`** e **`MAC_SIGN_PASSWORD`** con quei valori.
   Tieni da parte anche il file `.p12`: contiene la chiave privata, non va mai nel
   repository.
3. Con un Developer ID: metti il suo `.p12` in `MAC_SIGN_P12`, il nome completo del
   certificato in **`MAC_SIGN_IDENTITY`**, e per la notarizzazione **`APPLE_ID`**,
   **`APPLE_TEAM_ID`** e **`APPLE_APP_PASSWORD`** (una password per app creata su
   appleid.apple.com).

Senza `MAC_SIGN_P12` il workflow firma lo stesso come "Edoardo Ciarlo", ma con un
certificato nuovo a ogni compilazione: dopo ogni aggiornamento macOS chiederebbe di
nuovo il permesso di Accessibilità.

**Compilare sul proprio Mac:** `tools/build_mac_app.sh --dmg` crea `build/TeleMac.app`
e `build/TeleMac.dmg`. Se nel portachiavi non c'è ancora un certificato "Edoardo
Ciarlo", lo crea la prima volta.

## Sicurezza, in breve

- **Il certificato** è creato dal tuo Mac. È valido solo per nomi `.local` e indirizzi
  della rete di casa, quindi non può essere usato per siti internet. La chiave privata
  resta nella cartella `~/.telemac` del Mac. Per toglierlo: *Impostazioni → Generali →
  VPN e gestione dispositivi → TeleMac → Rimuovi profilo*.
- **Solo i telefoni abbinati** possono comandare il Mac. Il codice dura 2 minuti e si
  blocca dopo 5 tentativi sbagliati. Ogni blocco raddoppia l'attesa prima del codice
  successivo.
- **La pagina di configurazione** (porta 8766) è in chiaro ma non contiene segreti.
  Per il massimo della prudenza usa AirDrop per il certificato e confronta l'impronta
  con quella in *Impostazioni → Profilo → Altri dettagli*.
- **La finestra di TeleMac** è una pagina servita solo al Mac stesso (127.0.0.1),
  protetta da un gettone segreto che l'app cambia a ogni avvio: né la rete né le
  pagine web aperte nel browser possono leggere il codice di abbinamento.

## Limiti

- **Niente vera Dynamic Island** fuori dall'app: le pagine web non possono usarla. La
  pillola funziona solo con l'app aperta.
- **Vibrazione**: su iPhone usa un trucco che funziona da iOS 18 in poi.
- **Niente Bluetooth**: Safari non lo permette alle pagine web, quindi si passa dal Wi-Fi di casa.

## Sviluppo

```
python3 -m unittest discover -s tests        # test del server (anche con Python 3.9)
node --test tests/web/*.test.js              # test della matematica del puntatore
python3 telemac/server.py --dry-run          # server "a secco" nel terminale: stampa i comandi invece di eseguirli
python3 telemac/server.py --app --dry-run    # come lo avvia l'app: eventi su stdout e pannello locale
python3 tools/make_icons.py                  # rigenera le icone (iPhone e Mac)
tools/build_mac_app.sh --dmg                 # compila e firma TeleMac.app (su macOS)
```

Colori: tutto parte dal blu ciano **`#1AB8EF`** (`--accent` in `web/style.css`);
sfondi, superfici e testi sono lo stesso tono via via più scuro.

```
macos/            app per Mac
  TeleMac/main.swift  finestra, avvio del server, permessi, avvio automatico
  Info.plist      nome, versione, © Edoardo Ciarlo
  AppIcon.icns    icona (generata da tools/make_icons.py)
telemac/          server (Python, solo libreria standard)
  server.py       HTTPS (app, abbinamento, WebSocket) + HTTP (pagina di configurazione)
  panel.py        pannello della finestra per Mac (solo 127.0.0.1)
  certs.py        certificato "di casa" (CA locale) compatibile con iOS
  pairing.py      codice di abbinamento e dispositivi abbinati
  macinput.py     mouse, tastiera e tasti multimediali su macOS (CoreGraphics)
web/              pagine (HTML/CSS/JS senza dipendenze)
  index.html      app per iPhone
  pointer.js      matematica del puntatore a giroscopio
  setup.html      pagina di configurazione per l'iPhone
  panel.html      finestra dell'app per Mac
tests/            test automatici
tools/            icone, compilazione e firma dell'app per Mac
```
