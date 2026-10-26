# Contesto del progetto

Questa pagina conserva i requisiti e le decisioni utili a chi lavora sul progetto. Non contiene token, password, codici di verifica o ricevute.

## Obiettivo

Creare un bot Telegram privato per un piccolo gruppo. Il bot riceve foto o PDF di ricevute, propone i dati estratti, archivia il file sul NAS Synology e registra la spesa nel portale di bilancio AGESCI tramite Playwright. Il portale non ha un'API pubblica; il browser è quindi un'integrazione isolata e sostituibile.

## Ambiente previsto

- NAS Synology DS1522+, DSM 7.2, Container Manager, architettura x86_64.
- Applicazione in Docker, Python 3.12, bot Telegram asincrono con long polling.
- Volumi persistenti per database, stato della sessione browser e ricevute.
- Configurazione tramite `.env`, da non aggiungere mai a Git.

## Comportamenti richiesti

- Consentire l'accesso solo agli ID elencati nella configurazione; ignorare e registrare in modo sicuro gli altri accessi.
- Estrarre dati strutturati dalla ricevuta, validare i campi, chiedere all'utente le informazioni mancanti e richiedere conferma prima di procedere.
- Archiviare le ricevute esclusivamente sul NAS, con nomi sicuri e prevenzione dei duplicati tramite hash.
- Usare la cartella `STORAGE_PATH/YYYY/MM/` e il nome `GG_MM_AAAA_negoziante_conto_importo.estensione`; sanificare i valori inseriti nel nome.
- Interagire con il portale esclusivamente attraverso l'interfaccia `BilancioClient`; tenere selettori e URL in `bilancio/selectors.yaml`.
- Verificare ogni registrazione rileggendo la pagina. In caso di errore, conservare diagnostica protetta, ritentare e mettere in coda gli elementi non riusciti.
- Supportare la modalità `DRY_RUN` e i comandi utente e amministratore indicati nella richiesta iniziale.

## Decisioni concordate

- Privilegiare l'analisi locale delle ricevute. Nessun servizio AI online va attivato senza una scelta esplicita dell'utente.
- Gli utenti autorizzati aggiuntivi verranno raccolti in seguito. Non duplicare ID personali in documentazione o esempi.
- La prova del portale deve usare un conto vuoto indicato dall'utente e deve essere cancellata dopo il test. Non effettuare inserimenti reali senza conferma.
- Prima dell'inserimento nel portale, il bot deve chiedere quale cassa usare, se registrare un'entrata o un'uscita e poi le relative tipologie, unità e categorie. Le scelte devono essere presentate come opzioni selezionabili su Telegram.
- Non codificare elenchi parziali delle casse, tipologie, unità o categorie: l'utente non ha ancora sbloccato tutte le opzioni e le voci possono dipendere dalla cassa, dalla direzione e dai permessi. Preferire la lettura dinamica dal portale dopo l'accesso e le selezioni precedenti, mostrando su Telegram le sole opzioni disponibili. Non inventare voci non osservate.
- Non allegare la ricevuta alla registrazione del portale: conservarla nell'archivio persistente sul NAS. Questa decisione sostituisce il requisito iniziale di allegato al portale.
- Le spiegazioni all'utente, i messaggi del bot e la documentazione sono in italiano semplice.
- Codice, identificatori, commenti e messaggi di log sono in inglese.
- Non fare push. Prima del primo commit chiedere nome ed email e configurare l'identità solo nel repository locale. Non includere segreti o dati personali nei commit.
- Mantenere i componenti separati per poter aggiungere in futuro altre integrazioni e funzioni. Evitare però infrastruttura generica non necessaria finché non viene richiesta una nuova integrazione.

## Stato e informazioni ancora da raccogliere

