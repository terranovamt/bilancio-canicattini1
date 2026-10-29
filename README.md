# Archivio spese AGESCI via Telegram

Progetto per un bot Telegram privato: riceve una foto o un PDF, corregge e analizza la ricevuta con un modello visivo locale, chiede conferma e conserva il file sul NAS. Le opzioni per la registrazione AGESCI devono essere lette dal portale e mostrate nel bot.

## Stato attuale

Il bot, l'analisi locale, l'archivio, SQLite, la coda dei ritentativi e il client Playwright hanno test automatici. Il client segue l'ordine del portale: anno scout ricavato dalla data, Tesoriere di gruppo, Prima Nota, poi le scelte dinamiche. Il salvataggio reale da CSV è stato completato e verificato sul portale il 27 ottobre 2026.

È disponibile anche `bilancio/csv_import.py` per importare CSV con classificazioni AGESCI. L'import completato comprendeva 112 movimenti: 80 entrate e 32 uscite. Due uscite già presenti sono state escluse dal nuovo invio; le altre 110 sono state elaborate e verificate nel registro. Due timeout di autenticazione hanno interrotto i primi blocchi, poi ripresi senza reinviare le righe già verificate. Il verificatore riconosce l'unità `Gruppo G1` anche quando il registro la mostra come `G1`.

La modalità `DRY_RUN=true` è quella predefinita e non salva gli scontrini sul portale. Eccezione: i comandi amministratore `/entrata` e `/testspesa` inseriscono entrate manuali reali, dopo conferma finale, anche in modalità prova. Le uscite dal bot richiedono sempre una ricevuta allegata. Per ogni nuova registrazione reale da scontrino o CSV, usa `DRY_RUN=false` solo per il processo autorizzato e dopo aver controllato i dati e il numero di righe. La ricevuta viene archiviata sul NAS e non viene allegata alla registrazione web.

L'analisi delle ricevute è prevista in locale con Ollama. Non è configurato l'invio delle immagini a un servizio AI online. Il modello locale va scelto dopo aver verificato la memoria e le prestazioni disponibili sul NAS.

## Componenti

- `bot/`: comandi Telegram, accesso autorizzato, flusso di conferma e ritentativi. I testi e le etichette sono centralizzati in [`bot/messages.py`](bot/messages.py); gli esempi sono nel [catalogo dei messaggi](docs/PREVIEW_MESSAGGI_TELEGRAM.md).
- `vision/`: correzione locale dell'immagine, schema dei dati e client Ollama.
- `storage/`: conservazione temporanea e archivio permanente delle ricevute.
- `db/`: spese, stati, duplicati e coda SQLite.
- `bilancio/`: interfaccia e client Playwright; selettori osservati e opzioni lette dinamicamente dal portale.
- `Dockerfile` e `docker-compose.yml`: esecuzione prevista su Synology.
- `docs/GUIDA_SYNOLOGY.md`: istruzioni per preparare il NAS.
- `docs/CONTESTO_PROGETTO.md`: decisioni e stato dettagliato del lavoro.
- `aggiorna.sh`: aggiornamento sul NAS da un pacchetto estratto separatamente, senza sostituire credenziali o dati; procedura nella sezione 15 della guida Synology.
- `scarica.sh`: download e deploy dal ramo Git `deploy` su comando dell'utente. Prima configurazione in [Deploy Git sul Synology](docs/DEPLOY_GIT_SYNOLOGY.md).
- `automatico.sh`: facoltativo, per l'Utilità di pianificazione DSM. Controlla le nuove versioni, installa su richiesta `/aggiorna` da Telegram o all'orario programmato; procedura nella sezione 8 della stessa guida. `/start` mostra all'amministratore la versione attiva e quella disponibile.

## Agenti AI in VS Code

Il punto di contatto e **Manager**: mantiene la visione del progetto, assegna incarichi circoscritti agli specialisti e verifica il risultato complessivo. Le definizioni sono in [`.github/agents/`](.github/agents/).

