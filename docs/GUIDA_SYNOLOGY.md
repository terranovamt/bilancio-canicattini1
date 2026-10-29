# Installazione passo passo sul Synology DS1522+

Questa guida parte da un PC Windows e da un NAS con DSM 7.2. I nomi dei pulsanti possono cambiare leggermente secondo la versione di DSM.

Lo scopo iniziale è avviare il bot e provare una spesa simulata. Solo dopo si installa il modello per leggere le foto.

**Durante tutta questa guida lascia `DRY_RUN=true`: il bot può leggere e compilare il portale AGESCI, ma non deve salvare spese reali.** Un'eventuale prova reale va autorizzata separatamente.

## 1. Che cosa stiamo installando

- **NAS**: il Synology, dove resteranno programma e documenti.
- **DSM**: la pagina di amministrazione del Synology che apri nel browser.
- **Container Manager**: l'applicazione Synology che esegue il programma in ambienti separati chiamati container.
- **Servizio `bot`**: il programma che risponde su Telegram e usa il browser per AGESCI.
- **Servizio `ollama`**: il programma locale che eseguirà il modello per capire le foto.
- **File `.env`**: le impostazioni private, compresi token e password. Non va condiviso.

Le tre cartelle da creare sono normali cartelle vuote, non programmi da installare:

| Cartella | Che cosa conterrà |
| --- | --- |
| `data` | Database delle operazioni, stato degli accessi al portale, file temporanei e registri degli errori. |
| `scontrini` | Foto e PDF archiviati, organizzati per anno e mese. |
| `ollama-data` | File del modello AI, così non devi riscaricarli dopo ogni riavvio. |

Il programma le riempirà da solo. Si trovano fuori dai container: i contenuti restano anche quando ricrei il programma. **Non cancellarle per risolvere un errore.** Anche i dati delle prove possono restare sul NAS.

## 2. Preparazione guidata, anche se non conosci rete e credenziali

Non devi già saper fare queste operazioni. Segui le sottosezioni in ordine. Per ogni passaggio è indicato dove agire e che risultato aspettarti.

Non inviare token, password, codici di verifica o ricevute in questa chat. Non serve GitHub per questa installazione. Non servono Python, Chrome o Ollama installati direttamente in DSM.

### 2.1. Controlla che il NAS sia acceso

1. Guarda il Synology: se è acceso, la spia di alimentazione deve essere accesa.
2. Se è spento, premi brevemente il pulsante di accensione una sola volta.
3. Attendi che finisca l'avvio: possono servire alcuni minuti.
4. Controlla che un cavo di rete colleghi una porta LAN del NAS al router o a uno switch collegato al router.

Non tenere premuto il pulsante: potrebbe provocare uno spegnimento. Non scollegare cavi di un impianto che non conosci. Se il NAS emette segnali di errore o mostra avvisi sui dischi, fai controllare prima il dispositivo.

### 2.2. Collega il PC alla stessa rete del NAS

Non devi collegare il PC direttamente al NAS con un cavo. PC e NAS devono raggiungere lo stesso router.

**Se il PC usa il Wi-Fi:**

1. In Windows fai clic sulle icone di rete e volume in basso a destra, vicino all'orologio.
2. Apri l'elenco delle reti Wi-Fi.
3. Seleziona il Wi-Fi del router a cui è collegato il NAS.
4. Premi **Connetti**, se il PC non è già collegato.
5. Se viene richiesta, inserisci la password del Wi-Fi, non quella del NAS.

**Se il PC usa un cavo di rete:**

1. Controlla che il cavo del PC arrivi allo stesso router del NAS, anche attraverso uno switch.
2. Lascia attivo quel collegamento: non occorre anche il Wi-Fi.

Evita la rete chiamata **Guest/Ospiti**: spesso impedisce di raggiungere gli altri dispositivi. Se il NAS è in un'altra sede, fermati: questa guida parte da un collegamento locale e non richiede di aprire porte su Internet.

**Risultato atteso:** PC e NAS sono collegati alla stessa rete. Lo verificherai aprendo DSM nella sottosezione seguente.

### 2.3. Trova e apri la pagina del Synology

**Sul PC:**

1. Apri Edge, Chrome o Firefox.
2. Se hai già un preferito per il Synology, aprilo.
3. Se non hai un preferito, scrivi `https://find.synology.com` nella barra degli indirizzi in alto e premi Invio. È il servizio ufficiale Synology per trovare il NAS nella rete locale.
4. Attendi la ricerca.
5. Se compare il tuo NAS, verifica che modello e nome corrispondano al dispositivo che vuoi usare.
6. Premi il comando di collegamento proposto.
7. Quando compare la pagina di accesso DSM, salvala nei preferiti del browser.

Se la ricerca non trova il NAS, controlla di non usare il Wi-Fi ospiti. Se non riesci comunque a trovarlo, chiedi all'amministratore del NAS il suo indirizzo locale: non cambiare impostazioni di rete a tentativi.

