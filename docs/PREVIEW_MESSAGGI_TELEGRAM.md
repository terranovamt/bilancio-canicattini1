# Anteprime dei messaggi Telegram

Catalogo aggiornato il 29 ottobre 2026 dai testi centralizzati in [bot/messages.py](../bot/messages.py).

I dati sono inventati; casse, tipologie e unità sono esempi, non elenchi reali. I percorsi diagnostici usano segnaposto e sono visibili solo all'amministratore. I pulsanti sono indicati tra parentesi quadre.

Non eseguire `/testspesa` o `/pulisci` su dati reali per provare questi messaggi: `/testspesa` salva davvero dopo conferma anche con `DRY_RUN=true`; `/pulisci` cancella la cronologia locale dopo conferma.

## A. Avvio e aiuto

### 1 · `/start` — utente

```text
👋 Ciao! Qui tieni in ordine le spese.

📷 Mandami la foto o il PDF di uno scontrino: lo leggo io e ti faccio controllare i dati prima di salvare.

Cosa puoi fare:
• /ultime – le ultime spese
• /riepilogo – saldi delle casse
• /help – tutti i comandi
```

```text
[/ultime] [/riepilogo]
[/coda] [/help]
[/annulla]
```

### 2 · `/start` — amministratore

```text
👋 Ciao! Sei collegato come amministratore.

📷 Mandami uno scontrino per registrare una spesa.
✍️ /entrata – inserisci un’entrata senza ricevuta
📊 /riepilogo – saldi delle casse

🧪 Modalità PROVA: non viene salvato nulla nel portale.
/entrata e /testspesa salvano davvero, anche in modalità prova, dopo la tua conferma. Le uscite richiedono una ricevuta.

🔖 Versione attiva: a1b2c3d del 29/10/2026 13:00 – Titolo del commit
🆕 Nuova versione disponibile: e4f5a6b - Nuovo titolo (29/10/2026 14:00)
Per installarla: /aggiorna

Altri strumenti: /help
```

La parte sulla versione cambia secondo lo stato rilevato dal NAS:

```text
✅ È l'ultima versione (controllo del 29/10/2026 14:10).
ℹ️ Il controllo automatico degli aggiornamenti non è ancora attivo.
⚠️ L'ultimo controllo degli aggiornamenti non è riuscito (29/10/2026 14:10).
⚠️ Ultimo aggiornamento non riuscito (e4f5a6b - Nuovo titolo …): la nuova versione non è partita, ho ripristinato quella precedente.
⏳ Installazione richiesta: parte al prossimo controllo.
🔖 Versione attiva: non indicata (installata senza Git).
```

In modalità reale la riga della modalità diventa:

```text
⚠️ Modalità REALE: le spese confermate vengono salvate nel portale.
```

```text
[/entrata] [/testbilancio]
[/ultime] [/riepilogo]
[/coda] [/help]
[/annulla]
```

`/aggiornaopzioni`, `/aggiorna` e `/pulisci` restano disponibili con `/help` e nei suggerimenti dei comandi, non nella tastiera rapida.

### 3 · `/help`

```text
📋 Cosa posso fare per te

📷 Scontrino – mandami una foto o un PDF
/ultime – le ultime 10 spese
/riepilogo – saldi e andamento delle casse
/coda – spese in attesa o da controllare
/riprova – riprovo le spese in attesa
/annulla – interrompo quello che stiamo facendo
```

Per l'amministratore si aggiunge:

```text
🔧 Solo amministratore
/entrata – inserisci un’entrata senza ricevuta
/testspesa – alias di /entrata
/testbilancio – verifico il collegamento al portale
/aggiornaopzioni – aggiorno casse e tipologie
/aggiorna – installo l'ultima versione del bot
/pulisci – svuoto coda, ultime spese e registri
```

## B. Inserimento da scontrino

### 4 · Ricezione

```text
📷 Ricevuto! Lo leggo subito…
```

### 5 · Controllo dei dati

```text
Ecco cosa ho letto, è tutto giusto?

📅 29/10/2026
🏪 Negozio esempio
💶 12,50 €
🏷️ Alimentari
```

```text
[✅ Sì, è giusto]
[📅 Data] [💶 Importo]
[🏪 Negozio] [🏷️ Categoria]
[Annulla]
```

