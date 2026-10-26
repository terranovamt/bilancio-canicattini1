# Guida per il Synology DS1522+

Questa guida ti accompagna in due prove, senza fare subito registrazioni vere:

1. avviare il bot e provare `/testspesa` senza scontrino e senza AI, con `DRY_RUN=true`;
2. aggiungere il modello visivo locale e provare una foto, sempre con `DRY_RUN=true`.

In entrambe le prove il bot non deve salvare spese nel portale. Non cambiare `DRY_RUN` in `false`: quello abilita inserimenti reali. L'analisi locale non invia la foto a un servizio AI online; Telegram riceve comunque la foto che gli invii.

## 1. Prepara la cartella del progetto

1. In **File Station**, crea una cartella condivisa chiamata `docker`, se non esiste già.
2. Dentro `docker`, crea `bilancio_canicattini1`.
3. Carica nella cartella l'archivio ZIP del progetto che accompagna questa guida.
4. Estrai l'archivio. Controlla che `docker-compose.yml` e `Dockerfile` siano direttamente dentro `bilancio_canicattini1`, non in una sottocartella aggiuntiva.
5. Crea dentro `bilancio_canicattini1` le cartelle `data`, `scontrini` e `ollama-data`.

Queste cartelle conservano database, ricevute e modello anche quando aggiorni o riavvii il container. Non cancellarle per risolvere un errore.

## 2. Verifica i permessi

Il container usa `PUID` e `PGID` per poter scrivere nelle cartelle del NAS.

1. Se conosci già gli identificativi numerici del tuo utente NAS, annota `PUID` e `PGID`.
2. In File Station verifica che quell'utente possa leggere e scrivere in `data`, `scontrini` e `ollama-data`.
3. Se non conosci gli identificativi o non sai controllare i permessi, chiedi aiuto prima di avviare il progetto. Non mettere numeri casuali.

## 3. Crea le impostazioni private

1. Duplica `.env.example` e rinomina la copia `.env`.
2. Apri `.env` con l'editor di testo di DSM.
3. Inserisci `BOT_TOKEN`, il tuo `ADMIN_USER_ID` e, per la prima prova, il tuo stesso ID Telegram in `ALLOWED_USER_IDS`. Così il bot risponde solo a te.
4. Inserisci `BILANCIO_URL`, `BILANCIO_USER` e `BILANCIO_PASSWORD`: servono perché il bot legga le opzioni disponibili nel portale.
5. Inserisci i valori corretti di `PUID` e `PGID`.
6. Lascia `LOCAL_VISION_MODEL` vuoto per la prima prova senza foto.
7. Lascia `ANTHROPIC_API_KEY` e `ANTHROPIC_MODEL` vuoti: non servono per l'analisi locale.
8. Controlla che `DRY_RUN=true`.
9. Salva `.env` e limita l'accesso alla cartella del progetto agli amministratori NAS.

Non inviare `.env`, token, password o codici di verifica in chat e non caricarlo su GitHub.

## 4. Avvia il progetto per la prova senza foto

1. Apri **Container Manager** e seleziona **Progetto**.
2. Scegli **Crea** e seleziona come percorso `docker/bilancio_canicattini1`.
3. Seleziona `docker-compose.yml` e avvia la creazione del progetto.
4. Attendi che i servizi `ollama` e `bot` risultino avviati. Il modello AI non viene ancora scaricato; il servizio Ollama parte vuoto.
5. Apri i registri del servizio `bot` e verifica che non ci siano errori di configurazione o connessione a Telegram.

Il bot usa connessioni in uscita e non apre porte sul router. Se i registri mostrano errori di certificato HTTPS o di collegamento a Telegram, arresta il progetto e chiedi aiuto: non disattivare la verifica dei certificati. Sul PC la connessione a Telegram è già stata bloccata da un certificato non attendibile; il NAS potrebbe avere lo stesso problema di rete.

## 5. Prova Telegram senza scontrino e senza AI

Questa prova usa le credenziali AGESCI per leggere le opzioni del portale, ma `DRY_RUN=true` impedisce di salvare la spesa.