| Agente | Responsabilita |
| --- | --- |
| [Manager](.github/agents/manager.agent.md) | Dialogo con te, contesto, coordinamento e verifica finale. |
| [Sviluppo bot e archivio](.github/agents/bot-python.agent.md) | Telegram, accessi, archivio, database, coda, integrazione e deploy generale. |
| [Automazione portale AGESCI](.github/agents/agesci-portal.agent.md) | Client del portale, Playwright, selettori, CSV e verifica delle registrazioni. |
| [AI locale Ollama](.github/agents/ollama-ai.agent.md) | Analisi locale delle ricevute, modelli, prompt, immagini/PDF, errori e risorse Ollama. |
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

### Entrate manuali e scontrini non leggibili

Per un'entrata senza ricevuta, l'amministratore può usare `/entrata`:

1. Inserisci data, descrizione e importo quando richiesti.
2. Controlla il riepilogo; puoi correggere i dati prima di continuare.
3. Scegli cassa, Entrata e tipologia.
4. Verifica tutti i dati e premi **✅ Salva davvero** solo se vuoi registrare l'entrata reale. Questo comando ignora `DRY_RUN`.

Per un'uscita devi prima inviare una foto o un PDF della ricevuta. Se l'AI locale non riesce a leggerla, ricevi un solo avviso di errore e puoi compilare data, descrizione e importo a mano, poi scegliere cassa, direzione e tipologia. Il file resta temporaneamente sul NAS: compare nell'archivio `scontrini` solo quando confermi l'operazione e il bot lo archivia con i dati e la cassa scelti. `/annulla` elimina la copia temporanea, non le ricevute già archiviate. La compilazione a mano di una ricevuta non cambia `DRY_RUN`: resta una prova se la modalità prova è attiva. Questo percorso è disponibile agli stessi utenti autorizzati che possono inviare scontrini, anche se il modello non è configurato.

Le uscite senza ricevuta sono bloccate anche nei vecchi pulsanti e nei ritentativi della coda. Non usare l'inserimento manuale per aggirare un allegato mancante.

### Comando precedente `/testspesa`

All'avvio il bot registra il menu dei comandi Telegram: scrivendo `/` compaiono i suggerimenti. Invia `/start` per visualizzare la tastiera rapida. Gli altri utenti vedono solo i comandi di uso quotidiano; l'amministratore riceve anche i comandi riservati e l'indicazione della modalità, simulata o reale. Anche `/help` mostra i comandi riservati solo all'amministratore.

Le casse e le tipologie `Voci Scout` vengono lette dal portale in sottofondo circa 10 secondi dopo l'avvio del bot e di nuovo ogni notte alle 02:30 UTC. Restano in memoria per 26 ore, così durante la giornata i menu si aprono senza attendere il browser. La cache distingue anno scout, cassa, entrata/uscita, voce, tipologia e unità, così non mescola elenchi dipendenti. Se scegli un menu mentre la lettura iniziale è ancora in corso, il bot mostra un messaggio di attesa e completa la scelta appena possibile. Usa `/aggiornaopzioni` (amministratore) per rileggere subito casse e tipologie: al termine il bot ti scrive quante casse ha letto e quante tipologie di entrata e di uscita ha trovato per ciascuna. Il salvataggio e la verifica nel portale restano sempre operazioni reali di browser, anche quando i menu provengono dalla cache.

Nel passaggio delle tipologie il bot mostra prima le 6 più usate per quella cassa e direzione; in fondo c'è il pulsante **➕ Mostra altre** con quelle mancanti. All'inizio, quando non ci sono ancora spese registrate, mostra le prime 6 nell'ordine del portale.

`/testspesa` resta disponibile per l'amministratore come inserimento manuale di un'entrata, non di un'uscita senza ricevuta. Il bot propone subito valori predefiniti: data di oggi, descrizione «Prova Telegram» e importo 0,10 €. Puoi cambiarli con i pulsanti **📅 Data**, **💶 Importo** e **✏️ Descrizione**, poi premi **✅ Va bene**. Il bot chiede cassa e direzione, usa `Voci Scout` e mostra le tipologie disponibili per quella scelta. Imposta l’unità predefinita associata alla cassa; se per quell'anno scout il portale non offre alcuna unità («No results»), lascia l'unità vuota e non la mostra nel riepilogo. Lascia la categoria vuota. Prima del salvataggio mostra un riepilogo e chiede un'ultima conferma con **✅ Salva davvero**.

