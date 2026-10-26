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
- Il modulo mostra radio per `Entrata` e `Uscita`. Una registrazione seleziona entrambe le direzioni, ma compila solo campi chiamati `Importo Entrata` e `Tipologia Entrata`; non documenta in modo affidabile i campi, le tipologie o la categoria dell'uscita.
- L'elenco osservato nel codegen non è esaustivo: l'utente ha chiarito che non tutte le opzioni sono sbloccate. L'interfaccia Telegram dovrà quindi usare i valori che il portale rende disponibili dinamicamente, senza assumere che i nomi registrati siano tutti quelli esistenti.
- Non serve registrare un caricamento di allegato nel portale: l'utente ha chiarito che la ricevuta va archiviata sul NAS, senza allegarla all'operazione web.
- L'utente riferisce che la registrazione della seconda prova è stata chiusa senza salvare e che non trova la prima operazione di prova nel conto. Prima di qualsiasi test ulteriore, non assumere che una registrazione Playwright abbia cancellato o verificato dati nel portale.
- Non serve documentare il caricamento allegato: la ricevuta resta sul NAS. Il client Playwright usa campi e ruoli osservati; il tipo cambia tra `Tipologia Entrata` e `Tipologia Uscita`, mentre unità e categoria restano dinamiche. Il campo importo viene individuato dal nome visibile che inizia con `Importo`, senza fissare un valore specifico. Non eseguire le registrazioni Codegen come script contro il portale.
- L'utente ha escluso un approccio basato soltanto su OCR: la prima versione deve usare un modello visivo locale per comprendere i campi della ricevuta e una fase locale di correzione, ritaglio e raddrizzamento dell'immagine. Modello e risorse necessarie sul NAS restano da verificare; non attivare servizi online.
- È stata approvata una struttura modulare per future integrazioni. Configurazione, persistenza SQLite, archivio permanente e temporaneo, modello Pydantic, correzione locale delle immagini, client Ollama, regole di accesso, client Playwright, flusso Telegram e ritentativi hanno test automatici. Il bot legge le opzioni dal portale senza elenchi statici. Sono presenti `/coda`, `/riprova` e `/testbilancio`; il client compila il modulo in `DRY_RUN` e non salva mai una spesa.
- Il client Playwright usa la sessione persistente in `/data`, selettori e URL osservati in `bilancio/selectors.yaml`, ritenta gli errori e salva screenshot/HTML di diagnostica in `/data/debug` con campi di input mascherati o rimossi. L'etichetta esatta del campo importo in Uscita non è stata ricordata: il client cerca un campo visibile con nome che inizia per `Importo`, coerente con l'etichetta osservata per Entrata.
- Dockerfile e Compose sono stati aggiunti con immagine Playwright/Python 3.12 fissata, volumi persistenti, memoria condivisa per Chromium e controlli di salute; i file privati sono esclusi dal contesto di build. I log JSON hanno rotazione. I test automatici passano e il file Compose è stato letto come YAML; il PC non ha Docker, quindi l'immagine non è stata costruita qui.
- L'utente vuole provare il flusso sul PC prima di passare al NAS. Ha riferito 8 GB di RAM sul DS1522+ e 15,4 GB sul PC. Qwen3-VL 2B è il modello candidato per verificare sul PC lo stesso carico previsto sul NAS; Ollama è supportato come contenitore CPU-only per Linux/amd64, ma prestazioni e consumo del NAS non sono ancora stati misurati.
- Per la prova locale sono stati aggiunti il caricamento di `.env`, `DATA_PATH` configurabile e percorsi separati `data-local`/`scontrini-local`, esclusi da Git. L'installazione automatica di Ollama sul PC non è riuscita a causa del proxy che ha richiesto autenticazione HTTP 407; non sono state inserite credenziali né completati download o installazioni.
- Resta da definire e verificare una lettura della pagina che confermi una registrazione completata; per questo il pulsante Salva non viene attivato dal client, anche se `DRY_RUN` venisse disattivato. Le registrazioni Codegen non sono test di salvataggio e non vanno eseguite come script. La build Docker non è stata provata perché Docker non è installato sul PC. `README.md` e `docs/GUIDA_SYNOLOGY.md` spiegano i limiti e vietano l'uso per spese reali.