- Repository Git locale inizializzato sul ramo `main`; repository GitHub privato creato. I moduli di base e l'avvio iniziale del bot sono stati committati localmente; non è stato fatto alcun push.
- `.env` locale creato e ignorato da Git; il token del bot va inserito solo localmente dall'utente.
- Nome utente del bot confermato dall'utente: `@bilanciocanicattini1_bot`.
- L'utente ha recuperato l'ID Telegram dell'amministratore; gli ID degli altri utenti arriveranno in seguito. Gli ID vanno configurati localmente, non inseriti in questo documento.
- Python 3.14.6 è disponibile sul PC per registrare il flusso Playwright. Il container applicativo dovrà comunque usare Python 3.12.
- Le istruzioni per l'eventuale proxy del PC sono conservate nella nota locale ignorata da Git, non nella configurazione del bot né nei file da condividere.
- URL della pagina di accesso indicato dall'utente: `https://bilancio.agesci.it/login`.
- Sono disponibili tre registrazioni Playwright sanificate in `data/portal-recording/`. Sono materiale di osservazione, non automazioni da eseguire: contengono clic su Salva e selettori fragili.
- La registrazione `workflow-expense-upload.py` contiene solo l'apertura e la chiusura della pagina di accesso; non documenta azioni sul modulo.
- Nella seconda registrazione sono apparsi i nomi `Cassa LC`, `Cassa EG`, `Cassa RS`, `Cassa Co.ca` e `Conto Co.ca`. Sono le voci osservate, non un elenco completo verificato.
- Il modulo mostra radio per `Entrata` e `Uscita` e le scelte `Voci Scout` e `Voci Ministero`. I campi osservati sono `Data Operazione`, `Descrizione`, `Importo Entrata`/`Importo Uscita`, `Tipologia Entrata`/`Tipologia Uscita`, `Unità`, `Categoria` e `Note`. Le tipologie dipendono dalle scelte e si leggono dinamicamente.
- L'elenco osservato nel codegen non è esaustivo: l'utente ha chiarito che non tutte le opzioni sono sbloccate. L'interfaccia Telegram dovrà quindi usare i valori che il portale rende disponibili dinamicamente, senza assumere che i nomi registrati siano tutti quelli esistenti.
- Non serve registrare un caricamento di allegato nel portale: l'utente ha chiarito che la ricevuta va archiviata sul NAS, senza allegarla all'operazione web.
- L'utente riferisce che la registrazione della seconda prova è stata chiusa senza salvare e che non trova la prima operazione di prova nel conto. Prima di qualsiasi test ulteriore, non assumere che una registrazione Playwright abbia cancellato o verificato dati nel portale.
- Non serve documentare il caricamento allegato: la ricevuta resta sul NAS. Il client Playwright usa campi e ruoli osservati; il tipo cambia tra `Tipologia Entrata` e `Tipologia Uscita`, mentre unità e categoria restano dinamiche. Il campo importo viene individuato dal nome visibile che inizia con `Importo`, senza fissare un valore specifico. Non eseguire le registrazioni Codegen come script contro il portale.
- L'utente ha escluso un approccio basato soltanto su OCR: la prima versione deve usare un modello visivo locale per comprendere i campi della ricevuta e una fase locale di correzione, ritaglio e raddrizzamento dell'immagine. Modello e risorse necessarie sul NAS restano da verificare; non attivare servizi online.
- Il flusso verificato del portale parte dalla scelta dell'anno nel selettore superiore, prosegue con il ruolo `Tesoriere di gruppo` e quindi con `Prima Nota`. Selezionare l'anno può ripristinare il ruolo predefinito; il client deve perciò scegliere il ruolo dopo l'anno. L'anno scout va calcolato dalla data dell'operazione: dal 1º ottobre si usa l'anno nuovo (`26-27` per date dal 1 ottobre 2026 al 30 settembre 2027).
- Nel modulo AGESCI sono presenti anche le scelte `Voci Scout` e `Voci Ministero`, oltre a direzione, tipologia, unità e categoria. Il flusso Telegram le presenta come selezioni, e le liste successive vengono lette dal portale.
- È disponibile il comando amministratore `/testspesa` per inserire data, descrizione e importo in Telegram senza ricevuta e senza AI. Il flusso chiede una conferma iniziale e un riepilogo finale esplicito; archiviazione e hash della ricevuta sono esclusi. Con `DRY_RUN=true` simula; per l’inserimento reale si deve avviare con `DRY_RUN=false`. La prova automatica usa un client finto e non ha scritto sul portale.
- Per la prova locale Windows si può selezionare Chrome installato con `PLAYWRIGHT_BROWSER_CHANNEL=chrome`; nel container il valore resta vuoto e Playwright usa Chromium incluso nell’immagine.
- È stata approvata una struttura modulare per future integrazioni. Configurazione, persistenza SQLite, archivio permanente e temporaneo, modello Pydantic, correzione locale delle immagini, client Ollama, regole di accesso, client Playwright, flusso Telegram e ritentativi hanno test automatici. Il bot legge le opzioni dal portale senza elenchi statici. Sono presenti `/coda`, `/riprova` e `/testbilancio`; con `DRY_RUN=true` il client compila senza salvare, mentre `DRY_RUN=false` abilita il salvataggio verificato a livello di codice.
- Il client Playwright usa la sessione persistente in `/data`, selettori e URL osservati in `bilancio/selectors.yaml`, ritenta le letture e salva screenshot/HTML di diagnostica in `/data/debug` con campi di input mascherati o rimossi. Per un salvataggio reale non ripete il clic automaticamente; controlla la nuova riga della Prima Nota per data, conto, descrizione, importo, tipologia e unità prima di segnare la spesa come riuscita. Se il risultato è incerto, l'operazione va in coda e il tentativo successivo cerca prima la stessa riga per evitare duplicati.
- Dockerfile e Compose sono stati aggiunti con immagine Playwright/Python 3.12 fissata, volumi persistenti, memoria condivisa per Chromium e controlli di salute; i file privati sono esclusi dal contesto di build. I log JSON hanno rotazione. I test automatici passano e il file Compose è stato letto come YAML; il PC non ha Docker, quindi l'immagine non è stata costruita qui.
- L'utente ha scelto di passare direttamente alla prova sul NAS: prima `/testspesa` senza scontrino e senza AI con `DRY_RUN=true`, poi il modello visivo locale e una prova con foto sempre in modalità simulata. Ha riferito 8 GB di RAM sul DS1522+; le prestazioni CPU-only non sono ancora state misurate. Qwen3-VL 2B è il modello candidato per iniziare.
- Per la prova locale sono stati aggiunti il caricamento di `.env`, `DATA_PATH` configurabile e percorsi separati `data-local`/`scontrini-local`, esclusi da Git. L'utente ha installato Ollama per Windows (0.40.11). Il download di `qwen3-vl:2b` è stato bloccato dal filtro del proxy. Un successivo avvio del bot locale si è fermato prima del collegamento a Telegram per un errore HTTPS `CERTIFICATE_VERIFY_FAILED` (certificato autofirmato nella catena). Non è stato disattivato il controllo dei certificati, non è stata stabilita una conversazione Telegram e non è stata inserita alcuna spesa.
- La guida Synology è stata aggiornata per effettuare prima il test manuale simulato e poi configurare Qwen3-VL 2B sul servizio Ollama in Docker. Per il modello, la versione Ollama fissata in Compose (`0.40.11`) soddisfa il requisito minimo indicato dalla pagina ufficiale Qwen3-VL (`0.12.7`). Il modello non è ancora stato scaricato sul NAS e il progetto non è ancora stato avviato lì.
- È stato aggiunto `bilancio/csv_import.py`, un importatore controllato con simulazione predefinita, risoluzione delle opzioni dal portale, controllo dei duplicati già gestito dal client e arresto al primo errore. Cerca prima una corrispondenza univoca del testo completo e poi, quando è sicuro, un codice univoco. La modalità reale richiede `DRY_RUN=false`, il conteggio esatto e una conferma digitata.
- Il CSV di lavoro è stato verificato solo localmente: l'utente ha confermato che 52 righe sono intenzionali, con 4 entrate e 48 uscite; descrizioni, date e importi sono completi e la categoria è stata lasciata vuota in attesa che il portale la permetta. Non registrare il file né i suoi dati nel repository.
- Il preflight del CSV è stato eseguito con `DRY_RUN=true` e ha verificato l'accesso, la selezione dell'anno scout `25-26`, il ruolo `Tesoriere di gruppo`, la Prima Nota e la lettura delle opzioni dinamiche. L'utente ha scelto la tipologia completa per la riga 8; la copia corretta è locale in Download e l'originale non è stato modificato. La scansione in sola lettura ha trovato altre 35 righe in cui il codice `A.2` corrisponde a più voci del portale. Nessuna spesa è stata salvata; l'importazione completa non è ancora stata verificata.
- Il flusso di salvataggio reale è abilitabile con `DRY_RUN=false`; rimane non verificato sul portale da un test automatizzato e non va provato senza un'autorizzazione esplicita e un importo di test concordato. Non è stata eseguita una registrazione automatizzata dal bot. Le registrazioni Codegen non vanno eseguite come script e i file grezzi che possono contenere credenziali non vanno conservati. La build Docker non è stata provata perché Docker non è installato sul PC.
