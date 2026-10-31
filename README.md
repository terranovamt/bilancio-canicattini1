# Archivio spese AGESCI via Telegram

Progetto per un bot Telegram privato: riceve una foto o un PDF, corregge e analizza la ricevuta con un modello visivo locale, raccoglie i dati e conserva il file sul NAS. Le opzioni per la registrazione AGESCI vengono lette dal portale e mostrate nel bot.

## Stato attuale

Il bot, l'analisi locale, l'archivio, SQLite, la coda dei ritentativi e il client Playwright hanno test automatici. Il client segue l'ordine del portale: anno scout ricavato dalla data, Tesoriere di gruppo, Prima Nota, poi le scelte dinamiche. Il salvataggio reale da CSV è stato completato e verificato sul portale il 27 ottobre 2026.

È disponibile anche `bilancio/csv_import.py` per importare CSV con classificazioni AGESCI. L'import completato comprendeva 112 movimenti: 80 entrate e 32 uscite. Due uscite già presenti sono state escluse dal nuovo invio; le altre 110 sono state elaborate e verificate nel registro. Due timeout di autenticazione hanno interrotto i primi blocchi, poi ripresi senza reinviare le righe già verificate. Il verificatore riconosce l'unità `Gruppo G1` anche quando il registro la mostra come `G1`.

Il comportamento predefinito è **REALE** (`DRY_RUN=false`): dopo l'ultima scelta contabile il bot mostra il riepilogo e chiede una **nota facoltativa**: scriverla o premere «Salva senza nota» salva e verifica l'operazione nel portale, senza altre conferme. Vale per scontrini, `/entrata` e `/manual`; quest'ultimo permette entrate e uscite senza ricevuta. Per non salvare, l'amministratore deve scegliere esplicitamente **Amministrazione → Attiva modalità prova**. La ricevuta viene archiviata sul NAS e non allegata alla registrazione web. L'importatore CSV mantiene le proprie protezioni: simulazione predefinita, `--apply`, conteggio e conferma digitata per gli import reali.

La modalità scelta dall'amministratore vale per tutti i nuovi inserimenti, fino alla disattivazione, anche dopo un riavvio. È conservata nel database e prevale su `DRY_RUN`; in assenza di una scelta usa il valore di `.env`, ora reale per impostazione predefinita. **Per un NAS già configurato con `DRY_RUN=true`, dopo l'aggiornamento premi «Torna ai salvataggi reali» una volta**: il programma non modifica automaticamente `.env`. Ogni operazione conserva la modalità iniziale, anche nei ritentativi; le vecchie code senza modalità registrata vengono riprovate solo in simulazione, per non trasformare vecchie prove in inserimenti reali.

L'analisi delle ricevute usa OCR locale e Gemini. Se l'analisi non riesce, il bot mantiene il file temporaneo e chiede i campi manualmente.

## Componenti

- `bot/`: comandi Telegram, accesso autorizzato, inserimenti e ritentativi. I testi e le etichette sono centralizzati in [`bot/messages.py`](bot/messages.py); gli esempi sono nel [catalogo dei messaggi](docs/PREVIEW_MESSAGGI_TELEGRAM.md).
- `vision/`: correzione locale dell'immagine, OCR, schema dei dati e client Gemini.
- `storage/`: conservazione temporanea e archivio permanente delle ricevute.
- `db/`: spese, stati, duplicati e coda SQLite.
- `bilancio/`: interfaccia e client Playwright; selettori osservati e opzioni lette dinamicamente dal portale.
- `Dockerfile` e `docker-compose.yml`: esecuzione prevista su Synology.
- `docs/GUIDA_SYNOLOGY.md`: istruzioni per preparare il NAS.
- `docs/CONTESTO_PROGETTO.md`: decisioni e stato dettagliato del lavoro.
- `aggiorna.sh`: aggiornamento sul NAS da un pacchetto estratto separatamente, senza sostituire credenziali o dati; procedura nella sezione 15 della guida Synology.
- `scarica.sh`: download e deploy dal ramo Git `deploy` su comando dell'utente. Prima configurazione in [Deploy Git sul Synology](docs/DEPLOY_GIT_SYNOLOGY.md).
- `automatico.sh`: per l'Utilità di pianificazione DSM. L'attività in modalità `controlla`, ogni 10 minuti, verifica il ramo Git `deploy` senza installare autonomamente. Il bot avvisa soltanto l'amministratore quando trova una versione nuova, con un pulsante per aggiornare dopo conferma; procedura nella sezione 8 della stessa guida. Versione attiva e disponibile si consultano in **Amministrazione → Versione e aggiornamenti**.