Solo sotto il 70% si aggiunge questo avviso, senza percentuali:

```text
⚠️ Non sono sicuro di aver letto bene: controlla con attenzione.
```

### 6 · Dati mancanti o modifica

Lettura fallita, una richiesta per messaggio:

```text
Non sono riuscito a leggere la data. Me la scrivi? (es. 29/10/2026)
```

```text
Non sono riuscito a leggere il negozio. Come si chiama?
```

```text
Non sono riuscito a leggere il totale. Quanto hai speso? (es. 12,50)
```

Modifica scelta dall'utente, una richiesta per messaggio:

```text
Qual è la data giusta? (es. 29/10/2026)
```

```text
Come si chiama il negozio?
```

```text
Qual è l'importo giusto? (es. 12,50)
```

```text
Che categoria vuoi? Scrivi "-" per lasciarla vuota.
```

### 7 · Scelta della cassa

```text
⏳ Cerco le casse…
```

```text
🏦 Da quale cassa è uscita la spesa? (1 di 3)
```

```text
[Cassa esempio A]
[Cassa esempio B]
```

### 8 · Entrata o uscita

```text
↔️ È un'entrata o un'uscita? (2 di 3)
```

```text
[⬇️ Entrata] [⬆️ Uscita]
```

### 9 · Tipologia

```text
⏳ Carico le tipologie, può servire fino a un minuto. Aspetta senza premere altro 🙏
```

```text
🏷️ Che tipo di spesa è? (3 di 3)
Ti mostro le più usate.
```

```text
[Tipologia esempio A]
[Tipologia esempio B]
[➕ Mostra altre (8)]
```

Con tutte le voci subito disponibili:

```text
🏷️ Che tipo di spesa è? (3 di 3)
```

Dopo «Mostra altre»:

```text
🏷️ Altre tipologie
```

### 10 · Salvataggio in corso

```text
⏳ Salvo nel portale e controllo che sia andato tutto bene… Un attimo, non premere altro.
```

```text
🧪 Faccio la prova nel portale (non salvo nulla)… Un attimo, non premere altro.
```

### 11 · Spesa salvata

```text
✅ Fatto! Spesa salvata e verificata nel portale.

📅 29/10/2026
🏪 Negozio esempio
💶 12,50 € (uscita)
🏦 Cassa esempio
🏷️ Tipologia esempio
👥 Unità esempio
```

I campi vuoti non compaiono. La riga 👥 manca anche quando il portale non offre unità per l'anno. Non vengono mostrate voci contabili, percorsi o informazioni sull'archivio.

### 12 · Prova riuscita o già presente

```text
🧪 Prova riuscita! Nel portale non è stato salvato nulla.

📅 29/10/2026
🏪 Negozio esempio
💶 12,50 € (uscita)
🏦 Cassa esempio
🏷️ Tipologia esempio
👥 Unità esempio
```

```text
ℹ️ Questa spesa era già nel portale, non l'ho inserita due volte.

📅 29/10/2026
🏪 Negozio esempio
💶 12,50 € (uscita)
🏦 Cassa esempio
🏷️ Tipologia esempio
👥 Unità esempio
```

## C. Inserimento manuale

### 13 · Entrate manuali: `/entrata` e `/testspesa`

`/entrata` chiede tutti i dati, senza precompilare importi o descrizioni:

```text
✍️ Inseriamo un’entrata senza ricevuta: data, descrizione e importo, poi cassa e tipologia. Dopo la conferma finale sarà salvata davvero nel portale, anche in modalità prova. Puoi interrompere con /annulla.

📅 Qual è la data? (es. 29/10/2026)
```

`/testspesa` mantiene i valori iniziali modificabili. Entrambi permettono solo entrate senza ricevuta:

```text
✍️ Entrata a mano, senza ricevuta

📅 29/10/2026
📝 Prova Telegram
💶 0,10 €

Va bene così o vuoi cambiare qualcosa?
⚠️ Verrà salvata davvero nel portale: prima ti chiedo un'ultima conferma.
```

```text
[✅ Va bene]
[📅 Data] [💶 Importo]
[✏️ Descrizione]
[Annulla]
```

```text
📅 Qual è la data? (es. 29/10/2026)
```

```text
✏️ Come descrivo la spesa?
```