Se il browser mostra un avviso sul certificato, non disattivare le protezioni e non proseguire automaticamente: fai verificare indirizzo e certificato all'amministratore.

**Risultato atteso:** vedi una schermata di accesso Synology DSM, non la pagina di configurazione del router.

### 2.4. Accedi con l'account NAS corretto

1. Nella pagina DSM inserisci il nome utente del NAS.
2. Inserisci la password di quell'account.
3. Completa l'eventuale verifica a due fattori sul tuo dispositivo.
4. Dopo l'accesso, apri il menu principale DSM, normalmente in alto a sinistra.
5. Cerca **Centro pacchetti** e **Pannello di controllo**.

L'account NAS non è necessariamente quello di Windows, Telegram o AGESCI. Se non conosci le credenziali, chiedile alla persona che gestisce il NAS. Non creare un nuovo account amministratore e non usare il pulsante fisico RESET per recuperarle.

Per installare i pacchetti e usare i comandi successivi serve un account del gruppo amministratori. Se compare **Permesso negato** o non puoi installare pacchetti, chiedi all'amministratore di eseguire quelle operazioni.

**Risultato atteso:** sei dentro DSM e hai un account autorizzato a gestire i pacchetti.

### 2.5. Trova lo ZIP sul PC

Il pacchetto preparato si chiama `bilancio-canicattini1-synology-guida-completa.zip`.

1. Torna nella finestra di VS Code dove stai leggendo questa conversazione.
2. Apri il collegamento al pacchetto ZIP fornito nella conversazione.
3. Se il collegamento non apre la cartella, premi **Windows + E** per aprire Esplora file.
4. Incolla nella barra degli indirizzi di Esplora file questo percorso e premi Invio:

```text
C:\Users\terranom\.copilot\session-state\e2c63f0e-149f-4552-a091-0e4d09d44e3e\files
```

5. Cerca `bilancio-canicattini1-synology-guida-completa.zip`.
6. Selezionalo e premi **Ctrl+C** per copiarlo.
7. Apri la cartella **Download** dalla colonna sinistra di Esplora file.
8. Premi **Ctrl+V** per incollarne una copia.

Non estrarlo sul PC per questa procedura: lo caricherai ancora compresso sul NAS. Il percorso sopra vale per questo PC; su un altro computer devi prima trasferire lo ZIP, non copiare il testo del percorso.

**Risultato atteso:** vedi lo ZIP nella cartella Download e sai dove selezionarlo quando File Station chiederà il file da caricare.

### 2.6. Recupera token e ID Telegram già configurati

Nel progetto sul PC esiste già un file privato `.env`. Puoi leggere tu i valori da quel file senza generarne di nuovi.

1. Sul PC premi **Windows + E**.
2. Incolla nella barra degli indirizzi il percorso seguente e premi Invio:

```text
C:\Users\terranom\Prj\bilancio_canicattini1
```

3. In Esplora file attiva **Visualizza → Mostra → Estensioni nomi file**, se non è già attivo. Su Windows 10 la voce è nella scheda **Visualizza**.
4. Cerca il file chiamato esattamente `.env`, non `.env.example`.
5. Fai clic destro su `.env` e scegli **Apri con → Blocco note**. Se Windows chiede un'applicazione, seleziona Blocco note senza cambiare il nome del file.
6. Cerca la riga `BOT_TOKEN=`: il testo dopo `=` è il token da riutilizzare sul NAS.
7. Cerca `ADMIN_USER_ID=`: il numero dopo `=` è l'ID Telegram amministratore già configurato.
8. Controlla che lo stesso numero compaia anche in `ALLOWED_USER_IDS=`.
9. Lascia il file aperto soltanto sul tuo PC, per copiare i valori quando arriverai alla sezione 9.

Il token è una chiave segreta, non il nome del bot. L'ID Telegram è un numero, non il numero di telefono e non il nome che inizia con `@`.

**Se `BOT_TOKEN` è vuoto:**

1. Apri Telegram con l'account che ha creato il bot.
2. Cerca il bot ufficiale verificato **@BotFather**.
3. Invia `/mybots`.
4. Seleziona il bot del progetto.
5. Apri la voce **API Token**, se presente, per consultare il token.

Non revocare o rigenerare il token per questa installazione: cambierebbe la chiave usata anche dalle copie già configurate. Se il bot non compare, controlla di usare l'account Telegram del proprietario.

**Se l'ID numerico manca:** cerca prima la conversazione o l'appunto privato dove lo avevi già recuperato. Se non lo trovi, fermati alla configurazione e segnala soltanto che manca l'ID; non inserire il numero di telefono o un valore inventato e non inoltrare token a bot di terze parti.