## Agenti AI in VS Code

Il punto di contatto e **Manager**: mantiene la visione del progetto, assegna incarichi circoscritti agli specialisti e verifica il risultato complessivo. Le definizioni sono in [`.github/agents/`](.github/agents/).

| Agente | Responsabilita |
| --- | --- |
| [Manager](.github/agents/manager.agent.md) | Dialogo con te, contesto, coordinamento e verifica finale. |
| [Sviluppo bot e archivio](.github/agents/bot-python.agent.md) | Telegram, accessi, archivio, database, coda, integrazione e deploy generale. |
| [Automazione portale AGESCI](.github/agents/agesci-portal.agent.md) | Client del portale, Playwright, selettori, CSV e verifica delle registrazioni. |
| [AI locale Ollama](.github/agents/ollama-ai.agent.md) | Analisi ricevute (OCR+Gemini), prompt, immagini/PDF, errori e prestazioni. |
| [Doctor agenti](.github/agents/doctor.agent.md) | Coerenza di agenti e istruzioni, documentazione collegata, ripetizioni, riferimenti obsoleti e pulizia sicura. |

Sul PC, in VS Code:

1. Apri la chat del progetto.
2. Seleziona **Manager** nel selettore degli agenti. Se non compare, ricarica la finestra di VS Code e controlla di avere aperto questa cartella di progetto.
3. Scrivi la richiesta normalmente: il Manager usa gli specialisti quando servono e raccoglie i risultati nella stessa conversazione.
4. Per un controllo di manutenzione, chiedi per esempio: «Fai controllare al Doctor gli agenti e la documentazione: correggi i riferimenti errati e segnalami duplicazioni e incongruenze».

Gli agenti non fissano un modello nel proprio file: usano la scelta predefinita risolta da Copilot. Il precedente valore letterale `Auto (copilot)` veniva rifiutato dal runtime di questa chat. Gli specialisti sono configurati come sottoagenti e non compaiono nel selettore principale. Non occorre passare manualmente da uno all'altro. La scelta iniziale del Manager va fatta in VS Code: questi file non cambiano automaticamente l'agente di una chat gia aperta. La delega richiede che la versione e la modalita di chat in uso supportino i sottoagenti personalizzati.

Se compare ancora `Model 'Auto (copilot)' is not available` dopo la correzione:

1. Salva i file del progetto.
2. Premi `Ctrl+Shift+P` in VS Code e scegli **Developer: Reload Window**.
3. Apri una nuova chat del progetto, seleziona **Manager** e un modello effettivamente disponibile nel selettore di Copilot.
4. Prova a chiedere una risposta breve allo specialista Ollama, senza avviare il bot. La validazione dei file non basta a confermare che il sottoagente sia partito.

Se la delega fallisce, il Manager deve spiegare una sola volta la causa osservata e il rimedio, poi continuare direttamente senza ripetere lo stesso avviso a ogni incarico.

Il Doctor lavora su richiesta o quando il Manager rileva incoerenze rilevanti: non e un controllo periodico in sottofondo. Corregge gli errori documentali sicuri nel perimetro richiesto, ma prima di cancellare file o sezioni sostanziali, rimuovere agenti o cambiare responsabilita presenta una proposta e richiede conferma. Non pulisce ricevute, database, log, diagnostica o backup. Le regole comuni restano nelle [istruzioni condivise](.github/copilot-instructions.md); decisioni e stato verificato restano nel [contesto del progetto](docs/CONTESTO_PROGETTO.md).

## Configurazione privata

1. Crea `.env` copiando `.env.example`.
2. Inserisci lì token, ID autorizzati e credenziali: non inviarli in chat e non inserirli in GitHub.
3. Inserisci `GEMINI_API_KEY` e, se vuoi, `GEMINI_MODEL` (default `gemini-1.5-flash`).
4. Per l'uso normale lascia `DRY_RUN=false`; per una prova imposta esplicitamente `DRY_RUN=true` e verifica che Telegram mostri **Modalità PROVA**. Una scelta salvata nel menu Amministrazione prevale su questa variabile.

