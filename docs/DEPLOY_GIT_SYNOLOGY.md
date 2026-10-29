# Aggiornamento Git sul NAS, solo quando lo decidi tu

Non ci sono timer, webhook o aggiornamenti automatici. Un push dal PC rende disponibile il codice, ma **il NAS non fa nulla finché non esegui il comando di aggiornamento**.

Il ramo distribuito si chiama `deploy`. La copia Git viene tenuta separata dal progetto attivo: credenziali, database, ricevute e modello rimangono nel progetto esistente e non entrano in Git.

## 1. Prima configurazione sul PC e su GitHub

Questa parte deve essere completata prima dei comandi NAS.

1. Verifica che il repository GitHub del progetto sia privato.
2. Controlla che il commit da pubblicare includa `scarica.sh`, `aggiorna.sh` e le modifiche correlate.
3. Controlla che non includa `.env`, database, ricevute, log, file di sessione o diagnostica privata.
4. Conferma nome ed email dell'autore prima di creare il commit, se non sono già stati confermati.
5. Configura il remoto del repository sul PC, se manca. Per il repository previsto:

```powershell
git remote add origin git@github.com:terranovamt/bilancio-canicattini1.git
```

6. Non ripetere `remote add` se `origin` esiste già; controllane l'indirizzo senza inserirvi token.
7. Solo dopo l'autorizzazione al push, pubblica il commit verificato sul ramo `deploy`. Dal ramo locale contenente quel commit:

```powershell
git push origin HEAD:refs/heads/deploy
```

Non usare `--force`. Se Git rifiuta il push, controlla le differenze prima di proseguire. Pubblicare `main` non aggiorna il NAS: il comando NAS legge solo `deploy`.

**Prima di proseguire sul NAS:** apri il repository GitHub e verifica che nel selettore dei rami compaia `deploy` e che contenga `scarica.sh` e `aggiorna.sh`. La presenza del codice su GitHub non significa che la connessione NAS sia già configurata. Non provare a clonare `deploy` prima che sia stato pubblicato.

## 2. Sul NAS: installa Git

1. Accedi a DSM come descritto nella guida Synology.
2. Apri **Centro pacchetti**.
3. Cerca **Git Server**.
4. Installa il pacchetto ufficiale Synology, se non è già installato.
5. Apri il collegamento SSH con il tuo account amministratore NAS.
6. Scrivi:

```sh
git --version
```

7. Verifica che compaia una versione Git.

Non devi pubblicare un server Git sul NAS, abilitare accessi dall'esterno o aprire porte del router. Usiamo soltanto il client Git per scaricare da GitHub. Se `git` non viene trovato dopo l'installazione, fermati e segnala l'errore.

## 3. Sul NAS: crea una chiave dedicata di sola lettura

Esegui questi comandi nella sessione SSH **senza `sudo`**, con lo stesso account che userai per aggiornare.

1. Controlla che il tuo utente abbia una cartella personale accessibile:

```sh
cd ~
pwd
```

2. Se la cartella personale non esiste o non è scrivibile, chiedi all'amministratore NAS di verificare il servizio Home utente prima di continuare.
3. Prepara la cartella delle chiavi:

```sh
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
```

4. Crea una chiave nuova dedicata al progetto:

```sh
ssh-keygen -t ed25519 -f "$HOME/.ssh/bilancio_github" -C "bilancio-nas-readonly"
```

5. Se viene chiesto di sovrascrivere una chiave esistente, rispondi **no** e fermati: potresti aver già configurato l'accesso.
6. Alla richiesta della passphrase scegli una protezione e inseriscila due volte. Verrà richiesta durante il download; non comunicarla in chat.
7. Visualizza soltanto la chiave **pubblica**:

```sh
cat "$HOME/.ssh/bilancio_github.pub"
```

8. Copia l'intera riga visualizzata.

Il file che termina in `.pub` è la chiave pubblica da registrare su GitHub. **Non aprire, copiare o inviare il file `bilancio_github` senza `.pub`: è la chiave privata.**

## 4. Su GitHub: autorizza la chiave

1. Dal browser sul PC accedi a GitHub.
2. Apri il repository privato del progetto.
3. Apri **Settings → Deploy keys**.
4. Premi **Add deploy key**.
5. Come titolo scrivi `Synology bilancio - sola lettura`.
6. Nel campo della chiave incolla la riga pubblica ottenuta prima.
7. **Lascia disattivata l'opzione Allow write access.**
8. Conferma l'aggiunta e completa l'eventuale verifica richiesta da GitHub.

Il NAS potrà scaricare quel repository, non pubblicare modifiche. Non creare token con permessi generali sull'account.

## 5. Sul NAS: verifica la connessione e crea la copia Git

1. Nella sessione SSH verifica l'accesso:

```sh
ssh -T -i "$HOME/.ssh/bilancio_github" -o IdentitiesOnly=yes git@github.com
```

