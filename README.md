# Archivio spese AGESCI via Telegram

Progetto per un bot Telegram privato: riceve una foto o un PDF, corregge e analizza la ricevuta con un modello visivo locale, chiede conferma e conserva il file sul NAS. Le opzioni per la registrazione AGESCI devono essere lette dal portale e mostrate nel bot.

## Stato attuale

Il bot, l'analisi locale, l'archivio, SQLite, la coda dei ritentativi e il client Playwright hanno test automatici. Il client segue l'ordine del portale: anno scout ricavato dalla data, Tesoriere di gruppo, Prima Nota, poi le scelte dinamiche. Il salvataggio reale da CSV è stato completato e verificato sul portale il 27 ottobre 2026.

È disponibile anche `bilancio/csv_import.py` per importare CSV con classificazioni AGESCI. L'import completato comprendeva 112 movimenti: 80 entrate e 32 uscite. Due uscite già presenti sono state escluse dal nuovo invio; le altre 110 sono state elaborate e verificate nel registro. Due timeout di autenticazione hanno interrotto i primi blocchi, poi ripresi senza reinviare le righe già verificate. Il verificatore riconosce l'unità `Gruppo G1` anche quando il registro la mostra come `G1`.

La modalità `DRY_RUN=true` è quella predefinita e non salva sul portale. Per ogni nuova registrazione reale, usa `DRY_RUN=false` solo per il processo autorizzato e dopo aver controllato i dati e il numero di righe. La ricevuta viene archiviata sul NAS e non viene allegata alla registrazione web.

L'analisi delle ricevute è prevista in locale con Ollama. Non è configurato l'invio delle immagini a un servizio AI online. Il modello locale va scelto dopo aver verificato la memoria e le prestazioni disponibili sul NAS.

## Componenti

- `bot/`: comandi Telegram, accesso autorizzato, flusso di conferma e ritentativi.
- `vision/`: correzione locale dell'immagine, schema dei dati e client Ollama.
- `storage/`: conservazione temporanea e archivio permanente delle ricevute.
- `db/`: spese, stati, duplicati e coda SQLite.
- `bilancio/`: interfaccia e client Playwright; selettori osservati e opzioni lette dinamicamente dal portale.
- `Dockerfile` e `docker-compose.yml`: esecuzione prevista su Synology.
- `docs/GUIDA_SYNOLOGY.md`: istruzioni per preparare il NAS.
- `docs/CONTESTO_PROGETTO.md`: decisioni e stato dettagliato del lavoro.
- `aggiorna.sh`: aggiornamento sul NAS da un pacchetto estratto separatamente, senza sostituire credenziali o dati; procedura nella sezione 15 della guida Synology.
- `scarica.sh`: download e deploy dal ramo Git `deploy` solo su comando dell'utente, senza timer. Prima configurazione in [Deploy Git sul Synology](docs/DEPLOY_GIT_SYNOLOGY.md).

## Configurazione privata

1. Crea `.env` copiando `.env.example`.
2. Inserisci lì token, ID autorizzati e credenziali: non inviarli in chat e non inserirli in GitHub.
3. Lascia vuoto `LOCAL_VISION_MODEL` finché non è stato scelto un modello compatibile con il NAS.
4. Mantieni `DRY_RUN=true`.

Il file `.env`, le ricevute, il database e i modelli locali sono esclusi sia da Git sia dal contesto di creazione dell'immagine Docker.

## Prova locale sul PC

Per usare lo stesso modello locale previsto per il NAS:

1. Installa Ollama per Windows dal sito ufficiale e avvialo.
2. Apri PowerShell nella cartella del progetto e scarica il modello con `ollama pull qwen3-vl:2b`.
3. Crea l'ambiente Python e installa i pacchetti, se non l'hai già fatto:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

4. Avvia il bot dalla stessa finestra di PowerShell. Questi valori valgono solo per questa prova e non modificano `.env`:

```powershell
$env:LOCAL_VISION_URL = "http://127.0.0.1:11434"
$env:LOCAL_VISION_MODEL = "qwen3-vl:2b"
$env:DATA_PATH = "$PWD\data-local"
$env:STORAGE_PATH = "$PWD\scontrini-local"
$env:PLAYWRIGHT_BROWSER_CHANNEL = "chrome"
$env:DRY_RUN = "true"
.\.venv\Scripts\python.exe -m bot.main
```

Il bot legge token e ID dal file `.env`; non inviare quel file o le sue credenziali in chat. La prova salva database, registri e diagnostica in `data-local`, e le ricevute in `scontrini-local`, cartelle escluse da Git. Per fermare il bot, torna alla finestra PowerShell e premi `Ctrl+C`.

### Prova manuale AGESCI senza ricevuta

All'avvio il bot registra il menu dei comandi Telegram: scrivendo `/` compaiono i suggerimenti. Invia `/start` per visualizzare anche la tastiera rapida e, per l'amministratore, i comandi riservati. Il messaggio iniziale indica se la modalità è simulata o reale.

Le casse e le tipologie `Voci Scout` vengono lette dal portale in sottofondo circa 10 secondi dopo l'avvio del bot e di nuovo ogni notte alle 02:30 UTC. Restano in memoria per 26 ore, così durante la giornata i menu si aprono senza attendere il browser. La cache distingue anno scout, cassa, entrata/uscita, voce, tipologia e unità, così non mescola elenchi dipendenti. Se scegli un menu mentre la lettura iniziale è ancora in corso, il bot mostra un messaggio di attesa e completa la scelta appena possibile. Usa `/aggiornaopzioni` (amministratore) per svuotare la cache e rileggerla subito in sottofondo; se un menu era già aperto, annulla e ricomincia. Il salvataggio e la verifica nel portale restano sempre operazioni reali di browser, anche quando i menu provengono dalla cache.

L’amministratore può usare `/testspesa` per inserire data, descrizione e importo direttamente in Telegram, senza scontrino e senza AI. Il bot chiede cassa e direzione, usa `Voci Scout` e mostra le tipologie disponibili per quella scelta. Imposta l’unità predefinita associata alla cassa, se nota; lascia la categoria vuota. Prima di un inserimento reale mostra un riepilogo e chiede conferma.

- Con `DRY_RUN=true`, il portale non salva la spesa.
- Con `DRY_RUN=false`, il pulsante finale inserisce davvero la spesa nel portale.
- Usa `/annulla` per interrompere prima della conferma finale.
- Durante le operazioni lente il messaggio diventa «⏳ …» e i pulsanti spariscono. Se tocchi di nuovo un pulsante vecchio, il bot lo ignora e mostra «Questa scelta non è più attiva».
- Se il portale non risponde mentre il bot legge casse o tipologie, la prova manuale viene annullata in automatico, senza inserire nulla: puoi ripartire subito con `/testspesa`. Per uno scontrino, invece, il file resta salvato e il bot propone **Conferma** per riprovare oppure **Annulla**.
- Un nuovo `/testspesa` annulla da solo una prova manuale rimasta aperta.
- Se una conversazione iniziata prima di un aggiornamento mostra ancora i vecchi pulsanti per unità o categoria, usa `/annulla` e ricomincia con `/testspesa`.

Per la prima prova usa una descrizione chiaramente riconoscibile e controlla con attenzione anno, conto, direzione, tipologia, unità predefinita e importo. Non condividere i dati della spesa in chat con altre persone.

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

## Repository GitHub privato

Il repository locale è sul ramo `main`. Nessun push viene eseguito automaticamente. Quando vorrai pubblicare i commit nel repository privato, dal PC puoi usare:

```powershell
git remote add origin https://github.com/terranovamt/bilancio-canicattini1.git
git push -u origin main
```

Non aggiungere `.env`, ricevute, database, registrazioni non sanificate o credenziali al repository.