Il file `.env`, le ricevute e il database sono esclusi sia da Git sia dal contesto di creazione dell'immagine Docker.

## Prova locale sul PC

Per provare OCR+Gemini sul PC:

1. Crea l'ambiente Python e installa i pacchetti, se non l'hai già fatto:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

2. Avvia il bot dalla stessa finestra di PowerShell. Questi valori valgono solo per questa prova e non modificano `.env`:

```powershell
$env:GEMINI_API_KEY = "inserisci-qui-la-chiave"
$env:GEMINI_MODEL = "gemini-1.5-flash"
$env:GEMINI_TIMEOUT_SECONDS = "600"
$env:DATA_PATH = "$PWD\data-local"
$env:STORAGE_PATH = "$PWD\scontrini-local"
$env:PLAYWRIGHT_BROWSER_CHANNEL = "chrome"
$env:DRY_RUN = "true"
.\.venv\Scripts\python.exe -m bot.main
```

Il bot legge token e ID dal file `.env`; non inviare quel file o le sue credenziali in chat. La prova salva database, registri e diagnostica in `data-local`, e le ricevute in `scontrini-local`, cartelle escluse da Git. Per fermare il bot, torna alla finestra PowerShell e premi `Ctrl+C`.

### Inserimento manuale e scontrini non leggibili

Per un'entrata senza ricevuta, qualunque utente autorizzato può premere **💰 Nuova entrata** nel menu principale (oppure scrivere `/entrata`):

1. Scrivi la **descrizione** dell'operazione come normale messaggio.
2. Scrivi la **data**, giorno-mese-anno: valgono `29-10-2026`, `29/10/2026`, `29.10.26`, `2 3 26` o `2-3-26` (anno `26` = `2026`). Resta accettato anche il formato ISO.
3. Scrivi l'**importo**, ad esempio `12,50`. Un dato non valido viene richiesto di nuovo, senza cancellare alcuna risposta.
4. Scegli cassa e tipologia: la direzione è già Entrata. Prima della cassa puoi ancora correggere i dati con i pulsanti del riepilogo.
5. Nell'ultimo messaggio controlla il riepilogo e scrivi una **NOTA** facoltativa (massimo 500 caratteri), oppure premi **💾 Salva senza nota**: il salvataggio parte subito. Per una prova senza salvataggio attiva prima **Modalità PROVA** da Amministrazione.
6. **Quello stesso messaggio** mostra «Sto inserendo…» mantenendo il riepilogo, poi l'esito e i dati; il pulsante sparisce. Vale anche quando la nota arriva come testo: non viene inviato un nuovo risultato.

Per inserire a mano un'entrata **o un'uscita senza ricevuta**, l'amministratore può aprire **⚙️ Amministrazione** e premere **✍️ Inserisci a mano** (oppure scrivere `/manual`):

1. Scrivi descrizione, data e importo nei tre passaggi testuali; non vengono proposti importi di prova.
2. Scegli cassa, **Entrata** o **Uscita**, poi la tipologia. Scrivi una nota o premi **💾 Salva senza nota**: il salvataggio parte senza un'altra conferma.
3. Attendi l'esito verificato. Se la modalità prova era attiva all'inizio, non viene salvato nulla nel portale.

Non ci sono modulo, calendario o tastierino: si usano normali messaggi separati e le risposte restano in chat. Sticker, audio, video e file diversi da foto o PDF restano non supportati e vengono eliminati come prima.

Se Telegram non permette di modificare il riepilogo (ad esempio perché è stato eliminato), il bot registra l'errore e usa un messaggio sostitutivo solo quando necessario. Se fallisce anche l'invio sostitutivo, nota, dati, esito e riferimento restano nel database locale; non viene ripetuta la registrazione solo per un problema di notifica. Il recapito di quell'esito non viene ritentato automaticamente: chiedi all'amministratore di controllare **Ultime operazioni** e i log locali prima di reinserire.

Il vecchio `/testspesa` è stato rimosso per non confondere una prova con un inserimento reale: usa **Inserisci a mano** e scegli la modalità desiderata in Amministrazione.

