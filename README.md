# Archivio spese AGESCI via Telegram

Progetto per un bot Telegram privato: riceve una foto o un PDF, corregge e analizza la ricevuta con un modello visivo locale, chiede conferma e conserva il file sul NAS. Le opzioni per la registrazione AGESCI devono essere lette dal portale e mostrate nel bot.

## Stato attuale

Il bot, l'analisi locale, l'archivio, SQLite, la coda dei ritentativi e i file Docker sono in sviluppo e hanno test automatici. **Non usare ancora il bot per registrare spese reali:** il collegamento Playwright al portale e i relativi selettori non sono stati completati e verificati. Le registrazioni disponibili non descrivono ancora in modo affidabile tutte le operazioni di uscita.

La modalità `DRY_RUN=true` è quella predefinita. Non modificarla per provare il portale: prima serve completare e verificare il client AGESCI. La ricevuta viene archiviata sul NAS e non viene allegata alla registrazione web.

L'analisi delle ricevute è prevista in locale con Ollama. Non è configurato l'invio delle immagini a un servizio AI online. Il modello locale va scelto dopo aver verificato la memoria e le prestazioni disponibili sul NAS.

## Componenti

- `bot/`: comandi Telegram, accesso autorizzato, flusso di conferma e ritentativi.
- `vision/`: correzione locale dell'immagine, schema dei dati e client Ollama.
- `storage/`: conservazione temporanea e archivio permanente delle ricevute.
- `db/`: spese, stati, duplicati e coda SQLite.
- `bilancio/`: interfaccia del portale; il client Playwright concreto è ancora da completare.
- `Dockerfile` e `docker-compose.yml`: esecuzione prevista su Synology.
- `docs/GUIDA_SYNOLOGY.md`: istruzioni per preparare il NAS.
- `docs/CONTESTO_PROGETTO.md`: decisioni e stato dettagliato del lavoro.

## Configurazione privata

1. Crea `.env` copiando `.env.example`.
2. Inserisci lì token, ID autorizzati e credenziali: non inviarli in chat e non inserirli in GitHub.
3. Lascia vuoto `LOCAL_VISION_MODEL` finché non è stato scelto un modello compatibile con il NAS.
4. Mantieni `DRY_RUN=true`.

Il file `.env`, le ricevute, il database e i modelli locali sono esclusi sia da Git sia dal contesto di creazione dell'immagine Docker.

## Verifiche sul PC

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