```text
💶 Qual è l'importo? (es. 12,50)
```

### 14 · Conferma finale manuale

```text
Ultimo controllo prima di salvare:

📅 29/10/2026
📝 Prova Telegram
💶 0,10 € (uscita)
🏦 Cassa esempio
🏷️ Tipologia esempio
👥 Unità esempio

⚠️ Se confermi, la spesa viene salvata DAVVERO nel portale.
```

```text
[✅ Salva davvero] [Annulla]
```

### 15 · Inserimento manuale completato

```text
✅ Fatto! Spesa salvata e verificata nel portale.

📅 29/10/2026
📝 Prova Telegram
💶 0,10 € (uscita)
🏦 Cassa esempio
🏷️ Tipologia esempio
👥 Unità esempio
```

## D. Consultazione

### 16 · `/ultime`

```text
🧾 Le tue ultime spese

✅ 29/10 · Negozio esempio · 12,50 € · Cassa esempio
🧪 29/10 · Negozio esempio · 12,50 € · Cassa esempio · (prova)
⏳ 29/10 · Negozio esempio · 12,50 € · Cassa esempio · in attesa di essere salvata
⚠️ 29/10 · Negozio esempio · 12,50 € · Cassa esempio · da controllare
⏳ 29/10 · Negozio esempio · 12,50 € · Cassa esempio · in corso
```

Se non ci sono spese:

```text
Ancora nessuna spesa. Mandami uno scontrino per iniziare 📷
```

### 17 · `/riepilogo`

```text
⏳ Leggo i saldi dal portale (circa un minuto). Non modifico nulla.
```

```text
📊 Bilancio 26-27
Dati aggiornati adesso dal portale.

🏦 Cassa esempio
💰 Saldo: 487,50 €
Entrate 0,00 € · uscite 12,50 €
Ultima operazione: 29/10 Negozio esempio -12,50 €
```

```text
[⬅️ Anno 25-26]
[🔄 Aggiorna]
[📈 Dettagli]
```

«Dettagli» rilegge l'anno selezionato in sola lettura e mostra anche saldo iniziale, andamento mensile e ultime tre operazioni:

```text
📊 Bilancio 26-27
Dati aggiornati adesso dal portale.

🏦 Cassa esempio
💰 Saldo: 487,50 €
Entrate 0,00 € · uscite 12,50 €
Saldo iniziale: 500,00 €
📈 Andamento mensile
• Ottobre 2026: entrate 0,00 € · uscite 12,50 € → saldo 487,50 €
🧾 Ultime operazioni
• 29/10 Negozio esempio: -12,50 €
```

Negli anni precedenti compare anche il pulsante per l'anno successivo. «Aggiorna» torna alla vista compatta.

```text
⚠️ Il portale mostra solo 20 righe su 45: i dati sono parziali.
```

```text
Non trovo casse nel portale.
```

```text
❌ Non riesco a leggere i saldi adesso. Riprova tra poco.
```

```text
⏳ Lo sto già preparando, un attimo!
```

### 18 · `/coda`

```text
⏳ Spese ancora da salvare o da controllare

29/10 · Negozio esempio · 12,50 € · in attesa
29/10 · Negozio esempio · 12,50 € · ⚠️ da controllare

Scrivi /riprova per riprovare quelle in attesa.
```

```text
Tutto a posto: non ci sono spese in attesa ✅
```

### 19 · `/riprova` e notifiche

```text
Ho riprovato: 1 salvata ✅, 1 ancora in attesa ⏳, 0 da controllare ⚠️.
```

```text
Ho riprovato: 0 salvate ✅, 1 in prova 🧪 (nel portale non è stato salvato nulla per queste prove), 0 ancora in attesa ⏳, 0 da controllare ⚠️.
```

```text
Non c'è niente da riprovare.
```

```text
✅ Salvata: Negozio esempio, 12,50 € (29/10).
```

```text
🧪 Nuovo tentativo in prova: nel portale non è stato salvato nulla.
```

```text
⚠️ Non sono riuscito a salvare «Negozio esempio, 12,50 €». L'amministratore la controllerà: non devi fare altro.
```

Solo all'amministratore:

```text
⚠️ Una spesa non è stata salvata dopo vari tentativi. Controlla con /coda.
```