Inviando una foto o un PDF, invece, la direzione è già **Uscita**. Il bot prova prima un OCR locale e poi Gemini. Ogni valore letto è proposto nel relativo passaggio (descrizione, data, importo): premi **Conferma valore** oppure scrivi quello corretto. Non viene usato automaticamente senza il tuo controllo. Se l'analisi fallisce, anche per timeout, ricevi un avviso e compili gli stessi tre campi a mano, poi scegli cassa e tipologia senza dover selezionare di nuovo Uscita. File e stato per il recupero manuale vengono conservati prima di avviare l'AI. L'attesa totale dell'analisi è limitata da `GEMINI_TIMEOUT_SECONDS` (oppure `LOCAL_VISION_TIMEOUT_SECONDS` per compatibilità). Il file resta temporaneamente sul NAS: compare nell'archivio `scontrini` quando avvii il salvataggio, con i dati e la cassa scelti. `/annulla` elimina la copia temporanea, non le ricevute già archiviate. La compilazione a mano mantiene la modalità con cui hai inviato la ricevuta. Questo percorso è disponibile agli stessi utenti autorizzati che possono inviare scontrini, anche se Gemini non è configurato. Fuori dal percorso manuale dell'amministratore, un'uscita senza file resta bloccata, anche nei ritentativi della coda.

### Attesa dell'analisi immagini

Il bot usa i dati solo quando la risposta è completa e valida. Durante l'attesa aggiorna lo stesso messaggio ogni 30 secondi indicando la fase: preparazione dell'immagine, OCR locale, invio a Gemini e scrittura dei dati (con il numero di caratteri ricevuti). Non mostra frammenti della ricevuta o del ragionamento e non registra dati parziali.

Il limite predefinito è **600 secondi (10 minuti)** per l'analisi, compresa la preparazione dell'immagine. Se scade o la risposta si interrompe, il file resta conservato e il bot chiede i dati a mano. `/start` e l'aiuto restano disponibili durante l'attesa.

Per un NAS già configurato:

1. Controlla localmente il valore `GEMINI_TIMEOUT_SECONDS` nella configurazione privata, senza condividerla.
2. Se contiene ancora `180` e vuoi attendere fino a 10 minuti, portalo a `600`.
3. Dalla cartella attiva sul NAS ricrea il bot con `sudo docker compose up -d --force-recreate bot`, quando nessuno sta inserendo un'operazione.

L'aggiornamento del programma non riscrive la configurazione privata: un valore esplicito di `180` continua quindi a valere. Gli errori Telegram nell'aggiornare il messaggio vengono registrati senza interrompere l'analisi.

### Menu e opzioni contabili

Per usare il bot non occorre ricordare i comandi:

1. Su Telegram premi **Avvia**, oppure invia `/start` una volta. Al posto della tastiera del telefono compare il **menu fisso in basso**; dopo un aggiornamento del bot invia `/start` una volta per vedere il menu nuovo.
2. Premi un pulsante del menu in basso: **📷 Nuovo scontrino**, **💰 Nuova entrata**, **📊 Riepilogo**, **📋 Ultime operazioni**, **⏳ Operazioni in attesa**, **❓ Aiuto** o **✖️ Annulla operazione**. Il pulsante premuto compare in chat come un tuo messaggio e il bot risponde con un nuovo messaggio: è il funzionamento normale di Telegram. Per scrivere un testo usa l'icona della tastiera accanto al campo del messaggio.
3. Se sei amministratore, **⚙️ Amministrazione** sostituisce la stessa tastiera in basso con **✍️ Inserisci a mano** e gli strumenti di gestione, tutti protetti dai permessi. **💰 Nuova entrata** resta nel menu principale per tutti gli utenti autorizzati. La modalità prova/reale è indicata per tutti.
4. Nella tastiera amministratore premi **🏠 Menu principale** per ripristinare quella principale. Negli strumenti resta anche **⬅️ Indietro**. Cambiare menu non cancella i dati di un inserimento già aperto.
5. Per interrompere un inserimento premi **Annulla** nel suo messaggio, oppure **✖️ Annulla operazione** nel menu in basso.