**Risultato atteso:** hai trovato il token e il tuo ID numerico, senza condividerli in chat e senza modificare il file privato del PC.

### 2.7. Verifica le credenziali AGESCI senza inserire spese

1. Sul PC apri una nuova scheda del browser.
2. Vai a `https://bilancio.agesci.it/login`.
3. Accedi con il tuo account AGESCI.
4. Verifica soltanto che riesci a entrare.
5. Non aprire né salvare una nuova operazione.

Se nome utente o password sono già compilati nel file privato del PC, puoi consultarli lì. Se il browser accede automaticamente e non ricordi la password, usa il gestore password del tuo browser oppure il recupero ufficiale del portale. Non inviarmi le credenziali.

**Risultato atteso:** sai quali credenziali userai nella sezione 9 e hai verificato che permettono l'accesso.

### 2.8. Verifica un primo collegamento Internet dal NAS

1. Torna alla scheda del browser con DSM.
2. Apri **Centro pacchetti** dal menu principale.
3. Apri l'elenco dei pacchetti disponibili.
4. Cerca **Container Manager**.

Se l'elenco si carica e trovi il pacchetto, il NAS riesce almeno a contattare il catalogo Synology. Questo non garantisce ancora l'accesso a Telegram, al registro delle immagini Docker o al download del modello: li verificheremo nei rispettivi passaggi.

Se compare un errore di rete o certificato, annotalo e fermati prima dell'installazione. Non modificare DNS, proxy o firewall e non disattivare la verifica dei certificati a tentativi.

**Prima di passare alla sezione 3:** devi poter aprire DSM, avere un account amministratore, trovare lo ZIP sul PC e sapere dove recuperare le impostazioni private. Se manca qualcosa, torna alla sottosezione corrispondente; non procedere con valori inventati.

## 3. Sul NAS: installa Container Manager e l'editor

1. Dal PC apri nel browser l'indirizzo DSM che usi normalmente.
2. Accedi con il tuo account amministratore NAS.
3. Apri **Centro pacchetti**.
4. Cerca **Container Manager**.
5. Premi **Installa**, se non è già installato.
6. Attendi che l'installazione termini.
7. Cerca **Text Editor** o **Editor di testo** nel Centro pacchetti.
8. Installalo, se disponibile e non già presente: servirà per modificare le impostazioni.

Se Container Manager non compare o non si installa, fermati e annota modello del NAS, versione DSM ed errore. Non installare pacchetti da siti sconosciuti.

## 4. Dal PC al NAS: carica ed estrai lo ZIP

Lo ZIP preparato contiene già una cartella principale chiamata `bilancio_canicattini1`. Per evitare una cartella doppia, caricalo nella cartella `docker`, non dentro un'altra `bilancio_canicattini1`.

1. In DSM apri **File Station** dal menu principale.
2. Cerca nella colonna sinistra la cartella condivisa `docker`.
3. Se esiste, aprila e passa al punto 7.
4. Se non esiste, apri **Pannello di controllo → Cartella condivisa → Crea**.
5. Crea una cartella condivisa chiamata `docker`, scegliendo il volume del NAS dove vuoi conservare il progetto.
6. Torna in File Station e apri `docker`.
7. Premi **Carica** e seleziona sul PC lo ZIP del progetto.
8. Attendi che il caricamento finisca.
9. Fai clic destro sullo ZIP e scegli **Estrai → Estrai qui**, o la voce equivalente.
10. Apri la cartella `bilancio_canicattini1` ottenuta.
11. Controlla che al suo interno si vedano `Dockerfile`, `docker-compose.yml`, `requirements.txt` e le cartelle `bot`, `bilancio`, `db`, `storage`, `vision`.

Non usare la copia del progetto già presente sul PC con database, ricevute o cartelle locali. Il pacchetto preparato non contiene le impostazioni private: le inserirai sul NAS.

Se trovi `bilancio_canicattini1` dentro un'altra `bilancio_canicattini1`, il percorso giusto è quello più interno che contiene `docker-compose.yml`. Non avviare il progetto dalla cartella esterna.

## 5. Sul NAS: crea le tre cartelle vuote

1. In File Station entra nella cartella che contiene `docker-compose.yml`.
2. Premi **Crea → Crea cartella**.
3. Scrivi esattamente `data` e conferma.
4. Premi nuovamente **Crea → Crea cartella**.
5. Scrivi esattamente `scontrini` e conferma.
6. Premi nuovamente **Crea → Crea cartella**.
7. Scrivi esattamente `ollama-data` e conferma.

Non creare file con questi nomi: devono essere cartelle. Non devi inserire nulla al loro interno.

Alla fine, nella stessa finestra devono comparire almeno:

```text
bilancio_canicattini1
├── Dockerfile
├── docker-compose.yml
├── .dockerignore
├── .env.example
├── requirements.txt
├── config.py
├── logging_config.py
├── bot
├── bilancio
├── db
├── storage
├── vision
├── data
├── scontrini
└── ollama-data
```