2. Alla prima connessione, prima di accettare la chiave del server, confronta l'impronta mostrata con le [impronte ufficiali SSH di GitHub](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints), aperte nel browser tramite HTTPS.
3. Accetta solo se l'impronta corrisponde. Non usare opzioni che disabilitano la verifica.
4. Inserisci la passphrase della tua chiave quando richiesta.
5. GitHub deve indicare che l'autenticazione è riuscita e che non offre accesso shell: questo messaggio è normale, anche se il comando termina con codice 1.
6. Crea la copia Git con il seguente comando **solo se il progetto attivo è in `/volume1/docker/bilancio_canicattini1`**:

```sh
git -c core.sshCommand="ssh -i '$HOME/.ssh/bilancio_github' -o IdentitiesOnly=yes" clone --branch deploy --single-branch git@github.com:terranovamt/bilancio-canicattini1.git /volume1/docker/bilancio-canicattini1-git
```

7. Se usi un altro volume, sostituisci `/volume1/docker` con il tuo percorso effettivo. La nuova cartella `bilancio-canicattini1-git` deve stare accanto al progetto attivo, non dentro di esso.
8. Salva nella sola copia Git il comando SSH da riutilizzare:

```sh
git -C /volume1/docker/bilancio-canicattini1-git config core.sshCommand "ssh -i '$HOME/.ssh/bilancio_github' -o IdentitiesOnly=yes"
```

9. Adegua anche qui il percorso al tuo volume.

Se compare `Permission denied (publickey)`, controlla la deploy key e l'account NAS usato. Non ripetere con `sudo`: cambierebbe l'utente e la cartella delle chiavi. Se manca il ramo `deploy`, completa la pubblicazione dal PC.

## 6. Ogni aggiornamento: un comando sul NAS

Prima assicurati che nessuno stia completando una spesa. Lo script non attende automaticamente la fine delle conversazioni: durante le prove sospendi l'uso del bot prima di aggiornare.

1. Dal PC viene pubblicata una versione verificata sul ramo `deploy`. Questo **non avvia nulla sul NAS**.
2. Quando vuoi installarla, apri SSH sul NAS con lo stesso account della configurazione iniziale.
3. Esegui:

```sh
sh /volume1/docker/bilancio-canicattini1-git/scarica.sh /volume1/docker/bilancio_canicattini1
```

4. Sostituisci i percorsi se il tuo volume è diverso.
5. Inserisci la passphrase della chiave GitHub se richiesta.
6. Inserisci la password NAS quando `sudo` la richiede per Docker e la copia dei file.
7. Attendi **Versione installata**, seguito dall'identificativo Git.
8. Invia `/start` su Telegram per verificare la risposta.

Non anteporre `sudo` al comando iniziale: lo script usa l'utente normale per Git e chiede i privilegi soltanto per installare.

Il comando scarica il ramo `deploy` con `fetch` e lo aggiorna solo con avanzamento lineare (`--ff-only`). Se ci sono modifiche locali o rami divergenti, si ferma senza sovrascriverli. Due aggiornamenti contemporanei sono bloccati.

Puoi rilanciare il comando anche per la stessa versione, dopo aver risolto un errore: verrà ricostruita e ricreata. Non viene saltata solo perché Git è già aggiornato, dato che un precedente tentativo potrebbe non aver completato l'installazione.

## 7. Limiti e recupero dagli errori

- Puoi aggiornare anche con il bot spento: un container temporaneo controlla soltanto la configurazione del prossimo avvio, senza avviare il bot o elaborare la coda. Serve sempre `DRY_RUN=true`. Se il bot e acceso, viene verificata anche la modalita del processo corrente.
- Nessun aggiornamento Git cambia automaticamente la modalità simulata o reale.
- `.env`, `data`, `scontrini` e `ollama-data` non vengono copiati, cancellati o inseriti nella copia Git.
- Il vecchio codice viene conservato in `.update-backups` nel progetto attivo; questo non sostituisce il backup dei dati.
- Non esiste ancora un ripristino automatico dell'immagine precedente. Se la costruzione fallisce, il vecchio container mantiene il suo stato (acceso o spento); se la ricreazione o il controllo di salute falliscono, occorre esaminare l'errore prima di dichiarare riuscito il deploy.
- Se un comando viene interrotto bruscamente o il NAS si spegne, può rimanere una cartella di blocco. Non cancellarla senza prima verificare che nessun aggiornamento sia ancora in esecuzione.
- Non modificare i file nella copia Git NAS: lavora sul PC, pubblica la versione e poi richiedi il download.
- Non configurare **Utilità di pianificazione**, timer o webhook per questa procedura.

Per la diagnostica usa i comandi della [guida Synology](GUIDA_SYNOLOGY.md). Condividi soltanto errori ripuliti da credenziali e dati personali, mai la chiave privata o la configurazione privata.