In **Amministrazione** trovi modalità prova, verifica del collegamento, aggiornamento casse e tipologie, versione e aggiornamenti del bot, pulizia della cronologia. **Attiva modalità prova** disabilita i salvataggi nel portale per tutti i nuovi inserimenti; **Torna ai salvataggi reali** ripristina il funzionamento normale. Installazione degli aggiornamenti e pulizia mantengono le proprie conferme. Versione attiva e disponibile si consultano da **Versione e aggiornamenti**.

Le schermate del menu, l'aiuto e `/start` possono rispondere durante l'analisi AI o le attese del portale; le azioni che modificano dati restano elaborate una alla volta per evitare sovrapposizioni. Restano possibili i normali tempi di risposta della rete Telegram. I comandi scritti rimangono disponibili come alternativa, compresi `/help`, `/manual` e `/entrata`.

### Avvisi Git ogni 10 minuti

L'attività DSM **Bilancio - controllo aggiornamenti**, in modalità `controlla` ogni 10 minuti, interroga Git con la chiave di sola lettura già presente sul NAS. Non serve inserire credenziali Git nel bot. Il bot legge l'esito dal volume persistente ogni minuto e invia un messaggio privato solo a `ADMIN_USER_ID` se trova una versione diversa da quella attiva.

1. Quando ricevi **È disponibile una nuova versione del bot**, premi **🔄 Installa aggiornamento**, oppure invia `/aggiorna`.
2. Controlla che nessuno stia registrando una spesa e premi **Sì, installa** solo se vuoi procedere. Aprire l'avviso non installa nulla.
3. La richiesta viene eseguita al successivo controllo NAS, normalmente entro 10 minuti. Funziona anche in modalità reale, senza cambiare la modalità degli inserimenti.
4. Lo stesso messaggio della richiesta si aggiorna da solo:
   - **Inizio l'aggiornamento** quando il NAS inizia a scaricare da Git;
   - **Installo** mentre prepara la nuova versione, con il bot ancora attivo;
   - **Riavvio il bot** subito prima del riavvio: il NAS aspetta fino a 30 secondi che il bot abbia mostrato questo messaggio;
   - infine l'esito, ad esempio **Aggiornamento installato**. Il bot controlla l'avanzamento ogni 3 secondi.

   Consulta poi **Versione e aggiornamenti** per verificare la versione attiva.

Una versione già annunciata non viene notificata di nuovo a ogni controllo o dopo un riavvio: il codice dell'ultima versione notificata è conservato sul volume dati. Se Telegram non riceve il messaggio, il bot ritenta alla lettura successiva. Un controllo Git fallito non genera avvisi di novità usando il risultato vecchio rimasto in cache; l'errore resta visibile nella schermata della versione.