## 6. Dal PC: apri un collegamento al terminale del NAS

Useremo un collegamento SSH solo dalla rete locale. Serve per conoscere i permessi corretti e avviare il progetto senza dipendere dai nomi dei pulsanti di Container Manager.

1. In DSM apri **Pannello di controllo → Terminale e SNMP → Terminale**.
2. Annota se SSH era già abilitato e quale porta usava.
3. Abilita **Servizio SSH**, se era disabilitato.
4. Annota la porta indicata; normalmente è `22`.
5. Premi **Applica**.
6. In **Pannello di controllo → Rete → Interfaccia di rete**, trova l'indirizzo IP locale del NAS.
7. Sul PC apri il menu Start, cerca **PowerShell** e aprilo.
8. Scrivi il comando seguente, sostituendo `NOME_UTENTE_NAS` e `IP_DEL_NAS` con i tuoi valori:

```powershell
ssh NOME_UTENTE_NAS@IP_DEL_NAS
```

9. Se la porta SSH non è 22, usa invece `ssh -p PORTA NOME_UTENTE_NAS@IP_DEL_NAS`.
10. Alla prima connessione verifica che l'indirizzo sia quello del tuo NAS; accetta la chiave solo se sei certo di collegarti al dispositivo corretto.
11. Quando compare `password`, digita la password dell'account NAS e premi Invio. Mentre scrivi non vedrai lettere o asterischi: è normale.

Ora i comandi in quella finestra vengono eseguiti sul NAS, non sul PC.

Non aprire la porta SSH sul router. Se PowerShell non riconosce `ssh`, oppure compare un avviso di chiave cambiata, fermati e chiedi assistenza senza inviare password. Non cancellare chiavi o disattivare controlli per proseguire.

## 7. Sul NAS: recupera identificativi e percorso

1. Nella finestra SSH scrivi:

```sh
id
```

2. Nell'output cerca `uid=NUMERO` e `gid=NUMERO`.
3. Annota il numero dopo `uid=` come **PUID**.
4. Annota il numero dopo `gid=` come **PGID**.
5. In File Station fai clic destro sulla cartella del progetto e apri **Proprietà**.
6. Annota il percorso completo della cartella; per esempio potrebbe essere `/volume1/docker/bilancio_canicattini1`.

Il volume può essere diverso: usa il tuo percorso, non copiare automaticamente l'esempio.

PUID e PGID non sono l'ID Telegram. Sono i numeri con cui il NAS identifica l'utente e il gruppo che useranno il programma.

## 8. Sul NAS: verifica i permessi delle cartelle

1. In File Station apri le **Proprietà** della cartella del progetto.
2. Nella sezione **Permessi**, verifica che l'account NAS usato per SSH abbia lettura e scrittura.
3. Verifica gli stessi permessi su `data` e `scontrini`.
4. Limita l'accesso al progetto agli amministratori che devono gestirlo, perché conterrà credenziali e documenti.

Se i permessi sono ereditati dalla cartella condivisa, può essere necessario controllarli in **Pannello di controllo → Cartella condivisa → docker → Modifica → Permessi**. Non rimuovere permessi di altri progetti e non concedere accesso a tutti. Se il NAS è condiviso con altre persone, chiedi all'amministratore prima di modificarli.

Il servizio `bot` usa PUID e PGID per scrivere in `data` e `scontrini`. Il servizio Ollama usa l'utente previsto dalla propria immagine e scrive separatamente in `ollama-data`: non forzare permessi universali per aggirare un errore.

## 9. Sul NAS: crea il file privato `.env`

1. In File Station, dentro la cartella del progetto, seleziona `.env.example`.
2. Copialo e incollalo nella stessa cartella.
3. Rinomina solo la copia in `.env`, compreso il punto iniziale.
4. Apri `.env` con l'editor di testo.
5. Compila i valori indicati nella tabella.

| Impostazione | Cosa inserire per la prima prova |
| --- | --- |
| `BOT_TOKEN` | Il token del tuo bot, ottenuto da BotFather. |
| `ALLOWED_USER_IDS` | Il tuo ID numerico Telegram. Per ora solo il tuo. |
| `ADMIN_USER_ID` | Lo stesso ID numerico Telegram. |
| `BILANCIO_URL` | `https://bilancio.agesci.it/login` |
| `BILANCIO_USER` | Il nome utente del portale AGESCI. |
| `BILANCIO_PASSWORD` | La password del portale AGESCI. |
| `PUID` | Il numero `uid` annotato al passaggio precedente. |
| `PGID` | Il numero `gid` annotato al passaggio precedente. |

6. Controlla che le altre righe restino così:

```dotenv
ANTHROPIC_API_KEY=
ANTHROPIC_MODEL=
PLAYWRIGHT_BROWSER_CHANNEL=
LOCAL_VISION_URL=http://ollama:11434
LOCAL_VISION_MODEL=
DATA_PATH=/data
STORAGE_PATH=/scontrini
TZ=Europe/Rome
DRY_RUN=true
```

7. Salva il file.
8. Controlla che il nome sia proprio `.env`, non `.env.txt`.

Le righe `/data` e `/scontrini` sono percorsi interni al container: non sostituirli con il percorso del volume NAS.

Se l'editor DSM non è disponibile, puoi modificare una copia di `.env.example` sul PC con Blocco note e caricare sul NAS il file risultante. Usa **Salva con nome → Tutti i file**, nome `.env`, codifica UTF-8. Non condividere quella copia privata.

Non mostrare screenshot di questo file. Se una password contiene `$`, `#`, virgolette o spazi, chiedi assistenza sulla sintassi senza comunicare la password: quei caratteri possono richiedere una gestione specifica nel formato `.env`.

## 10. Sul NAS: avvia il progetto

Prima di avviare, ferma eventuali copie dello stesso bot aperte sul PC: due programmi con lo stesso token possono entrare in conflitto.

1. Torna nella finestra SSH.
2. Entra nella cartella del progetto con `cd` seguito dal percorso annotato. Esempio, valido solo se il tuo percorso coincide:

```sh
cd /volume1/docker/bilancio_canicattini1
```

3. Verifica che Docker Compose sia disponibile:

```sh
sudo docker compose version
```

4. Se viene chiesta la password, inserisci quella del tuo account NAS. Anche qui i caratteri non compaiono.
5. Se compare una versione di Compose, avvia:

```sh
sudo docker compose up -d --build
```

6. Attendi il ritorno del cursore: al primo avvio vengono scaricate immagini e dipendenze e possono servire diversi minuti.
7. Controlla lo stato:

```sh
sudo docker compose ps
```

8. Controlla i registri del bot:

```sh
sudo docker compose logs --tail=80 bot
```

Questi comandi sono per il terminale del NAS. Non eseguirli in una seconda finestra PowerShell non collegata via SSH.

`-d` lascia il programma acceso sul NAS anche dopo che chiudi il terminale. Le cartelle persistenti rimangono nel progetto. Non serve aprire porte sul router e non devi configurare Web Station.

Se `docker compose` non è disponibile, fermati e comunica versione di DSM e Container Manager. Se il download o la costruzione dell'immagine fallisce, conserva il messaggio: la costruzione Docker non è stata verificata sul PC, dove Docker non è installato.

## 11. Controlla che il programma risponda davvero

1. Nel risultato di `docker compose ps`, cerca i servizi `bot` e `ollama`.
2. Attendi che risultino in esecuzione; il controllo di salute può richiedere qualche minuto.
3. Se compaiono `Exited`, `Restarting` o `unhealthy`, non proseguire con le spese: consulta la sezione errori.
4. Sul telefono apri Telegram.
5. Cerca il tuo bot `@bilanciocanicattini1_bot`.
6. Apri la chat privata dal tuo account autorizzato.
7. Invia `/start`.
8. Verifica che arrivi il messaggio di benvenuto.
9. Invia `/help`.
10. Verifica che compaia l'elenco dei comandi.

Con la versione aggiornata, `/start` mostra anche una tastiera con scorciatoie e l'indicazione SIMULATA/REALE. Scrivendo `/` nel campo del messaggio, Telegram propone i comandi. L'amministratore deve inviare `/start` dopo l'aggiornamento per registrare i suggerimenti dei comandi riservati nella propria chat. Se non compaiono subito, chiudi e riapri la chat.

Un container acceso non basta: la risposta Telegram conferma anche che il bot riesce a collegarsi e che il tuo ID è autorizzato. Il controllo di salute del container `bot` verifica il database, non l'accesso a Telegram o ad AGESCI.

## 12. Su Telegram: inserisci una spesa a mano (solo amministratore)

`/testspesa` è il modo per l'amministratore di inserire a mano una spesa, senza foto e senza AI. **Salva davvero nel portale**, anche se `DRY_RUN=true`: quell'impostazione vale solo per gli scontrini. Prima del salvataggio il bot chiede sempre un'ultima conferma.

1. Invia `/testspesa`.
2. Leggi il riepilogo proposto dal bot: data di oggi, descrizione «Prova Telegram» e importo 0,10 €.
3. Cambia i valori con **📅 Data**, **💶 Importo** o **✏️ Descrizione** e invia il nuovo valore.
4. Premi **✅ Conferma**.
5. Scegli la cassa.
6. Scegli entrata o uscita.
7. Scegli la tipologia: compaiono prima le più usate; premi **➕ Altre tipologie** se quella giusta non c'è. Unità e categoria vengono impostate dal bot.
8. Leggi il riepilogo completo e verifica data, anno scout, descrizione, importo, cassa e tipologia.
9. Premi **✅ CONFERMA INSERIMENTO REALE** solo se tutto è corretto; altrimenti premi **Annulla**.
10. Attendi l'esito: il messaggio «⏳ Registro l'operazione nel portale…» viene sostituito da «✅ Operazione registrata e verificata», con il riepilogo di tutti i dati inseriti (data, descrizione, importo, cassa, operazione, tipologia, unità e categoria).

