# Anteprime dei messaggi Telegram

Catalogo aggiornato il 29 ottobre 2026 dai testi centralizzati in [bot/messages.py](../bot/messages.py).

I dati sono inventati; casse, tipologie e unità sono esempi, non elenchi reali. I percorsi diagnostici usano segnaposto e sono visibili solo all'amministratore. I pulsanti sono indicati tra parentesi quadre.

Per provare questi messaggi attiva prima **Modalità PROVA** da Amministrazione e controlla l'indicatore. Nell'uso normale scontrini, `/manual` e `/entrata` salvano davvero all'ultima tipologia, senza conferma aggiuntiva. `/pulisci` cancella la cronologia locale dopo conferma. `/testspesa` è stato rimosso.

## A. Avvio e aiuto

### 1 · Avvio — utente

```text
👋 Ciao! Qui tieni in ordine le spese.

📷 Mandami una foto o un PDF dello scontrino, oppure scegli cosa fare con i pulsanti qui sotto.

✅ Modalità REALE: scegliendo l’ultima tipologia salvo nel portale, senza altre conferme.
```

```text
[📷 Nuovo scontrino]
[📊 Riepilogo] [📋 Ultime operazioni]
[⏳ Operazioni in attesa]
[❓ Aiuto] [✖️ Annulla operazione]
```

I pulsanti sono sotto il messaggio. All'avvio la vecchia tastiera con i comandi slash viene rimossa; i comandi scritti restano disponibili come alternativa.

### 2 · Avvio — amministratore

La modalità è indicata per tutti. Se l'amministratore ha attivato la prova, l'indicatore diventa:

```text
🧪 Modalità PROVA: non viene salvato nulla nel portale.

```

```text
[📷 Nuovo scontrino]
[📊 Riepilogo] [📋 Ultime operazioni]
[⏳ Operazioni in attesa]
[❓ Aiuto] [✖️ Annulla operazione]
[⚙️ Amministrazione]
```

La schermata Amministrazione contiene:

```text
[✍️ Inserisci a mano] [💰 Nuova entrata]
[🧪 Attiva modalità prova]
[🔌 Verifica collegamento]
[🔄 Aggiorna casse e tipologie]
[🔖 Versione e aggiornamenti]
[🧹 Pulisci cronologia]
[🏠 Menu principale]
```

Versione attiva e disponibile vengono mostrate nella schermata **Versione e aggiornamenti**, con **Installa aggiornamento**, **Indietro** e **Menu principale**. Il pulsante di installazione apre la conferma, non installa subito. La pulizia mantiene la propria conferma.

In prova il primo pulsante diventa **✅ Torna ai salvataggi reali**. La scelta resta nel database anche dopo il riavvio e vale per tutti i nuovi inserimenti; quelli già iniziati mantengono la loro modalità.

### 3 · Aiuto

```text
❓ Come funziona

📷 Nuovo scontrino: invia una foto o un PDF, controlla i dati e scegli cassa e tipologia. Se non riesco a leggerlo, ti chiedo i dati a mano.

L’ultima tipologia avvia subito la registrazione, senza altre conferme. Solo in modalità PROVA non salvo nel portale.

📊 Riepilogo: consulta i saldi delle casse.
📋 Ultime operazioni: controlla le tue registrazioni.
⏳ Operazioni in attesa: consulta gli elementi da riprovare o verificare.

Aprire il menu non annulla un inserimento in corso. Per interromperlo premi «Annulla operazione».
```

Per l'amministratore si aggiunge:

```text
In ⚙️ Amministrazione trovi:
✍️ Inserisci a mano: registra un’entrata o un’uscita senza foto.
💰 Nuova entrata: registra un’entrata senza foto.
Scegliendo l’ultima tipologia registri subito, senza altre conferme.
🧪 Lì puoi anche attivare o disattivare la modalità prova.
```

Le schermate hanno **Menu principale**; gli strumenti amministratore hanno anche **Indietro**. Il ritorno al menu non cancella un inserimento. I moduli conservano correzione e annullamento, non conferme aggiuntive. Le schermate informative sono accessibili durante le attese dell'AI o del portale; salvataggi e altre azioni che cambiano dati restano sequenziali.

## B. Inserimento da scontrino

### 4 · Ricezione

```text
📷 Ricevuto! Lo leggo subito…
```

Se l'analisi dura più di 30 secondi, lo stesso messaggio viene aggiornato ogni 30 secondi con la fase in corso, ad esempio:

```text
⏳ Analisi in corso (30 secondi).
Fase 2 di 4: Ollama sta caricando il modello in memoria. La prima analisi dopo una pausa è la più lenta.
```