1. Apri la chat privata con il bot dal tuo account Telegram autorizzato.
2. Invia `/testspesa`.
3. Quando il bot lo chiede, scrivi data, descrizione e importo separati dal simbolo `|`. Per esempio: `26/10/2026 | PROVA SIMULATA | 1,00`.
4. Seleziona le opzioni che il bot mostra: cassa, entrata o uscita, voci, tipologia, unità e categoria.
5. Controlla il riepilogo. Procedi solo se il bot indica chiaramente che la modalità è **SIMULATA**.
6. Conferma la simulazione nel bot. Con `DRY_RUN=true` il client compila il modulo ma non preme il comando di salvataggio.
7. Verifica che il bot confermi che la prova è terminata senza salvare la spesa. Se il portale richiede un accesso manuale o un codice di verifica, segui solo le istruzioni mostrate dal bot; non inviare il codice in chat.

Per interrompere prima della conferma, invia `/annulla`. Se il portale non si apre o le opzioni non compaiono, fermati e conserva i registri nel NAS; non provare a inserire la spesa manualmente.

## 6. Aggiungi il modello visivo locale

Fai questo passaggio solo dopo che il test senza foto è riuscito. Il DS1522+ è CPU-only e nel progetto risultano 8 GB di RAM: il modello piccolo è il punto di partenza, ma l'analisi può essere lenta. Il modello `qwen3-vl:2b` occupa circa 1,9 GB da scaricare. L'immagine Ollama già impostata nel progetto è la versione `0.40.11`, compatibile con Qwen3-VL, che richiede Ollama `0.12.7` o successivo. [Pagina ufficiale del modello](https://ollama.com/library/qwen3-vl).

1. In **File Station**, riapri `.env`.
2. Imposta `LOCAL_VISION_MODEL=qwen3-vl:2b`. Lascia invariati `LOCAL_VISION_URL` e `DRY_RUN=true`.
3. In **Container Manager**, apri il container `ollama` e seleziona **Terminale**.
4. Nel terminale esegui `ollama pull qwen3-vl:2b` e attendi che il download finisca. Non chiudere il terminale durante il download.
5. Se il download termina con un errore di rete o certificato, fermati: non disattivare le protezioni e non sostituire il modello con un servizio online.
6. Torna a **Progetto** e riavvia il servizio `bot` per fargli leggere il nuovo valore di `.env`.
7. Controlla i registri del bot e verifica che si avvii senza errori.

Il download del modello usa Internet; una volta installato, l'analisi delle immagini avviene sul NAS. Nessuna chiave Anthropic è necessaria.

## 7. Prova una foto in modalità simulata

1. Lascia `DRY_RUN=true`.
2. Invia al bot una foto di prova come immagine, non come file PDF. Evita di condividere qui la foto o dati personali che contiene.
3. Segui le domande del bot e controlla con attenzione i dati estratti. Correggi quelli sbagliati prima di procedere.
4. Controlla che il riepilogo indichi la modalità **SIMULATA** e conferma la simulazione.
5. Verifica la risposta finale del bot e che la foto sia stata archiviata nella cartella `scontrini`, organizzata per anno e mese.

La ricevuta resta sul NAS; non viene allegata all'operazione del portale. Non cambiare `DRY_RUN` in `false` durante queste prove. Un'eventuale registrazione reale va concordata e verificata separatamente prima di abilitarla.

## 8. Aggiornamenti e copie di sicurezza

1. Prima di aggiornare, arresta il progetto da Container Manager.
2. Conserva una copia delle cartelle `data` e `scontrini`; conserva anche `ollama-data` per non dover riscaricare il modello.
3. Aggiorna i file del programma senza sostituire `.env`.
4. Ricrea o riavvia il progetto e controlla i registri.
5. Non cancellare il database per risolvere un problema: contiene stati e hash usati per evitare doppie registrazioni.

Non caricare mai `.env`, database, ricevute o registrazioni non sanificate su GitHub.