Se compare «❌ Non sono riuscito a registrare e verificare la spesa manuale», il bot non riprova da solo: apri il portale e controlla se la spesa c'è. Ripeti `/testspesa` solo se non compare, per evitare doppioni.

Puoi interrompere prima della conferma finale con `/annulla`.

### Tempi dei menu e aggiornamento delle opzioni

Il bot legge casse e tipologie dal portale in sottofondo dopo l'avvio e ogni notte, e le riutilizza per 26 ore senza riaprire il browser per quei menu. Le opzioni non sono inventate o fissate nel codice: provengono dal portale e sono separate per anno scout, cassa, direzione e scelte precedenti.

1. Se le opzioni sono cambiate nel portale, invia `/aggiornaopzioni` dalla chat amministratore.
2. Attendi il messaggio «✅ Opzioni aggiornate» con il numero di casse e di tipologie lette. Se compare «❌», riprova più tardi.
3. Se avevi già aperto una selezione, invia `/annulla` e ricomincia con `/testspesa` o con una nuova ricevuta.

Per eliminare dal bot operazioni rimaste in coda o elenchi non più utili, l'amministratore può usare `/pulisci`: chiede conferma e non cancella nulla nel portale né le ricevute archiviate.

Un riavvio del bot svuota la cache in memoria. Questa modifica non rende istantaneo l'inserimento: compilazione, controllo dei duplicati e verifica del risultato devono ancora usare il portale.

Se è richiesto un accesso manuale o un codice di verifica, fermati e segnala soltanto il tipo di richiesta: questa guida non configura una schermata browser interattiva sul NAS. Non inviare codici in chat.

## 13. Solo dopo: scarica il modello per leggere le foto

Il DS1522+ lavora qui con la CPU, non con una GPU. Con gli 8 GB di RAM dichiarati, `qwen3-vl:2b` è il candidato iniziale, non una garanzia di prestazioni: l'analisi potrebbe essere lenta o non avere memoria sufficiente. Il download richiede circa 1,9 GB, oltre allo spazio delle immagini Docker.

1. Torna nel terminale SSH, sempre nella cartella del progetto.
2. Scarica il modello:

```sh
sudo docker compose exec ollama ollama pull qwen3-vl:2b
```

3. Attendi la conclusione del download senza chiudere il terminale.
4. Controlla i modelli installati:

```sh
sudo docker compose exec ollama ollama list
```

5. Verifica che compaia `qwen3-vl:2b`.
6. In File Station riapri il file `.env`.
7. Cambia soltanto `LOCAL_VISION_MODEL=` in `LOCAL_VISION_MODEL=qwen3-vl:2b`.
8. Salva il file.
9. Nel terminale SSH ricrea il servizio bot:

```sh
sudo docker compose up -d --force-recreate bot
```

10. Controlla di nuovo stato e registri con i comandi della sezione 10.

**Un semplice riavvio del container non rilegge necessariamente le variabili del file `.env`: per questo usiamo la ricreazione.** La ricreazione non cancella le tre cartelle di dati.

Il download usa Internet, ma il modello analizza poi le immagini sul NAS. Non serve una chiave Anthropic. Telegram riceve comunque la foto che invii nella chat del bot.

## 14. Su Telegram: prova una foto

Se compare «Non sono riuscito a completare il trasferimento», l'analisi AI non è iniziata e non è stata registrata alcuna spesa. Attendi un momento e invia di nuovo il file. Se il problema si ripete, dalla cartella del progetto nel terminale SSH esegui:

```sh
sudo docker compose logs --tail=100 bot | grep -F "Receipt Telegram transfer failed"
```

Condividi soltanto la riga trovata: contiene il passaggio (`get_file`, `download` o `progress_message`) e il tipo di errore, non il contenuto del documento. I tempi massimi di connessione sono 30 secondi e quelli di attesa dei dati per recupero e download sono 120 secondi; sono separati dal timeout del modello AI. Se il log mostra ancora `Unhandled Telegram update error`, il problema potrebbe essere in un altro passaggio oppure il NAS potrebbe usare una versione precedente.

