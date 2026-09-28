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
iPhone (app nella schermata Home)  ──Wi-Fi di casa, HTTPS──▶  Mac (server TeleMac)  ──▶  mouse e tastiera
```

- Sul Mac gira un piccolo server in Python. Non serve installare nulla: Python c'è già su macOS.
- Sull'iPhone l'app è una pagina web aggiunta alla schermata Home. Si apre a tutto
  schermo come un'app vera: niente barre, niente zoom.
- Il collegamento è cifrato con un certificato creato dal tuo Mac. Il certificato vale
  solo per gli indirizzi della rete di casa.
- Ogni iPhone si abbina una volta sola, con un codice di 6 cifre che compare sul Mac.
  Funziona come con l'Apple TV.

## Prima configurazione (una volta sola, circa 3 minuti)

**Sul Mac**

1. Scarica il progetto: *Code → Download ZIP* e scompatta la cartella dove vuoi.
2. Fai doppio clic su **`TeleMac.command`**.
   - Se macOS dice che non può aprirlo: tasto destro sul file → *Apri* → *Apri*.
   - La prima volta macOS potrebbe proporti di installare gli "strumenti per sviluppatori"
     (è il pacchetto che contiene Python): accetta e rilancia il file.
3. macOS chiede il permesso di **Accessibilità**. Apri *Impostazioni di Sistema → Privacy e
   sicurezza → Accessibilità* e attiva **Terminale**. Senza questo permesso il Mac
   ignora i comandi.
4. Se compaiono domande su "connessioni in entrata" o "dispositivi sulla rete locale",
   rispondi **Consenti**.

Nel Terminale compare un **QR code**.

**Sull'iPhone** (sulla stessa rete Wi-Fi del Mac)

1. Inquadra il QR con la fotocamera: si apre la pagina di configurazione di TeleMac.
2. **Installa il certificato**: tocca il pulsante, poi apri *Impostazioni*. In alto trovi
   *Profilo scaricato*: tocca *Installa*.
3. **Attiva la fiducia**: *Impostazioni → Generali → Info → Impostazioni certificati
   attendibili* → attiva **TeleMac CA**.
4. Torna alla pagina di configurazione, tocca **Verifica** e poi **Apri TeleMac**.
5. In Safari tocca *Condividi → Aggiungi alla schermata Home*.
6. Apri TeleMac dall'icona e tocca **Mostra il codice sul Mac**. Sul Mac (quindi sulla TV)
   compare un codice di 6 cifre: scrivilo sul telefono.

Fatto. Da ora in poi basta aprire l'icona: l'iPhone resta abbinato anche quando riavvii il Mac.

> Se preferisci non passare dalla pagina web per il certificato, puoi mandarlo con
> **AirDrop**. Nel Finder usa *Vai → Vai alla cartella…*, scrivi `~/.telemac` e manda
> all'iPhone il file **`TeleMac.mobileconfig`**. Poi prosegui dal passo 3.

## Avvio automatico (consigliato)

Fai doppio clic su **`Installa avvio automatico.command`**. TeleMac parte da solo
all'accensione del Mac e si riavvia da solo se si chiude. Non c'è nessuna finestra del
Terminale da tenere aperta.

- macOS chiederà di nuovo il permesso di Accessibilità, questa volta per **Python**.
- Per toglierlo: **`Rimuovi avvio automatico.command`**.
- Se ti serve rivedere il QR (per esempio per un iPhone nuovo), apri `TeleMac.command`:
  se il server è già attivo mostra il QR e basta.

## Come si usa

**Telecomando**, fatto come quello dell'Apple TV:

- **Puntatore sempre attivo**: muovi il telefono e il cursore lo segue, sia tenendolo
  piatto sia dritto. Al primo tocco iOS chiede il permesso al movimento: rispondi
  *Consenti* una volta.
- **Cerchio in basso**, alla portata del pollice:
  - **centro** = clic; tenendo premuto trascini. Mentre il dito è sul centro il
    cursore si ferma, così il clic arriva dove miravi;
  - **frecce** = frecce della tastiera: nei video vai avanti/indietro, nelle pagine
    scorri. Tenendole premute si ripetono;
  - **manopola del volume**: appoggia il dito sull'anello e giralo come la manopola
    di una radio, in senso orario alzi, in senso antiorario abbassi.
- **In alto quattro tasti**: Clic destro, Play/Pausa, Tastiera, Altro.

**Tastiera**

Quello che scrivi arriva al Mac dove si trova il cursore. Da qui hai anche Invio,
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
| "Mac non raggiungibile" | Il server è acceso? Mac e iPhone sono sullo stesso Wi-Fi? Riapri `TeleMac.command` per controllare. |
| La pagina dice che il sito non è sicuro | Manca il passo 3 della configurazione (fiducia nel certificato). |
| Il puntatore non si muove | Chiudi l'app dal multitasking, riaprila, tocca un tasto e rispondi **Consenti** quando iOS chiede l'accesso al movimento. |
| Il cursore va nella direzione sbagliata | *Altro → Impostazioni → Inverti orizzontale/verticale*. |
| Il cursore è troppo lento o veloce | *Altro → Impostazioni → Sensibilità*. |
| Il Mac non reagisce ai comandi | Manca il permesso di Accessibilità (la pillola in alto te lo segnala). |
| Voglio scollegare tutti gli iPhone | Nel Terminale: `python3 telemac/server.py --forget-devices` |

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
  mostrata nel Terminale con quella in *Impostazioni → Profilo → Altri dettagli*.

## Limiti

- **Niente vera Dynamic Island** fuori dall'app: le pagine web non possono usarla. La
  pillola funziona solo con l'app aperta.
- **Vibrazione**: su iPhone usa un trucco che funziona da iOS 18 in poi.
- **Niente Bluetooth**: Safari non lo permette alle pagine web, quindi si passa dal Wi-Fi di casa.

## Sviluppo

```
python3 -m unittest discover -s tests        # test del server (anche con Python 3.9)
node --test tests/web/*.test.js              # test della matematica del puntatore
python3 telemac/server.py --dry-run          # server "a secco": stampa i comandi invece di eseguirli
python3 tools/make_icons.py                  # rigenera le icone
```

```
telemac/          server (Python, solo libreria standard)
  server.py       HTTPS (app, abbinamento, WebSocket) + HTTP (pagina di configurazione)
  certs.py        certificato "di casa" (CA locale) compatibile con iOS
  pairing.py      codice di abbinamento e dispositivi abbinati
  macinput.py     mouse, tastiera e tasti multimediali su macOS (CoreGraphics)
  autostart.py    avvio automatico (LaunchAgent)
web/              app per iPhone (HTML/CSS/JS senza dipendenze)
  pointer.js      matematica del puntatore a giroscopio
  setup.html      pagina di configurazione
tests/            test automatici
tools/            generatore delle icone
```