```text
⏳ Analisi in corso (120 secondi).
Fase 3 di 4: il modello è pronto e sta leggendo l’immagine. È la fase più lunga e non mostra avanzamenti finché non inizia a scrivere.
```

Quando il modello inizia a scrivere:

```text
⏳ Analisi in corso (300 secondi).
Fase 4 di 4: il modello sta scrivendo i dati (180 caratteri finora). Aspetto la risposta completa prima di usarla.
```

La fase 1 («preparo l’immagine per il modello») di solito dura pochi secondi. Se la fase non è ancora nota compare il messaggio generico «Sto ancora analizzando la ricevuta…».

Alla fine dell'attesa il messaggio viene sostituito da:

```text
📷 Attesa dell’analisi terminata. Trovi il risultato o le istruzioni nel messaggio successivo.
```

I frammenti non vengono mostrati né usati per registrare operazioni. Il limite predefinito è 10 minuti; eventuali valori espliciti già configurati restano validi. Dopo un timeout o una risposta interrotta compare il recupero manuale.

### 5 · Controllo dei dati

```text
Ecco i dati dell’operazione. Puoi correggerli prima di scegliere la cassa.

📅 29/10/2026
🏪 Negozio esempio
💶 12,50 €

✅ Modalità REALE: scegliendo l’ultima tipologia salvo nel portale, senza altre conferme.
```

Solo sotto il 70% si aggiunge questo avviso, senza percentuali:

```text
⚠️ Non sono sicuro di aver letto bene: controlla con attenzione.
```

### 6 · Dati mancanti o modifica

Lettura fallita, una richiesta per messaggio:

```text
Non sono riuscito a leggere la data. Me la scrivi come giorno-mese-anno? (es. 29-10-2026 o 29/10/26)
```

```text
Non sono riuscito a leggere il negozio. Come si chiama?
```

```text
Non sono riuscito a leggere il totale. Quanto hai speso? (es. 12,50)
```

Modifica scelta dall'utente, una richiesta per messaggio:

```text
Qual è la data giusta? Scrivila come giorno-mese-anno (es. 29-10-2026 o 29/10/26)
```

Le date sono sempre in forma italiana giorno-mese-anno. Separatori ammessi: `-`, `/`, `.` o spazio; giorno e mese anche con una cifra (`2-3-26`), anno con due o quattro cifre (`26` = `2026`).

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
🏦 Quale cassa vuoi usare? (1 di 3)

✅ Modalità REALE: scegliendo l’ultima tipologia salvo nel portale, senza altre conferme.
```

```text
[Cassa esempio A]
[Cassa esempio B]
[📅 Data] [💶 Importo] [✏️ Descrizione]
[🏷️ Categoria]
[Annulla]
[🏠 Menu principale]
```

### 8 · Entrata o uscita

Questo passaggio compare solo con `/manual`. `/entrata` è già impostato su Entrata; una foto o un PDF è già impostato su Uscita.

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

### 13 · Inserimento manuale: Amministrazione → «Inserisci a mano» / «Nuova entrata»

I due pulsanti sono nella schermata ⚙️ Amministrazione (restano disponibili anche `/manual` ed `/entrata`). Si apre un modulo unico: ogni dato scritto viene tolto dalla chat e il modulo si ripresenta aggiornato in fondo, con i pulsanti sempre visibili.

```text
💰 Nuova entrata a mano

Scrivi qui sotto il dato richiesto: il modulo si aggiorna e i pulsanti restano in fondo. Per cambiare un campo premi il suo pulsante.

📅 Data: da inserire
✏️ Descrizione: da inserire
💶 Importo: da inserire

👉 📅 Scrivi la data come giorno-mese-anno, ad esempio 29-10-2026, 29/10/26 o 2-3-26.

Poi sceglierai cassa e tipologia: l’ultima scelta registra subito, senza altre conferme.

✅ Modalità REALE: scegliendo l’ultima tipologia salvo nel portale, senza altre conferme.
```

```text
[📅 Oggi]
[📅 Data] [✏️ Descrizione] [💶 Importo]
[Annulla]
[🏠 Menu principale]
```

«Inserisci a mano» usa il titolo `✍️ Nuova operazione a mano (entrata o uscita)`. **📅 Oggi** compare solo quando è richiesta la data. Dopo la data:

```text
📅 Data: 02/03/2026
✏️ Descrizione: da inserire
💶 Importo: da inserire

👉 ✏️ Scrivi la descrizione dell’operazione.
```

Se il valore non è valido, il modulo mostra `⚠️ Non ho capito il valore scritto. Riprova.` e ripete la richiesta. Le altre richieste sono `💶 Scrivi l’importo (es. 12,50).`

Dopo aver compilato i dati:

```text
Ecco i dati dell’operazione. Puoi correggerli prima di scegliere la cassa.

