---
description: "Use when creating or changing Python files, the Telegram bot, receipt processing, SQLite storage, Docker runtime, or Python tests."
name: "Python project rules"
applyTo: "**/*.py"
---
# Regole per il codice Python

- Usa Python 3.12 e tipizzazione esplicita; mantieni le funzioni asincrone quando interagiscono con `python-telegram-bot`.
- Segui la separazione prevista tra `bot/`, `vision/`, `storage/`, `bilancio/` e `db/`; non accoppiare direttamente i gestori Telegram al portale.
- Scrivi in inglese identificatori, commenti e messaggi di log. Tutti i testi mostrati alle persone, inclusi pulsanti e notifiche, devono essere in italiano.
- Carica la configurazione da `.env` tramite il modulo di configurazione del progetto. Non inserire valori segreti predefiniti e non stampare segreti o ricevute nei log.
- Valida i dati esterni con modelli tipizzati e gestisci gli errori in modo esplicito; non nascondere errori con valori di successo predefiniti o `except` generici.
- Mantieni le operazioni sul portale dietro `BilancioClient`; usa i selettori esterni in `bilancio/selectors.yaml`.
- Scrivi test isolati per parsing, nomi file, duplicati e comportamento modificato. Non contattare il portale reale nei test automatici.