1. Verifica che `.env` contenga ancora `DRY_RUN=true`.
2. Scegli una foto di prova leggibile, evitando documenti con dati personali non necessari.
3. Invia al bot la foto come immagine, non come PDF per questa prima verifica.
4. Attendi l'analisi senza inviare ripetutamente la stessa foto.
5. Controlla data, descrizione e importo estratti.
6. Correggi i dati errati seguendo le domande del bot.
7. Completa le scelte contabili.
8. Verifica che il riepilogo sia **SIMULATO**.
9. Conferma soltanto la simulazione.
10. Dopo la risposta finale, apri `scontrini` in File Station e cerca il documento nella sottocartella dell'anno e del mese.

La ricevuta resta nell'archivio NAS e non viene allegata all'operazione AGESCI. Se il modello va in errore o il NAS diventa troppo lento, fermati: non sostituirlo con un servizio online senza una decisione esplicita.

## 15. Come fermare, riavviare e aggiornare

Esegui questi comandi nel terminale SSH, dalla cartella del progetto.

- Per fermare i due servizi senza cancellare i dati:

```sh
sudo docker compose stop
```

- Per riavviarli senza cambiare la configurazione:

```sh
sudo docker compose start
```

- Dopo modifiche a `.env`, per ricaricare il bot:

```sh
sudo docker compose up -d --force-recreate bot
```

### Aggiornare con un comando, senza scegliere i file uno per uno

**Metodo Git, scelto per gli aggiornamenti successivi:** segui la [guida al deploy Git manuale](DEPLOY_GIT_SYNOLOGY.md) per collegare una sola volta il NAS al repository privato. Dopo la configurazione non serviranno ZIP: il NAS scaricherà e installerà solo quando esegui tu il comando. Un push da solo non attiva alcun aggiornamento. Il metodo ZIP qui sotto rimane un'alternativa.

I nuovi pacchetti includono `aggiorna.sh`. Questo file aggiorna solo il programma, costruisce l'immagine, ricrea solo il bot e controlla la salute del container. Non reinstalla Ollama e non riscarica il modello.

Non serve copiare i file a mano e non serve rifare l'installazione iniziale. Il pacchetto deve provenire da una fonte fidata: lo script esegue il codice che contiene.

#### Una sola volta: prepara la cartella degli aggiornamenti

1. In DSM apri **File Station**.
2. Apri la cartella condivisa `docker`.
3. Premi **Crea → Crea cartella**.
4. Chiamala `aggiornamenti-bilancio`.

Questa cartella deve stare accanto a `bilancio_canicattini1`, non dentro il progetto in esecuzione.

#### Ogni volta che ricevi un nuovo ZIP

1. Attendi che non ci siano operazioni in corso nel bot.
2. Scarica sul PC lo ZIP aggiornato fornito nella conversazione.
3. In File Station apri `docker/aggiornamenti-bilancio`.
4. Carica lo ZIP in quella cartella, sostituendo soltanto lo ZIP precedente se richiesto.
5. Fai clic destro sullo ZIP e scegli **Estrai → Estrai qui**.
6. Se viene chiesto, consenti di sostituire i file gia estratti nella sola cartella degli aggiornamenti. Non estrarre mai sopra il progetto attivo.
7. Apri il collegamento SSH al NAS come descritto nella sezione 6.
8. Esegui il seguente comando **soltanto se il tuo volume e `volume1`**:

```sh
sudo sh /volume1/docker/aggiornamenti-bilancio/bilancio_canicattini1/aggiorna.sh /volume1/docker/bilancio_canicattini1
```

9. Se il tuo progetto usa un altro volume o percorso, sostituisci entrambe le parti del comando con i percorsi effettivi. La prima indica il nuovo script estratto; l'ultima indica il progetto gia installato, che contiene `.env` e `data`.
10. Inserisci la password NAS quando richiesta.
11. Attendi il messaggio **Aggiornamento installato: container acceso e database verificato**.
12. Invia `/start` al bot e verifica che risponda e mostri il menu.

Non importa in quale cartella si trovi il terminale: il comando usa percorsi completi. Non occorre dare manualmente il permesso di esecuzione allo script, perche viene aperto con `sh`.

#### Che cosa protegge lo script

- Non copia, stampa o modifica `.env`.
- Non sostituisce `data`, `scontrini` o `ollama-data` e non cambia i loro proprietari o permessi.
- Funziona anche se il bot e spento: verifica la configurazione del prossimo avvio tramite un container temporaneo che esegue soltanto il controllo, non il bot. Richiede sempre `DRY_RUN=true`. Se il bot e acceso, verifica anche la modalita del processo corrente. Se rileva la modalita reale o non puo completare i controlli, si ferma.
- Conserva i vecchi file del programma in `.update-backups`, dentro il progetto; questa cartella e esclusa da Git e dall'immagine Docker.
- Se la costruzione fallisce, non ricrea il container: se il precedente era acceso resta acceso; se era spento resta spento. Anche i file del programma su disco vengono riportati alla versione precedente.
- Dopo la ricreazione controlla il container e il database. Se il nuovo container non diventa sano entro tre minuti, ripristina da solo file e immagine precedenti e lo segnala con **ERRORE**. La risposta a `/start` resta necessaria per verificare Telegram: per l'amministratore mostra anche la **Versione attiva** (con lo ZIP compare «non indicata», perché manca Git).

