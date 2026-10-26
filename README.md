# Archivio spese AGESCI via Telegram

Progetto per un bot Telegram privato: riceve una foto o un PDF, corregge e analizza la ricevuta con un modello visivo locale, chiede conferma e conserva il file sul NAS. Le opzioni per la registrazione AGESCI devono essere lette dal portale e mostrate nel bot.

## Stato attuale

Il bot, l'analisi locale, l'archivio, SQLite, la coda dei ritentativi e il client Playwright hanno test automatici. Il client segue l'ordine del portale: anno scout ricavato dalla data, Tesoriere di gruppo, Prima Nota, poi le scelte dinamiche per la spesa. Può salvare se `DRY_RUN=false` e verifica che la riga compaia nella Prima Nota; un salvataggio reale non è ancora stato provato dal bot.

È disponibile anche `bilancio/csv_import.py` per preparare e simulare l'importazione di un CSV di spese con classificazioni AGESCI. La modalità predefinita non salva; l'importazione reale richiede `DRY_RUN=false`, il numero esatto di righe e una conferma digitata. La connessione attuale al portale non ha ancora superato la verifica delle opzioni, quindi nessuna importazione CSV reale è stata eseguita.

La modalità `DRY_RUN=true` è quella predefinita e non salva sul portale. Non impostare `DRY_RUN=false` finché non hai concordato una prova controllata: in quel modo il bot inserisce davvero la spesa. La ricevuta viene archiviata sul NAS e non viene allegata alla registrazione web.

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

L’amministratore può usare `/testspesa` per inserire data, descrizione e importo direttamente in Telegram, senza scontrino e senza AI. Il bot mostra poi le scelte del portale e un riepilogo finale.

- Con `DRY_RUN=true`, il portale non salva la spesa.
- Con `DRY_RUN=false`, il pulsante finale inserisce davvero la spesa nel portale.
- Usa `/annulla` per interrompere prima della conferma finale.

Per la prima prova usa una descrizione chiaramente riconoscibile e controlla con attenzione anno, conto, direzione, voci, tipologia, unità, categoria e importo. Non condividere i dati della spesa in chat con altre persone.

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