L'attività ogni 10 minuti è stata confermata dall'utente; la sua esecuzione effettiva non è verificabile dai test locali. Per prepararla o controllarla segui la [sezione 8 della guida NAS](docs/DEPLOY_GIT_SYNOLOGY.md#82-in-dsm-attività-di-controllo-ogni-10-minuti). Per installare **solo dopo conferma**, lascia disattivata l'eventuale attività separata di installazione programmata descritta nella sezione 8.4.

### Opzioni contabili dinamiche

Le casse e le tipologie `Voci Scout` vengono lette dal portale in sottofondo circa 10 secondi dopo l'avvio del bot e di nuovo ogni notte alle 02:30 UTC. Restano in memoria per 26 ore, così durante la giornata i menu si aprono senza attendere il browser. La cache distingue anno scout, cassa, entrata/uscita, voce, tipologia e unità, così non mescola elenchi dipendenti. Se scegli un menu mentre la lettura iniziale è ancora in corso, il bot mostra un messaggio di attesa e completa la scelta appena possibile. Usa `/aggiornaopzioni` (amministratore) per rileggere subito casse e tipologie: al termine il bot ti scrive quante casse ha letto e quante tipologie di entrata e di uscita ha trovato per ciascuna. Il salvataggio e la verifica nel portale restano sempre operazioni reali di browser, anche quando i menu provengono dalla cache.

Nel passaggio delle tipologie il bot mostra prima le 6 più usate per quella cassa e direzione; in fondo c'è il pulsante **➕ Mostra altre** con quelle mancanti. All'inizio, quando non ci sono ancora spese registrate, mostra le prime 6 nell'ordine del portale.

I percorsi manuali usano `Voci Scout` e le tipologie disponibili per cassa e direzione. Impostano l'unità predefinita associata alla cassa; se il portale non offre alcuna unità («No results»), lasciano l'unità vuota e non la mostrano nel riepilogo. La categoria resta vuota.

- `/manual` e `/entrata` salvano davvero per impostazione predefinita; rispettano la modalità prova come gli scontrini.
- Usa `/annulla` o il pulsante **Annulla** prima di scrivere o saltare la nota per interrompere senza salvare.
- Se il salvataggio non viene verificato, il bot non riprova da solo: controlla nel portale e ripeti `/manual` o `/entrata` solo se l'operazione non c'è.
- Durante le operazioni lente compare un messaggio di attesa e i pulsanti spariscono. Se tocchi di nuovo un pulsante vecchio, il bot lo ignora e mostra «Questo pulsante non è più attivo».
- Se il portale non risponde mentre il bot legge casse o tipologie, i dati manuali restano conservati: premi **✅ Va bene** per riprovare oppure **Annulla**. Per uno scontrino, il file resta salvato e il bot propone **✅ Sì, è giusto** per riprovare oppure **Annulla**.
- Un nuovo `/manual` o `/entrata` annulla da solo un inserimento manuale rimasto aperto.
- Se una conversazione iniziata prima di un aggiornamento mostra ancora i vecchi pulsanti per unità o categoria, usa `/annulla` e ricomincia.

Per la prima prova usa una descrizione chiaramente riconoscibile e controlla con attenzione anno, conto, direzione, tipologia, unità predefinita e importo. Non condividere i dati della spesa in chat con altre persone.

### Ultime spese e pulizia

**📋 Ultime operazioni** (o `/ultime`) mostra le ultime 10 spese: ✅ salvata e verificata, 🧪 prova senza salvataggio, ⚠️ da controllare oppure ⏳ in attesa o in corso. Le operazioni annullate o mai confermate non compaiono. Nei riepiloghi compaiono solo i campi compilati; i percorsi e i dettagli tecnici vengono inviati solo all'amministratore. La pulizia e l'aggiornamento delle opzioni si trovano nella schermata **Amministrazione**.

`/pulisci` (solo amministratore) svuota la memoria del bot. Cancella la coda, l'elenco di `/ultime`, le operazioni aperte, i file temporanei, i log e la diagnostica. Non cancella nulla nel portale AGESCI e non tocca le ricevute archiviate.

1. Su Telegram apri **Amministrazione → Pulisci cronologia**, oppure invia `/pulisci`.
2. Leggi l'avviso e premi **Sì, pulisci**; premi **No, lascia stare** per lasciare tutto com'è.
3. Il bot risponde con il numero di spese e di file eliminati.

### Riepilogo delle casse

`/riepilogo` legge dal portale AGESCI, in sola lettura, i saldi di tutte le casse dell'anno scout corrente. Lo possono usare tutti gli utenti autorizzati e non modifica nulla.

1. Su Telegram invia `/riepilogo`.
2. Compare «⏳ Leggo i saldi dal portale…». Il bot legge la tabella generale di Prima Nota, senza entrare nelle singole casse.
3. Il messaggio si trasforma nel riepilogo. Per ogni cassa vedi:
   - il saldo attuale, cioè quanto dovresti avere in contanti o sul conto;
   - le entrate e le uscite;
   - il saldo iniziale.
4. Premi **📈 Dettagli** per vedere l'andamento mese per mese e le ultime 3 operazioni. Solo per questa vista il bot entra nei registri delle singole casse, senza modificare nulla.
5. Premi **⬅️ Anno …** per vedere l'anno scout precedente, **Anno … ➡️** per tornare avanti, oppure **🔄 Aggiorna** per rileggere il portale e tornare alla vista compatta.

Ogni lettura riuscita salva in SQLite uno **snapshot persistente dei soli saldi del portale**, separato per anno scout e con data e ora di lettura. Se compare «❌ Non riesco a leggere i saldi adesso», il bot mostra anche lo snapshot dello **stesso anno**, se disponibile, preceduto da «Ultimi dati letti dal portale il … alle …» e dall'avviso che sono dati non aggiornati. Non calcola mai i saldi dalle operazioni locali del bot. Una lettura fallita o invalida non sovrascrive lo snapshot precedente.

La tabella generale deve essere completa e gli importi coerenti, altrimenti viene segnalato un errore. Nella vista Dettagli, se compare «⚠️ Il portale mostra solo … righe su …», il saldo è comunque quello del portale, ma andamento e ultime operazioni sono parziali. Se Dettagli fallisce, l'eventuale cache viene mostrata **solo come saldi**, con «dettagli non disponibili»: non sostituisce andamento e operazioni.

### Lettura e resoconto giornaliero dell'amministratore

Con il bot acceso, **ogni mezzanotte locale** il bot legge i saldi dell'anno scout corrente dalla tabella generale (`read_ledgers(include_entries=False)`) e aggiorna lo snapshot persistente. Usa `settings.timezone`, configurato da `TZ` (predefinito `Europe/Rome`), compresi i cambi tra ora solare e legale. Non serve un'attività DSM separata.

Solo l'amministratore riceve sempre un tentativo di notifica dell'esito della lettura, riuscita o fallita, e il resoconto del **giorno calendario precedente**:

- conteggi delle operazioni, delle entrate e delle uscite; importi globali e per cassa, calcolati con `Decimal`;
- solo operazioni reali `submitted` con evento di verifica persistito dal bot, incluse quelle riuscite tramite retry;
- escluse prove/dry-run, operazioni incomplete, in coda o fallite e risultati «già presenti»; un evento per operazione evita ricontaggi;
- nessuna descrizione, nota, identità utente o ricevuta nel report o nei suoi log; se non ci sono operazioni compare **zero movimenti**.

**Quale data conta:** il primo evento di registrazione verificata salvato nel DB, convertito nel fuso configurato, **non la data della ricevuta**. Il vecchio `updated_at` cambia anche con l'aggiornamento dei messaggi e non consente una ricostruzione certa; i vecchi `submitted` senza questo evento non vengono conteggiati automaticamente. Le nuove tabelle sono aggiunte senza cancellare o riscrivere le operazioni esistenti.

Lo snapshot resta nel volume del database dopo un riavvio. Se il portale viene letto ma SQLite non può salvare, la notifica distingue lettura riuscita da cache non aggiornata. Se non è possibile calcolare il report, viene mostrato un errore, non uno zero inventato. Il job richiede il bot acceso: non recupera automaticamente mezzanotti perse durante uno spegnimento. Un errore Telegram viene registrato senza contenuti privati; il recapito non è garantibile e non è ritentato automaticamente.

Dopo la pulizia la classifica delle tipologie più usate riparte da zero.

## Verifiche automatiche sul PC

Con Python installato, dalla cartella del progetto:

```powershell
python -m unittest discover -s tests -v
```

Per compilare i moduli senza avviare il bot:

```powershell
python -m compileall -q bot bilancio db storage vision tests
```

Queste verifiche non accedono al portale AGESCI e non contattano Telegram.

Per verificare solo snapshot, report e integrazioni relative, con l'ambiente virtuale locale e senza avviare il bot:

```powershell
$env:DRY_RUN = "true"
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_daily.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_bot_application.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_receipt_workflow.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_retry_service.py -v
```

Questi test usano database temporanei e client finti, anche quando simulano esiti reali. `DRY_RUN=true` da solo non autorizza prove manuali su servizi reali.

## Repository GitHub privato

Il repository locale è sul ramo `main`. Dal 29 ottobre 2026 l'utente ha autorizzato commit e push al termine delle modifiche verificate, senza richiedere ogni volta una nuova conferma. L'identità dell'autore viene configurata solo nel repository locale. Si pubblica senza forzature su `main` e `deploy`; il NAS legge `deploy` secondo la procedura di [aggiornamento Git](docs/DEPLOY_GIT_SYNOLOGY.md). Il push non dimostra che la nuova versione sia gia installata sul NAS.

```powershell
git push origin HEAD:refs/heads/main HEAD:refs/heads/deploy
```

Non aggiungere `.env`, ricevute, database, registrazioni non sanificate o credenziali al repository.