Il backup automatico riguarda solo il programma, **non e un backup del database o delle ricevute**. Mantieni anche una copia protetta dei dati con il sistema di backup del NAS. Il ripristino automatico riguarda solo programma e immagine del bot, non i dati, e lo script non elimina i vecchi backup.

Se compare **ERRORE**, non cancellare dati e non ripetere comandi di pulizia. Comunica soltanto il messaggio necessario, dopo aver rimosso informazioni private. Se manca `aggiorna.sh`, stai usando un vecchio ZIP: procurati il nuovo pacchetto.

Prima di passare in futuro a una prova reale, controlla `/coda`: il bot ritenta automaticamente le operazioni in attesa. Cambiare globalmente `DRY_RUN` può coinvolgere anche quelle, non soltanto la nuova prova. Non abilitare la modalità reale per aggirare un errore della simulazione.

Non eliminare database o ricevute per ripartire. Non usare comandi di pulizia Docker o cancellazione dei volumi come tentativo generico di risoluzione.

## 16. Se qualcosa non funziona

| Cosa vedi | Cosa controllare |
| --- | --- |
| File Compose non trovato | Sei nella cartella sbagliata: verifica il percorso e la presenza di `docker-compose.yml`. |
| `.env` non trovato | La copia deve chiamarsi `.env`, non `.env.txt`, e stare accanto al file Compose. |
| PUID o PGID vuoti/non validi | Usa i numeri ottenuti con `id`, senza parentesi o nomi utente. |
| `Permission denied` o database non scrivibile | Controlla i permessi NAS di `data` e `scontrini` per l'utente configurato; non dare permessi a tutti. |
| Il bot non risponde | Controlla token localmente, ID autorizzati, registri e presenza di un'altra copia del bot accesa sul PC. |
| Errore Telegram `Conflict` | Ferma l'altra copia del bot con lo stesso token. |
| Errore HTTPS/certificato | Fermati e verifica la rete; non disabilitare la verifica dei certificati. |
| Opzioni AGESCI assenti o accesso fallito | Controlla le credenziali localmente e conserva la diagnostica; non provare un salvataggio reale. |
| Modello non trovato | Controlla `ollama list` e che `.env` usi lo stesso nome del modello scaricato. |
| Foto molto lenta o memoria insufficiente | Controlla l'uso RAM in Monitoraggio risorse DSM; non ripetere l'invio in continuazione. |

Per vedere anche gli errori di Ollama:

```sh
sudo docker compose logs --tail=80 ollama
```

Prima di chiedere aiuto:

1. Annota il numero del passaggio dove ti sei fermato.
2. Conserva sul NAS i registri e i file diagnostici.
3. Condividi soltanto il messaggio di errore necessario, dopo aver rimosso token, password, ID, dati delle ricevute e URL che contengono credenziali.

Non incollare l'intero `.env` e non inviare screenshot o HTML di diagnostica senza averli controllati: possono contenere dati personali anche quando i campi di input sono mascherati.

## 17. Quando hai finito

1. Nel terminale SSH scrivi `exit` per scollegarti. Il bot rimane acceso.
2. Se avevi abilitato SSH solo per questa installazione, disabilitalo da DSM dopo aver concluso le verifiche.
3. Conserva il file `.env` solo in posizioni protette.
4. Lascia `DRY_RUN=true`.

L'installazione si considera verificata sul NAS soltanto dopo le risposte Telegram e le prove riuscite. I test automatici sul PC non sostituiscono queste verifiche.

## Appendice: eventuale CSV, non necessario per iniziare

Esegui questa parte solo dopo il successo della prova manuale, e solo se vuoi provare un CSV.

1. In File Station crea `pending` dentro `data`, se non esiste.
2. Copia lì il CSV, senza caricarlo su GitHub o in chat.
3. Verifica `DRY_RUN=true`.
4. Dal terminale SSH, nella cartella del progetto, avvia:

```sh
sudo docker compose exec bot python -m bilancio.csv_import /data/pending/nomefile.csv
```

5. Sostituisci `nomefile.csv` con il nome effettivo del file.
6. Controlla che il riepilogo confermi le righe simulate senza salvataggio.

L'importatore si ferma al primo errore. Cerca le opzioni correnti nel portale e richiede corrispondenze univoche; la categoria può restare vuota solo se il portale lo consente. Non aggiungere `--apply` e non cambiare `DRY_RUN` per queste prove.

Se un'importazione reale autorizzata in futuro si interrompe, non rilanciarla subito: alcune righe potrebbero essere già registrate. Occorre prima controllare la Prima Nota e ottenere una nuova autorizzazione per le sole righe mancanti.