- `/testspesa` salva **sempre davvero** nel portale, anche con `DRY_RUN=true`: quell'impostazione vale solo per gli scontrini.
- Usa `/annulla` o il pulsante **Annulla** per interrompere prima della conferma finale.
- Se il salvataggio non viene verificato, il bot non riprova da solo: controlla nel portale e ripeti `/testspesa` solo se la spesa non c'è.
- Durante le operazioni lente compare un messaggio di attesa e i pulsanti spariscono. Se tocchi di nuovo un pulsante vecchio, il bot lo ignora e mostra «Questo pulsante non è più attivo».
- Se il portale non risponde mentre il bot legge casse o tipologie, la prova manuale viene annullata in automatico, senza inserire nulla: puoi ripartire subito con `/testspesa`. Per uno scontrino, invece, il file resta salvato e il bot propone **✅ Sì, è giusto** per riprovare oppure **Annulla**.
- Un nuovo `/testspesa` annulla da solo una prova manuale rimasta aperta.
- Se una conversazione iniziata prima di un aggiornamento mostra ancora i vecchi pulsanti per unità o categoria, usa `/annulla` e ricomincia con `/testspesa`.

Per la prima prova usa una descrizione chiaramente riconoscibile e controlla con attenzione anno, conto, direzione, tipologia, unità predefinita e importo. Non condividere i dati della spesa in chat con altre persone.

### Ultime spese e pulizia

`/ultime` mostra le ultime 10 spese: ✅ salvata e verificata, 🧪 prova senza salvataggio, ⚠️ da controllare oppure ⏳ in attesa o in corso. Le operazioni annullate o mai confermate non compaiono. Nei riepiloghi compaiono solo i campi compilati; i percorsi e i dettagli tecnici vengono inviati solo all'amministratore. La tastiera rapida dell'amministratore non contiene `/aggiornaopzioni` e `/pulisci`: li trovi in `/help`.

`/pulisci` (solo amministratore) svuota la memoria del bot. Cancella la coda, l'elenco di `/ultime`, le operazioni aperte, i file temporanei, i log e la diagnostica. Non cancella nulla nel portale AGESCI e non tocca le ricevute archiviate.

1. Su Telegram invia `/pulisci`.
2. Leggi l'avviso e premi **Sì, pulisci**; premi **No, lascia stare** per lasciare tutto com'è.
3. Il bot risponde con il numero di spese e di file eliminati.

### Riepilogo delle casse

`/riepilogo` legge dal portale AGESCI, in sola lettura, i saldi di tutte le casse dell'anno scout corrente. Lo possono usare tutti gli utenti autorizzati e non modifica nulla.

1. Su Telegram invia `/riepilogo`.
2. Attendi circa un minuto: compare «⏳ Leggo i saldi dal portale…».
3. Il messaggio si trasforma nel riepilogo. Per ogni cassa vedi:
   - il saldo attuale, cioè quanto dovresti avere in contanti o sul conto;
   - le entrate e le uscite;
   - l'ultima operazione.
4. Premi **📈 Dettagli** per vedere anche il saldo iniziale al 1° ottobre, l'andamento mese per mese e le ultime 3 operazioni. Il bot rilegge l'anno selezionato senza modificare nulla.
5. Premi **⬅️ Anno …** per vedere l'anno scout precedente, **Anno … ➡️** per tornare avanti, oppure **🔄 Aggiorna** per rileggere il portale e tornare alla vista compatta.

Se compare «❌ Non riesco a leggere i saldi adesso», riprova più tardi. Se compare «⚠️ Il portale mostra solo … righe su …», il saldo è comunque quello del portale, ma andamento e ultime operazioni sono parziali.

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

## Repository GitHub privato

Il repository locale è sul ramo `main`. Nessun push viene eseguito automaticamente. Quando vorrai pubblicare i commit nel repository privato, dal PC puoi usare:

```powershell
git remote add origin https://github.com/terranovamt/bilancio-canicattini1.git
git push -u origin main
```

Non aggiungere `.env`, ricevute, database, registrazioni non sanificate o credenziali al repository.