📅 29/10/2026
📝 Prova Telegram
💶 0,10 €

✅ Modalità REALE: scegliendo l’ultima tipologia salvo nel portale, senza altre conferme.
```

Sticker, audio, video e file diversi da foto o PDF vengono eliminati e il bot risponde:

```text
Sticker, audio, video e altri file non sono supportati e non li conservo. Usa i pulsanti, scrivi il dato richiesto oppure invia una foto o un PDF della ricevuta.
```

### 14 · Ultima scelta manuale

Non compare una conferma finale. Scegliendo la tipologia si passa direttamente al messaggio di salvataggio o prova in corso e poi all'esito verificato.

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

Premi «Riprova operazioni» per riprovare quelle in attesa.
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
⚠️ Una spesa non è stata salvata dopo vari tentativi. Controlla «Operazioni in attesa» nel menu.
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
ℹ️ Il controllo automatico non è attivo sul NAS: la richiesta non verrebbe eseguita. Aggiorna con il comando manuale.
⏳ C'è già una richiesta in attesa: il NAS la esegue al prossimo controllo.
```

Notifiche inviate automaticamente all'amministratore, una sola volta per evento:

```text
🆕 È disponibile una nuova versione del bot: e4f5a6b - Nuovo titolo (29/10/2026 14:00)
Premi «Installa aggiornamento» oppure usa /aggiorna. Ti chiederò conferma prima di installare.
```

```text
[🔄 Installa aggiornamento]
[🔖 Versione e aggiornamenti]
```

Il NAS controlla Git ogni 10 minuti con l'attività DSM in modalità `controlla`; il bot legge lo stato ogni minuto. Il messaggio raggiunge solo l'amministratore. La stessa versione non viene annunciata di nuovo dopo i controlli successivi o il riavvio; se Telegram fallisce, l'invio viene ritentato. Un controllo Git fallito non annuncia versioni dalla vecchia cache. Il pulsante apre la conferma: non installa subito.

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
Stiamo già lavorando su uno scontrino: completalo oppure premi «Annulla operazione» nel menu.
```

```text
Prima finiamo lo scontrino in corso, oppure premi «Annulla operazione» nel menu.
```

```text
Avevi una prova rimasta aperta: l'ho chiusa.
```

```text
Usa i pulsanti qui sopra, oppure premi «Annulla operazione» nel menu.
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
⚠️ Non sono riuscito a leggere lo scontrino con l’AI locale. Ho conservato il file temporaneo, senza inviarlo a servizi AI online. Inseriamo i dati a mano: data, descrizione e importo, poi cassa e tipologia. La ricevuta sarà archiviata quando confermi l’operazione; Annulla elimina la copia temporanea.

Non sono riuscito a leggere la data. Me la scrivi come giorno-mese-anno? (es. 29-10-2026 o 29/10/26)
```

Questo è un unico messaggio al mittente, anche se è l'amministratore. Non viene inviata una seconda notifica per lo stesso errore AI. Il file resta disponibile per la compilazione manuale anche se il modello non è configurato o l'analisi va in timeout. File e stato manuale sono conservati prima dell'analisi.

Solo se il mittente è amministratore, lo stesso messaggio aggiunge una diagnosi senza dati della ricevuta, per esempio:

```text
🔧 Diagnosi AI — solo amministratore
Codice: memory — HTTP 500
Timeout configurato: 180 secondi.
Ollama ha segnalato memoria insufficiente. Controlla la RAM libera del NAS e gli altri servizi in esecuzione.
```

Per l'amministratore, la domanda sulla data viene mostrata dopo la diagnosi, alla fine dello stesso messaggio.

Se il mittente è un altro utente autorizzato, non vede questa parte: il bot la invia solo all'amministratore, senza contenuti, nomi di file o identificativi della ricevuta. I motivi sono frasi controllate dal programma, non risposte grezze del modello.

Per un'uscita senza ricevuta fuori dall'inserimento manuale dell'amministratore, anche da un vecchio pulsante o dalla coda:

```text
⚠️ Per registrare un’uscita serve una ricevuta allegata. Non ho registrato nulla. Premi «Annulla operazione» nel menu e invia una foto o un PDF: se non riesco a leggerlo, puoi compilare i dati a mano.
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
Non riesco a leggere le casse dal portale. Non ho inserito nulla e ho conservato i dati: premi «Va bene» per riprovare oppure Annulla.
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
⚠️ Registrazione non verificata. Controlla <cartella> e «Operazioni in attesa» nel menu.
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

👉 Controlla nel portale se compare. Se non c'è, riparti da ⚙️ Amministrazione con «Inserisci a mano» o «Nuova entrata».
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