## E. Comandi amministratore

### 20 · `/testbilancio`

```text
✅ Il portale risponde correttamente. Non ho inserito nulla.
```

```text
⚠️ Il portale chiede una verifica manuale (es. un codice). Non ho inserito nulla.
Dettagli: <cartella> (<file>)
```

```text
❌ Non riesco ad accedere al portale. Non ho inserito nulla.
Dettagli: <cartella>
```

### 21 · `/aggiornaopzioni`

```text
🔄 Aggiorno casse e tipologie in sottofondo, ti scrivo quando ho finito. Non inserisco nessuna spesa.
```

```text
✅ Fatto! Trovate 2 casse.
• Cassa esempio A: 10 entrate, 15 uscite
• Cassa esempio B: 8 entrate, 12 uscite
```

```text
Alcune tipologie non si sono caricate: le rileggo quando servono.
```

```text
❌ Non riesco a leggere le casse dal portale. Riprova più tardi.
```

Un conteggio non letto compare come «da rileggere», non come zero.

### 22 · `/pulisci`

```text
🧹 Vuoi svuotare coda, ultime spese e registri del bot?
Il portale e le ricevute archiviate non vengono toccati.
```

```text
[Sì, pulisci] [No, lascia stare]
```

```text
🧹 Fatto! Eliminate 12 spese (2 in attesa) e 5 file temporanei. Portale e ricevute intatti.
```

```text
Ok, non ho toccato niente.
```

### 22 bis · `/aggiorna` e notifiche di aggiornamento

```text
🔄 Vuoi installare l'ultima versione?

🔖 Versione attiva: a1b2c3d – Titolo del commit
🆕 Nuova versione disponibile: e4f5a6b - Nuovo titolo (29/10/2026 14:00)
Per installarla: /aggiorna

Il bot si riavvia per circa un minuto: controlla che nessuno stia registrando una spesa.
```

```text
[Sì, installa] [No, lascia stare]
```

```text
⏳ Richiesta registrata. Il NAS la esegue al prossimo controllo, di solito entro 10 minuti, e ti scrivo l'esito.
```

Se il comando non può procedere:

```text
⚠️ Il bot è in modalità REALE: l'aggiornamento automatico è bloccato. Riporta prima DRY_RUN=true.
ℹ️ Il controllo automatico non è attivo sul NAS: la richiesta non verrebbe eseguita. Aggiorna con il comando manuale.
⏳ C'è già una richiesta in attesa: il NAS la esegue al prossimo controllo.
```

Notifiche inviate automaticamente all'amministratore, una sola volta per evento:

```text
🆕 È disponibile una nuova versione del bot: e4f5a6b - Nuovo titolo (29/10/2026 14:00)
Per installarla: /aggiorna
```

```text
✅ Aggiornamento installato: e4f5a6b - Nuovo titolo (29/10/2026 14:00)
```

```text
⚠️ Aggiornamento non riuscito (e4f5a6b - Nuovo titolo …): la nuova versione non è partita, ho ripristinato quella precedente.
❌ Aggiornamento non riuscito (e4f5a6b - Nuovo titolo …): la costruzione è fallita, è rimasta attiva la versione precedente.
ℹ️ Nessun aggiornamento: è già installata l'ultima versione.
```

## F. Annullamenti, avvisi ed errori

### 23 · Annullamento e operazioni in corso

```text
Ok, ho annullato. Quando vuoi, mandami un altro scontrino.
```

```text
Non c'è niente da annullare.
```

```text
Stiamo già lavorando su uno scontrino: completalo oppure scrivi /annulla.
```

```text
Prima finiamo lo scontrino in corso, oppure scrivi /annulla.
```

```text
Avevi una prova rimasta aperta: l'ho chiusa.
```

```text
Usa i pulsanti qui sopra, oppure scrivi /annulla.
```

### 24 · File e analisi

```text
Mandami una foto o un PDF dello scontrino 📷
```

```text
Il file è troppo grande (massimo 20 MB). Ne provi uno più leggero?
```

```text
Non riesco ad aprire questo file. Controlla che sia una foto o un PDF e riprova.
```

```text
Questo scontrino l'ho già ricevuto, non lo rielaboro.
```

