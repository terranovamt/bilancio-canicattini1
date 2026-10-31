# Anteprime dei messaggi Telegram

Catalogo: menu e inserimento aggiornati il 31 ottobre 2026 (TASK 1) dai testi centralizzati in [bot/messages.py](../bot/messages.py); conferma unica dei dati letti e riepilogo finale con «Accetta»/«Modifica» aggiornati lo stesso giorno, superando il salvataggio immediato dopo la nota. Le anteprime dell'analisi AI restano storiche e non fanno parte di questo intervento.

I dati sono inventati; casse, tipologie e unità sono esempi, non elenchi reali. I percorsi diagnostici usano segnaposto e sono visibili solo all'amministratore. I pulsanti sono indicati tra parentesi quadre.

Per provare questi messaggi attiva prima **Modalità PROVA** da Amministrazione e controlla l'indicatore. Nell'uso normale scontrini, `/manual` e `/entrata` salvano davvero solo quando premi **✅ Accetta** nel riepilogo finale. `/pulisci` cancella la cronologia locale dopo conferma. `/testspesa` è stato rimosso.

## A. Avvio e aiuto

### 1 · Avvio — utente

```text
👋 Ciao! Qui tieni in ordine le spese.

📷 Mandami una foto o un PDF dello scontrino, oppure scegli cosa fare con i pulsanti qui sotto.
```

Menu fisso in basso, al posto della tastiera del telefono:

```text
[📷 Nuovo scontrino] [💰 Nuova entrata]
[📊 Riepilogo] [📋 Ultime operazioni]
[⏳ Operazioni in attesa] [❓ Aiuto]
```

Il menu resta visibile durante tutta la conversazione; il campo del messaggio mostra `Scegli dal menu o invia una foto`. Un pulsante premuto compare in chat come messaggio dell'utente e la risposta arriva in un nuovo messaggio. I comandi scritti restano disponibili come alternativa. Dopo un aggiornamento serve un `/start` per ricevere il menu nuovo.

### 2 · Avvio — amministratore

La modalità è indicata per tutti. Se l'amministratore ha attivato la prova, l'indicatore diventa:

```text
🧪 Modalità PROVA: non viene salvato nulla nel portale.

```

```text
[📷 Nuovo scontrino] [💰 Nuova entrata]
[📊 Riepilogo] [📋 Ultime operazioni]
[⏳ Operazioni in attesa] [❓ Aiuto]
[⚙️ Amministrazione]
```

**⚙️ Amministrazione** sostituisce la tastiera persistente in basso con gli strumenti amministratore:

**✖️ Annulla operazione** appare nel menu principale solo durante un inserimento attivo (nella stessa riga di Amministrazione per l'admin). Sparisce alla conclusione o all'annullamento. Il menu rilegge lo stato anche con `/start` e dopo un riavvio; le operazioni già in coda non fanno comparire questo pulsante. I pulsanti «Annulla» nel riepilogo attivo e `/annulla` restano disponibili.

La schermata Amministrazione contiene:

```text
[✍️ Inserisci a mano]
[🧪 Attiva modalità prova]
[🔌 Verifica collegamento]
[🔄 Aggiorna casse e tipologie]
[🔖 Versione e aggiornamenti]
[📊 Stato API]
[🧹 Pulisci cronologia]
[🏠 Menu principale]
```

Versione attiva e disponibile vengono mostrate nella schermata **Versione e aggiornamenti**, con **Installa aggiornamento** e **Indietro**. Il pulsante di installazione apre la conferma, non installa subito. La pulizia mantiene la propria conferma.

**Stato API** (oppure `/apistatus`, solo amministratore) legge i contatori locali senza chiamare Google. Esempio inventato per un modello:

```text
gemini-2.5-flash-lite
Minuto: 3/30 (10.0%)
Giorno: 120/1500 (8.0%)
Disponibile secondo il contatore locale; disponibilità Google non verificata.
```

La testata mostra data e ora della lettura, soglia di avviso e prossimo giorno del contatore Pacifico nell'ora locale del bot. I blocchi indicano l'attesa locale o quella suggerita da Google; se Google non comunica il reset, la sospensione prudenziale del bot è distinta da un reset remoto confermato. Il consumo riguarda solo il bot, non AI Studio o altri programmi. Tutti i modelli della catena compaiono nell'ordine configurato; nessuna chiave o ricevuta è mostrata.

In prova il primo pulsante diventa **✅ Torna ai salvataggi reali**. La scelta resta nel database anche dopo il riavvio e vale per tutti i nuovi inserimenti; quelli già iniziati mantengono la loro modalità.

### 3 · Aiuto

```text
❓ Come funziona

📷 Nuovo scontrino: invia una foto o un PDF. Ti mostro insieme i dati letti: confermali o correggi solo quello sbagliato. Se manca qualcosa, ti chiedo solo quel dato; poi scegli cassa e tipologia.

💰 Nuova entrata: registra un’entrata senza foto.

Alla fine ti mostro il riepilogo: «Accetta» registra, «Modifica» ti permette di cambiare un dato, la cassa, la tipologia o la nota. Non salvo nulla prima di «Accetta». Solo in modalità PROVA non salvo nel portale.

📊 Riepilogo: consulta i saldi delle casse.
📋 Ultime operazioni: controlla le tue registrazioni.
⏳ Operazioni in attesa: consulta gli elementi da riprovare o verificare.

Aprire il menu non annulla un inserimento in corso. Per interromperlo premi «Annulla operazione».
```

Per l'amministratore si aggiunge:

```text
In ⚙️ Amministrazione trovi:
✍️ Inserisci a mano: registra un’entrata o un’uscita senza foto.
Alla fine controlli il riepilogo e registri solo con «Accetta».
🧪 Lì puoi anche attivare o disattivare la modalità prova.
```

Il ritorno **Menu principale** è nella tastiera amministratore, non nei messaggi dell'inserimento. Gli strumenti amministratore mantengono **Indietro**. Usare il menu non cancella un inserimento. I passaggi testuali conservano le risposte; non c'è un modulo, calendario o tastierino. Le schermate informative sono accessibili durante le attese dell'AI o del portale; salvataggi e altre azioni che cambiano dati restano sequenziali.

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

### 5 · Controllo dei dati letti, tutti insieme

Con descrizione, data e importo letti compare un solo messaggio:

```text
🧾 Ecco i dati letti dalla ricevuta.
Se sono giusti premi «Conferma», altrimenti tocca il dato da correggere.

📅 29/10/2026
🏪 Negozio esempio
💶 12,50 €
```

```text
[✅ Conferma]
[📅 Data] [💶 Importo]
[✏️ Descrizione]
[Annulla]
```

«Conferma» passa alla scelta della cassa. Non ci sono più proposte campo per campo né il pulsante «Conferma valore» (decisione superata il 31 ottobre 2026). Se la lettura è incerta compare anche `⚠️ Non sono sicuro di aver letto bene: controlla con attenzione.`

### 6 · Dati mancanti o correzione

Se l'analisi non ha letto un campo, il bot chiede **solo quelli mancanti**, uno per messaggio, con il solo pulsante **[Annulla]**; poi mostra il riepilogo della sezione 5:

```text
Non sono riuscito a leggere la data. Me la scrivi come giorno-mese-anno? (es. 29-10-2026 o 29/10/26)
```

```text
Non sono riuscito a leggere il negozio. Come si chiama?
```

```text
Non sono riuscito a leggere il totale. Quanto hai speso? (es. 12,50)
```

Se l'analisi fallisce del tutto, si compilano descrizione, data e importo come in `/entrata` (sezione 13).

Correzione scelta dall'utente (dal riepilogo iniziale o da «Modifica» nel riepilogo finale), ad esempio:

```text
Qual è la data giusta? Scrivila come giorno-mese-anno (es. 29-10-2026 o 29/10/26)

Valore attuale: 29/10/2026
Scrivi il valore corretto oppure premi «⬅️ Torna al riepilogo».
```

```text
[⬅️ Torna al riepilogo]
[Annulla]
```

Le date sono sempre in forma italiana giorno-mese-anno. Separatori ammessi: `-`, `/`, `.` o spazio; giorno e mese anche con una cifra (`2-3-26`), anno con due o quattro cifre (`26` = `2026`).

```text
Come si chiama il negozio?
```

```text
Qual è l'importo giusto? (es. 12,50)
```

Dopo il nuovo valore il bot torna al riepilogo da cui è partita la correzione: quello iniziale (sezione 5) oppure quello finale (sezione 9 bis), senza saltare passaggi e senza salvare. Un valore non valido mostra `⚠️ Non ho capito il valore scritto. Riprova.` e ripete la richiesta. La correzione della categoria letta dall'AI non è più proposta: non viene inviata al portale.

### 7 · Scelta della cassa

```text
⏳ Cerco le casse…
```

```text
🏦 Quale cassa vuoi usare? (1 di 3)
```

```text
[Cassa esempio A]
[Cassa esempio B]
[Annulla]
```

Quando la cassa si cambia da «Modifica» compare anche **[⬅️ Torna al riepilogo]**. Scegliere la stessa cassa torna subito al riepilogo finale; una cassa diversa richiede di nuovo la tipologia.

### 8 · Entrata o uscita

Questo passaggio compare solo con `/manual`. `/entrata` è già impostato su Entrata; una foto o un PDF è già impostato su Uscita.

```text
↔️ È un'entrata o un'uscita? (2 di 3)
```

```text
[⬇️ Entrata] [⬆️ Uscita]
[Annulla]
```

Nel `/manual` dell'amministratore la direzione si può cambiare da «Modifica» (**↔️ Entrata/Uscita**): la stessa scelta torna al riepilogo, quella opposta richiede di nuovo la tipologia.

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
[Annulla]
```

Quando la tipologia si cambia da «Modifica» compare anche **[⬅️ Torna al riepilogo]**.

Con tutte le voci subito disponibili:

```text
🏷️ Che tipo di spesa è? (3 di 3)
```

Dopo «Mostra altre»:

```text
🏷️ Altre tipologie
```

### 9 bis · Riepilogo finale: Accetta o Modifica

Dopo la tipologia lo stesso messaggio diventa il riepilogo finale. Nulla è salvato finché non premi «Accetta».

```text
📋 Riepilogo finale: controlla i dati prima di registrare.

📅 29/10/2026
🏪 Negozio esempio
💶 12,50 € (uscita)
🏦 Cassa esempio
🏷️ Tipologia esempio
👥 Unità esempio
🗒️ Nessuna nota

Premi «✅ Accetta» per registrare l’operazione, oppure «✏️ Modifica» per cambiare un dato o aggiungere una nota. Non salvo nulla finché non premi «Accetta».
```

```text
[✅ Accetta]
[✏️ Modifica]
[Annulla]
```

Per l'amministratore in prova compare anche `🧪 Modalità PROVA: non viene salvato nulla nel portale.` prima dell'ultima riga. **Accetta** salva con la nota attuale oppure senza nota.

**Modifica** mostra i dati e i campi modificabili:

```text
✏️ Cosa vuoi modificare?
Dopo la modifica torni al riepilogo: non salvo nulla finché non premi «Accetta».
```

```text
[📅 Data] [💶 Importo]
[✏️ Descrizione]
[🏦 Cassa] [🏷️ Tipologia]
[↔️ Entrata/Uscita]   ← solo /manual dell'amministratore
[🗒️ Nota]
[⬅️ Torna al riepilogo]
[Annulla]
```

Unità e categoria del portale non hanno pulsanti: nel percorso attuale non si scelgono (unità predefinita della cassa, categoria vuota). Una data in un altro anno scout fa scegliere di nuovo cassa e tipologia, con il prefisso:

```text
📅 La nuova data è in un altro anno scout: scegli di nuovo cassa e tipologia.
```

**Nota**:

```text
🗒️ Scrivi la NOTA per il portale (massimo 500 caratteri).
Nota attuale: Nota esempio
Dopo l’invio torni al riepilogo: non salvo nulla finché non premi «Accetta».
```

```text
[🧹 Togli la nota]   ← solo se c'è già una nota
[⬅️ Torna al riepilogo]
[Annulla]
```

Scritta la nota, compare un nuovo riepilogo finale con la riga `🗒️ Nota esempio`: **non** parte il salvataggio. Una nota oltre 500 caratteri viene rifiutata:

```text
La nota è troppo lunga: scrivila in massimo 500 caratteri.
```

Se il portale non risponde durante una modifica, il riepilogo finale torna con le scelte precedenti e il prefisso:

```text
⚠️ Non riesco a leggere le casse dal portale adesso: ho lasciato i dati come prima.
```

### 10 · Salvataggio in corso

```text
⏳ Sto inserendo…

📅 29/10/2026
🏪 Negozio esempio
💶 12,50 € (uscita)
🏦 Cassa esempio
🏷️ Tipologia esempio
🗒️ Nota esempio
```

```text
🧪 Modalità PROVA: non viene salvato nulla nel portale.
```

Il messaggio del riepilogo finale su cui hai premuto «Accetta» viene modificato, prima per il progresso e poi per l'esito, senza pulsanti. Non compare un nuovo risultato. Solo se Telegram rifiuta l'edit viene usato un messaggio sostitutivo; se anche questo fallisce, dati, nota ed esito restano nel database e l'errore viene registrato senza ripetere la registrazione.

### 11 · Spesa salvata

```text
✅ Fatto! Spesa salvata e verificata nel portale.

📅 29/10/2026
🏪 Negozio esempio
💶 12,50 € (uscita)
🏦 Cassa esempio
🏷️ Tipologia esempio
👥 Unità esempio
🗒️ Nota esempio
```

I campi vuoti non compaiono. La riga 👥 manca anche quando il portale non offre unità per l'anno; la riga 🗒️ manca se hai salvato senza nota. Non vengono mostrate voci contabili, percorsi o informazioni sull'archivio.

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

### 13 · Inserimento manuale: «Nuova entrata» / Amministrazione → «Inserisci a mano»

**💰 Nuova entrata** è nel menu principale per tutti gli utenti autorizzati; **✍️ Inserisci a mano** (entrata o uscita) è nella tastiera ⚙️ Amministrazione, solo per l'amministratore. Restano `/entrata` e `/manual`. Ogni richiesta è un messaggio separato: descrizione, data italiana, importo. Le risposte non vengono eliminate.

```text
✏️ Scrivi la descrizione dell’operazione.
```

```text
[Annulla]
```

Dopo la descrizione:

```text
📅 Scrivi la data come giorno-mese-anno, ad esempio 29-10-2026, 29/10/26 o 2-3-26.
```

Dopo la data:

```text
💶 Scrivi l’importo (es. 12,50).
```

Se un valore non è valido, il bot mostra `⚠️ Non ho capito il valore scritto. Riprova.` e ripete la richiesta. Ogni passaggio mostra anche la modalità iniziale dell'operazione.

Dopo aver compilato i dati:

```text
Ecco i dati dell’operazione. Potrai correggerli nel riepilogo finale con «Modifica».

📅 29/10/2026
📝 Prova Telegram
💶 0,10 €
```

Sticker, audio, video e file diversi da foto o PDF vengono eliminati e il bot risponde:

```text
Sticker, audio, video e altri file non sono supportati e non li conservo. Usa i pulsanti, scrivi il dato richiesto oppure invia una foto o un PDF della ricevuta.
```

### 14 · Ultima scelta manuale

Dopo la tipologia compare il riepilogo finale della sezione 9 bis: **✅ Accetta** salva (o fa la prova) e mostra l'esito nello stesso messaggio; **✏️ Modifica** permette di cambiare dati, cassa, tipologia, nota e, nel `/manual` dell'amministratore, Entrata/Uscita. La precedente regola «nota o Salva senza nota salvano subito» è superata dal 31 ottobre 2026.

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
⏳ Richiesta registrata. Il NAS la esegue al prossimo controllo, di solito entro 10 minuti: aggiorno questo messaggio a ogni passaggio.
```

Lo stesso messaggio viene poi modificato in sequenza:

```text
⏳ Inizio l'aggiornamento: scarico la nuova versione…
```

```text
🔧 Installo 0ef7d60 - feat: … (30/10/2026 00:50).
Preparo la nuova versione, può richiedere qualche minuto. Intanto il bot continua a funzionare.
```

```text
🔄 Riavvio il bot con 0ef7d60 - feat: … (30/10/2026 00:50).
Per un minuto circa potrei non rispondere: l'esito comparirà qui.
```

```text
✅ Aggiornamento installato: 0ef7d60 - feat: … (30/10/2026 00:50)
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
Non riesco a leggere le casse dal portale adesso. Non ho salvato nulla e i dati dello scontrino restano qui: premi «Conferma» per riprovare oppure Annulla.
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

👉 Controlla nel portale se compare. Se non c'è, riparti con «Nuova entrata» dal menu principale o con «Inserisci a mano» da ⚙️ Amministrazione.
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
