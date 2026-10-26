# Guida per il Synology DS1522+

Questa guida descrive la preparazione del NAS con DSM 7.2 e Container Manager. Il client Playwright è presente e legge le opzioni osservate nel portale, ma può soltanto compilare il modulo in modalità di prova. **Il bot non è ancora pronto per inserire spese reali:** il salvataggio resta disabilitato finché non sarà verificato come rileggere e riconoscere con certezza una registrazione completata. Non disattivare `DRY_RUN` e non provare il salvataggio di una spesa.

## 1. Prepara la cartella del progetto

1. In DSM apri **File Station**.
2. Crea `docker/bilancio_canicattini1` nel volume di archiviazione che vuoi usare.
3. Dentro crea le cartelle `data`, `scontrini` e `ollama-data`.
4. Copia in `docker/bilancio_canicattini1` i file del progetto, compresi `Dockerfile` e `docker-compose.yml`.
5. Non copiare `.env`, ricevute, database o registrazioni del portale da altri dispositivi. Crea un `.env` nuovo direttamente sul NAS, come spiegato più avanti.

Le tre cartelle corrispondono ai volumi permanenti del database e dei dati del bot, delle ricevute e dei modelli locali. Non cancellarle durante gli aggiornamenti.

## 2. Imposta i permessi delle cartelle

Il container usa gli identificativi `PUID` e `PGID` impostati in `.env` per poter scrivere nei volumi. Devono corrispondere all'utente NAS proprietario delle cartelle.

1. Se sai già quali sono gli identificativi numerici del tuo utente NAS, annota `PUID` e `PGID`.
2. Se non li conosci, chiedi aiuto prima di avviare il progetto. Non impostare valori casuali: senza i permessi corretti il bot non potrà archiviare i file.
3. In File Station verifica che quell'utente possa leggere e scrivere nelle cartelle `data`, `scontrini` e `ollama-data`.

## 3. Crea il file delle impostazioni

1. Duplica `.env.example` nella cartella del progetto sul NAS e rinomina la copia `.env`.
2. Apri `.env` con l'editor di testo DSM.
3. Inserisci `BOT_TOKEN`, `ALLOWED_USER_IDS` e `ADMIN_USER_ID` senza spazi superflui. Per più utenti, separa gli ID con una virgola.
4. Lascia `LOCAL_VISION_MODEL` vuoto finché non avrai controllato la memoria del NAS e scelto un modello adatto.
5. Lascia `DRY_RUN=true`.
6. Inserisci `PUID` e `PGID` con i valori corretti dell'utente NAS.
7. Lascia vuote le impostazioni AGESCI se non sono ancora state configurate.
8. Salva il file e limita l'accesso alla cartella del progetto agli amministratori NAS.

Non condividere il file `.env`, non inserirlo in GitHub e non inviare token o password in chat.

## 4. Controlla la memoria prima del modello locale

1. In DSM controlla la memoria installata e quella disponibile.
2. Controlla anche lo spazio libero nel volume dove conserverai `ollama-data`.
3. Comunica questi valori prima di scegliere o scaricare un modello visivo.

Non scaricare ancora un modello a caso: il DS1522+ non ha una scheda grafica dedicata e modello, memoria e tempi di risposta vanno valutati insieme.

## 5. Crea il progetto in Container Manager

Esegui questi passaggi solo dopo aver verificato la memoria del NAS, scelto un modello locale e completato un controllo in modalità di prova:

1. Apri **Container Manager** e vai in **Progetto**.
2. Scegli **Crea**.
3. Seleziona come percorso `docker/bilancio_canicattini1`.
4. Seleziona il file `docker-compose.yml`.
5. Avvia la creazione e attendi il download delle immagini e la costruzione del bot. La prima attesa può essere lunga.
6. Avvia il progetto.

Compose avvia Ollama e il bot, non apre porte di rete in ingresso e mantiene i dati nelle cartelle create al punto 1. Chromium ha 1 GB di memoria condivisa. Il servizio bot riparte dopo un riavvio del NAS.

## 6. Controlla lo stato

1. In Container Manager controlla che il progetto e i servizi risultino avviati.
2. Apri i registri del servizio `bot` se qualcosa non parte. I registri sono in formato leggibile e salvati anche in `data/logs`, con rotazione automatica.
3. Non copiare in chat registri che potrebbero contenere dati personali.
4. Quando il modello locale sarà configurato, prova prima solo la ricezione e l'analisi della ricevuta.
5. Non fare prove di salvataggio sul portale: il client al momento non esegue il comando Salva. Prima occorre definire e provare la verifica del risultato con una procedura autorizzata.

## 7. Aggiornamenti e copie di sicurezza

1. Prima di aggiornare, arresta il progetto da Container Manager.
2. Conserva una copia delle cartelle `data` e `scontrini`; conserva anche `ollama-data` se non vuoi riscaricare il modello.
3. Aggiorna solo i file del programma, senza sostituire `.env`.
4. Ricrea/avvia il progetto e controlla i registri.
5. Non cancellare il database per risolvere un problema: contiene gli stati e gli hash usati per evitare doppie registrazioni.

Per pubblicare il codice sul repository GitHub privato, segui le istruzioni nel `README.md` dal PC. Non caricare mai `.env`, dati o ricevute.