```text
⚠️ Non sono riuscito a leggere lo scontrino con l’AI locale. Ho conservato il file temporaneo, senza inviarlo a servizi AI online. Inseriamo i dati a mano: data, descrizione e importo, poi cassa e tipologia. La ricevuta sarà archiviata quando confermi l’operazione; /annulla elimina la copia temporanea.

Non sono riuscito a leggere la data. Me la scrivi? (es. 29/10/2026)
```

Questo è un unico messaggio al mittente, anche se è l'amministratore. Non viene inviata una seconda notifica per lo stesso errore AI. Il file resta disponibile per la compilazione manuale anche se il modello non è configurato.

Solo se il mittente è amministratore, lo stesso messaggio aggiunge una diagnosi senza dati della ricevuta, per esempio:

```text
🔧 Diagnosi AI — solo amministratore
Codice: memory — HTTP 500
Timeout configurato: 180 secondi.
Ollama ha segnalato memoria insufficiente. Controlla la RAM libera del NAS e gli altri servizi in esecuzione.
```

Se il mittente è un altro utente autorizzato, non vede questa parte: il bot la invia solo all'amministratore, senza contenuti, nomi di file o identificativi della ricevuta. I motivi sono frasi controllate dal programma, non risposte grezze del modello.

Per un'uscita senza ricevuta, anche da un vecchio pulsante o dalla coda:

```text
⚠️ Per registrare un’uscita serve una ricevuta allegata. Non ho registrato nulla. Usa /annulla e invia una foto o un PDF: se non riesco a leggerlo, puoi compilare i dati a mano.
```

### 25 · Pulsanti non più validi e formato manuale

```text
Questo pulsante non è più attivo. Se vuoi, mandami di nuovo lo scontrino.
```

```text
⏳ Sto ancora lavorando, un attimo!
```

```text
Prima scegli entrata o uscita 🙂
```

```text
Non ho capito 🙂 Scrivi data, descrizione e importo separati da «|», per esempio: 26/10/2026 | Pranzo | 12,50
```

### 26 · Permessi e collegamento

```text
Questo comando è riservato all'amministratore.
```

```text
Il collegamento al portale non è ancora attivo. Non ho salvato nulla.
```

```text
Il collegamento al portale non è ancora attivo. Non ho salvato nulla: lo scontrino resta in attesa.
```

Agli utenti non autorizzati non viene inviata alcuna risposta.

### 27 · Errore leggendo casse o tipologie

```text
Non riesco a leggere le casse dal portale adesso. Non ho salvato nulla e i dati dello scontrino restano qui: premi «Sì, è giusto» per riprovare oppure Annulla.
```

```text
Non riesco a leggere le casse dal portale. Ho annullato la prova, non è stato inserito nulla. Riprova più tardi con /testspesa.
```

Per le tipologie si usa «le tipologie» al posto di «le casse». Solo all'amministratore:

```text
Non riesco a leggere le casse. Dettagli: <cartella> (<file>)
```

### 28 · Registrazione non verificata

```text
⚠️ Non sono sicuro che la spesa sia stata salvata. L'ho messa in attesa e riproverò io: non devi rifare nulla.
```

```text
⚠️ Non sono riuscito a completare l'operazione. Lo scontrino è al sicuro: avvisa l'amministratore.
```

Solo all'amministratore:

```text
⚠️ Registrazione non verificata. Controlla <cartella> e /coda.
```

### 29 · Errore inserimento manuale

```text
❌ Non sono riuscito a salvare la spesa e non riproverò da solo.

📅 29/10/2026
📝 Prova Telegram
💶 0,10 € (uscita)
🏦 Cassa esempio
🏷️ Tipologia esempio
👥 Unità esempio

👉 Controlla nel portale se compare. Se non c'è, ripeti /testspesa.
```

In un messaggio separato, solo all'amministratore:

```text
Dettagli: <cartella>
```

### 30 · Sicurezza ed errore generale

```text
⚠️ Il portale non ha confermato che fosse una prova, quindi mi sono fermato per sicurezza. Avvisa l'amministratore e controlla il portale prima di riprovare.
```

Solo all'amministratore:

```text
⚠️ La modalità prova non è stata rispettata. Controlla il portale prima di riprovare.
```

```text
⚠️ Errore imprevisto durante l'elaborazione di un messaggio.
```
