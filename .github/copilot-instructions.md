# Indicazioni del progetto

## Scopo e lingua

Questo progetto realizza un bot Telegram privato per archiviare ricevute e registrare spese nel portale AGESCI tramite automazione browser, perché il portale non dispone di un'API pubblica.

- Parla con l'utente e scrivi i messaggi del bot in italiano semplice.
- README e documentazione per l'utente vanno scritti in italiano.
- Identificatori, commenti nel codice e messaggi di log vanno scritti in inglese.

## Requisiti da rispettare

- Python 3.12 nel container; bot asincrono con `python-telegram-bot` v21 o superiore.
- Limita l'uso del bot agli ID configurati; non rispondere agli utenti non autorizzati e non registrare nei log dati o contenuti sensibili.
- Non inserire segreti nel codice, nei test, nella documentazione, negli output o nei commit. `.env` deve restare ignorato da Git.
- L'analisi delle ricevute deve privilegiare l'elaborazione locale. Non aggiungere invio a un servizio online senza una scelta esplicita dell'utente.
- Il client AGESCI deve restare isolato dietro `BilancioClient`. Leggi URL e selettori da `bilancio/selectors.yaml`; non inventare selettori senza una registrazione reale e sanificata.
- Dopo un salvataggio automatizzato, verifica il risultato leggendo la pagina; un clic riuscito non basta.
- Mantieni archivio e database su volumi persistenti; conserva gli errori diagnostici senza esporre segreti.

## Modifiche e verifiche

- Per lavorare con gli agenti del progetto, usa `Manager` come interlocutore principale; gli specialisti in `.github/agents/` ricevono incarichi circoscritti. Ruoli e uso sono descritti in `README.md`, sezione "Agenti AI in VS Code".
- Le regole comuni restano in questo file e nelle istruzioni pertinenti; non duplicarle integralmente nei singoli agenti. Le decisioni e lo stato verificato restano in `docs/CONTESTO_PROGETTO.md`.

- Esamina prima le convenzioni e il codice già presenti; modifica solo ciò che serve alla richiesta.
- Aggiungi o aggiorna test per il comportamento modificato e usa i comandi di verifica già previsti dal progetto.
- Mantieni aggiornato `docs/CONTESTO_PROGETTO.md` quando cambia una decisione o lo stato del lavoro.
- Prima di ogni commit, verifica che `.env`, credenziali, ricevute, registrazioni non sanificate e dati personali non siano inclusi.
- I commit devono avere solo l'autore richiesto dall'utente. Chiedi nome ed email prima del primo commit e configurali solo nel repository locale. Non fare push senza richiesta esplicita.
