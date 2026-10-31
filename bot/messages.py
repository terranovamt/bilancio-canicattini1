"""Italian Telegram copy shared by commands, workflows and notifications."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Mapping
from zoneinfo import ZoneInfo

GEMINI_MODEL_PROGRESS = "\nGemini · {model}"
GEMINI_NOTICES = {
    "using_model": "Gemini · provo {model}.",
    "quota_warning": "{model}: raggiunta la soglia di avviso delle richieste contate dal bot.",
    "local_limit": "{model}: limite configurato del bot raggiunto. Nuovo tentativo dal {retry}.",
    "remote_limit": "{model}: quota segnalata da Google. {retry}",
    "unavailable": "{model}: non disponibile; passo al successivo.",
    "unreadable": "{model}: lettura non affidabile o risposta non utilizzabile; provo il successivo, se disponibile.",
    "partial": "Gemini non ha ottenuto una lettura completa e affidabile. Ti mostro i migliori dati disponibili: controllali e completa quelli mancanti.",
}


def gemini_notice(notice: str, model: str | None, retry_at: float | None, zone: str) -> str:
    retry = (
        datetime.fromtimestamp(retry_at, ZoneInfo(zone)).strftime("%d/%m/%Y %H:%M:%S %Z")
        if retry_at is not None else ""
    )
    if notice == "remote_limit":
        retry = (
            f"Google indica di riprovare non prima del {retry}; non garantisce il ripristino di tutte le quote."
            if retry else "Google non indica quando riprovare. Sospendo questo modello fino al prossimo giorno del contatore; non e' un reset garantito."
        )
    return GEMINI_NOTICES[notice].format(model=model, retry=retry)

DAILY_READ_SUCCESS = (
    "✅ Saldi letti dal portale e salvati localmente · anno scout {year}.\n"
    "Lettura del {day:%d/%m/%Y} alle {clock:%H:%M:%S} ({zone})."
)
DAILY_READ_FAILED = (
    "❌ Non riesco ad aggiornare i saldi dal portale · anno scout {year}.\n"
    "L'eventuale snapshot precedente resta invariato."
)
DAILY_READ_CACHE_FAILED = (
    "✅ Saldi letti dal portale · anno scout {year}.\n"
    "Lettura del {day:%d/%m/%Y} alle {clock:%H:%M:%S} ({zone}).\n"
    "❌ Non riesco a salvare lo snapshot locale; i dati precedenti restano invariati."
)
DAILY_REPORT_HEADER = (
    "📊 Operazioni BOT del {day:%d/%m/%Y}\n"
    "Solo registrazioni reali verificate: giorno del primo evento di verifica "
    "(ora locale configurata), non data della ricevuta. Escluse prove e già presenti.\n"
    "Gli eventi storici senza timestamp di verifica certo non sono conteggiati."
)
DAILY_TOTALS = (
    "Operazioni: {count}\n"
    "Entrate: {income_count} · {income}\n"
    "Uscite: {expense_count} · {expense}"
)
DAILY_ACCOUNT = "🏦 {account}"
DAILY_GLOBAL = "Totale globale"
DAILY_ZERO_MOVEMENTS = "Nessuna operazione reale verificata dal bot nel giorno: zero movimenti."
DAILY_REPORT_FAILED = "❌ Non riesco a calcolare il resoconto delle operazioni BOT."
SUMMARY_CACHED = (
    "Ultimi dati letti dal portale il {day:%d/%m/%Y} alle {clock:%H:%M:%S} ({zone}).\n"
    "⚠️ Solo saldi salvati localmente, non aggiornati; dettagli non disponibili."
)
SUMMARY_CACHE_SAVE_FAILED = "⚠️ Saldi letti dal portale, ma non salvati nella cache locale."

HOME_TEXT = (
    "👋 Ciao! Qui tieni in ordine le spese.\n\n"
    "📷 Mandami una foto o un PDF dello scontrino, oppure scegli cosa fare "
    "con i pulsanti qui sotto."
)
BACK_BUTTON = "⬅️ Indietro"
MENU_HELP = (
    "❓ Come funziona\n\n"
    "📷 Nuovo scontrino: invia una foto o un PDF. Ti mostro insieme i dati letti: "
    "confermali o correggi solo quello sbagliato. Se manca qualcosa, ti chiedo solo "
    "quel dato; poi scegli cassa e tipologia.\n\n"
    "💰 Nuova entrata: registra un’entrata senza foto.\n\n"
    "Alla fine ti mostro il riepilogo: «Accetta» registra, «Modifica» ti permette di "
    "cambiare un dato, la cassa, la tipologia o la nota. Non salvo nulla prima di «Accetta». "
    "Solo in modalità PROVA non salvo nel portale.\n\n"
    "📊 Riepilogo: consulta i saldi delle casse.\n"
    "📋 Ultime operazioni: controlla le tue registrazioni.\n"
    "⏳ Operazioni in attesa: consulta gli elementi da riprovare o verificare.\n\n"
    "Aprire il menu non annulla un inserimento in corso. Per interromperlo "
    "premi «Annulla operazione»."
)
MENU_ADMIN_HELP = (
    "\n\nIn ⚙️ Amministrazione trovi:\n"
    "✍️ Inserisci a mano: registra un’entrata o un’uscita senza foto.\n"
    "Alla fine controlli il riepilogo e registri solo con «Accetta».\n"
    "🧪 Lì puoi anche attivare o disattivare la modalità prova."
    "\n📊 Stato API: richieste Gemini contate dal bot, limiti e attese."
)
MENU_RECEIPT = (
    "📷 Nuovo scontrino\n\nInvia qui una foto o un PDF della ricevuta. "
    "Ti mostro i dati letti tutti insieme da confermare o correggere, poi scegli cassa "
    "e tipologia. Registro solo quando premi «Accetta» nel riepilogo finale."
)
MENU_ADMIN = "⚙️ Strumenti amministratore\n\nScegli un’operazione."
MENU_PLACEHOLDER = "Scegli dal menu o invia una foto"
MENU_UNKNOWN = "Questo pulsante non è disponibile. Usa il menu in basso."
MENU_RETRY = "🔄 Riprova operazioni"
MENU_RECEIPT_BUTTON = "📷 Nuovo scontrino"
MENU_MANUAL_BUTTON = "✍️ Inserisci a mano"
MENU_INCOME_BUTTON = "💰 Nuova entrata"
MENU_SUMMARY_BUTTON = "📊 Riepilogo"
MENU_LATEST_BUTTON = "📋 Ultime operazioni"
MENU_QUEUE_BUTTON = "⏳ Operazioni in attesa"
MENU_HELP_BUTTON = "❓ Aiuto"
MENU_CANCEL_BUTTON = "✖️ Annulla operazione"
RECEIPT_PREVIEW_INITIAL = "Ricevuta originale: confrontala con i dati del riepilogo prima di confermare."
RECEIPT_PREVIEW_FINAL = "Ricevuta originale: controlla i dati del riepilogo finale prima di premere «Accetta»."
RECEIPT_PREVIEW_FAILED = (
    "⚠️ Non riesco a mostrarti la ricevuta qui. Controlla il file che hai inviato "
    "prima di confermare i dati; non ho registrato nulla."
)
OPERATION_MENU_ACTIVE = "Hai un'operazione in corso: nel menu puoi annullarla."
OPERATION_MENU_FINISHED = "Operazione conclusa: menu aggiornato."
OPERATION_MENU_FAILED = "❌ Non riesco a verificare l'operazione in corso per aggiornare il menu."
MENU_ADMIN_BUTTON = "⚙️ Amministrazione"
MENU_HEALTH_BUTTON = "🔌 Verifica collegamento"
MENU_OPTIONS_BUTTON = "🔄 Aggiorna casse e tipologie"
MENU_VERSION_BUTTON = "🔖 Versione e aggiornamenti"
MENU_API_STATUS_BUTTON = "📊 Stato API"
COMMAND_API_STATUS = "Utilizzo Gemini, limiti e attese del bot"
API_STATUS_HEADER = (
    "📊 Stato API · Gemini\nLettura del {checked}\n"
    "Solo richieste contate dal bot, inclusi tentativi falliti; non consumo ufficiale Google. "
    "Non include AI Studio, altri programmi, token o quote condivise.\n"
    "Minuto mobile: ultimi 60 secondi. Prossimo giorno del contatore: {reset} "
    "(mezzanotte Pacifico).\nSoglia avviso: {warning}%."
)
API_STATUS_NOT_CONFIGURED = "Gemini non configurato: nessun utilizzo API disponibile."
API_STATUS_LEGACY = (
    "Gemini in modalità singolo modello: {model}.\n"
    "Contatori e limiti consultabili solo con la catena GEMINI_MODELS attiva; "
    "utilizzo non disponibile, non significa zero richieste."
)
API_STATUS_FAILED = "❌ Non riesco a leggere i contatori Gemini. Riprova; non ho effettuato richieste API."
API_STATUS_MODEL = (
    "{model}\nMinuto: {minute}/{rpm} ({minute_percent:.1f}%)\n"
    "Giorno: {daily}/{rpd} ({daily_percent:.1f}%)"
)
API_STATUS_AVAILABLE = "Disponibile secondo il contatore locale; disponibilità Google non verificata."
API_STATUS_LOCAL_BLOCK = "Limite locale raggiunto: nuovo tentativo dal {retry}."
API_STATUS_REMOTE_BLOCK = "Quota Google: riprovare non prima del {retry}; ripristino non garantito."
API_STATUS_REMOTE_UNKNOWN = (
    "Quota Google: reset sconosciuto. Sospensione prudenziale del bot fino al {retry}, "
    "non reset Google confermato."
)
MENU_CLEAN_BUTTON = "🧹 Pulisci cronologia"
MENU_UPDATE_BUTTON = "🔄 Installa aggiornamento"
MENU_TEST_ON_BUTTON = "🧪 Attiva modalità prova"
MENU_TEST_OFF_BUTTON = "✅ Torna ai salvataggi reali"
MODE_CHANGED = (
    "{mode}\n\nLa scelta vale per tutti i nuovi inserimenti, fino a quando la cambi, "
    "anche dopo un riavvio. Le operazioni già iniziate mantengono la loro modalità."
)
MODE_CHANGE_FAILED = "❌ Non riesco a cambiare modalità. Non ho modificato l’impostazione: riprova."
MODE_TRIAL = "🧪 Modalità PROVA: non viene salvato nulla nel portale."
TEST_DISABLED = "Modalità prova disattivata."
SUMMARY_TOTALS = (
    "Totale di tutte le casse e conti\n"
    "Saldo iniziale: {opening}\nEntrate: {income}\n"
    "Uscite: {expense}\nSaldo finale: {balance}"
)
MODE_REAL = "✅ Modalità REALE: salvo nel portale solo dopo «Accetta» nel riepilogo finale."
COMMAND_START = "Apri il menu del bot"
COMMAND_HELP = "Guida e comandi"
COMMAND_LATEST = "Le tue ultime spese"
COMMAND_SUMMARY = "Saldi e andamento delle casse"
COMMAND_CANCEL = "Interrompi quello che stiamo facendo"
COMMAND_QUEUE = "Spese in attesa o da controllare"
COMMAND_RETRY = "Riprova le spese in attesa"
COMMAND_MANUAL = "Inserisci un'entrata o un'uscita a mano"
COMMAND_INCOME = "Inserisci un'entrata manualmente"
COMMAND_HEALTH = "Verifica il collegamento al portale"
COMMAND_REFRESH = "Aggiorna casse e tipologie"
COMMAND_CLEAN = "Svuota spese e registri del bot"
COMMAND_UPDATE = "Installa l'ultima versione del bot"

VERSION_WITH_DATE = "{short} del {date}"
VERSION_RUNNING = "🔖 Versione attiva: {version}"
VERSION_UNKNOWN = "🔖 Versione attiva: non indicata (installata senza Git)."
VERSION_UNKNOWN_SHORT = "versione non indicata"
VERSION_NOT_CHECKED = (
    "ℹ️ Il controllo automatico degli aggiornamenti non è ancora attivo."
)
VERSION_CHECK_FAILED = (
    "⚠️ L'ultimo controllo degli aggiornamenti non è riuscito ({checked})."
)
VERSION_NEW = "🆕 Nuova versione disponibile: {summary}\nPer installarla: /aggiorna"
VERSION_LATEST = "✅ È l'ultima versione (controllo del {checked})."
VERSION_PUBLISHED = (
    "ℹ️ Ultima versione pubblicata: {summary} (controllo del {checked})."
)
VERSION_LAST_FAILED = "⚠️ Ultimo aggiornamento non riuscito ({summary}): {reason}"
VERSION_REQUEST_PENDING = "⏳ Installazione richiesta: parte al prossimo controllo."
UPDATE_REASONS = {
    "build": "la costruzione è fallita, è rimasta attiva la versione precedente.",
    "start": "la nuova versione non è partita, ho ripristinato quella precedente.",
    "rollback": "la nuova versione non è partita e il ripristino non è riuscito: serve un controllo sul NAS.",
    "live_mode": "il bot è in modalità REALE, aggiornamento bloccato.",
    "config": "la configurazione non è valida, non ho cambiato nulla.",
    "download": "non riesco a scaricare la nuova versione.",
    "dirty": "la copia Git sul NAS contiene modifiche locali.",
    "up_to_date": "è già installata l'ultima versione.",
    "package": "il pacchetto da installare è incompleto.",
}
UPDATE_REASON_UNKNOWN = "errore imprevisto: serve un controllo sul NAS."
UPDATE_DONE = "✅ Aggiornamento installato: {summary}"
UPDATE_ROLLED_BACK = "⚠️ Aggiornamento non riuscito ({summary}): {reason}"
UPDATE_FAILED = "❌ Aggiornamento non riuscito ({summary}): {reason}"
UPDATE_SKIPPED = "ℹ️ Nessun aggiornamento: {reason}"
UPDATE_AVAILABLE = (
    "🆕 È disponibile una nuova versione del bot: {summary}\n"
    "Premi «Installa aggiornamento» oppure usa /aggiorna. "
    "Ti chiederò conferma prima di installare."
)
UPDATE_PROMPT = (
    "🔄 Vuoi installare l'ultima versione?\n\n{status}\n\n"
    "Il bot si riavvia per circa un minuto: controlla che nessuno stia registrando una spesa."
)
UPDATE_YES = "Sì, installa"
UPDATE_NO = "No, lascia stare"
UPDATE_REQUESTED = (
    "⏳ Richiesta registrata. Il NAS la esegue al prossimo controllo, "
    "di solito entro 10 minuti: aggiorno questo messaggio a ogni passaggio."
)
UPDATE_PROGRESS = {
    "download": "⏳ Inizio l'aggiornamento: scarico la nuova versione…",
    "install": (
        "🔧 Installo {summary}.\n"
        "Preparo la nuova versione, può richiedere qualche minuto. "
        "Intanto il bot continua a funzionare."
    ),
    "restart": (
        "🔄 Riavvio il bot con {summary}.\n"
        "Per un minuto circa potrei non rispondere: l'esito comparirà qui."
    ),
}
UPDATE_ALREADY_REQUESTED = (
    "⏳ C'è già una richiesta in attesa: il NAS la esegue al prossimo controllo."
)
UPDATE_CANCELLED = "Ok, nessun aggiornamento."
UPDATE_NOT_CONFIGURED = (
    "ℹ️ Il controllo automatico non è attivo sul NAS: la richiesta non verrebbe eseguita. "
    "Aggiorna con il comando manuale."
)
UPDATE_REQUEST_FAILED = "❌ Non riesco a registrare la richiesta. Riprova più tardi."

SAVED_ICON = "✅"
TRIAL_ICON = "🧪"
REVIEW_ICON = "⚠️"
RECEIPT_SAVED = f"{SAVED_ICON} Fatto! Spesa salvata e verificata nel portale."
TRIAL_SAVED = f"{TRIAL_ICON} Prova riuscita! Nel portale non è stato salvato nulla."
ALREADY_PRESENT = "ℹ️ Questa spesa era già nel portale, non l'ho inserita due volte."
STATUS_SAVED = "✅ salvata"
STATUS_TRIAL = "🧪 prova"
STATUS_QUEUED = "⏳ in attesa di essere salvata"
STATUS_REVIEW = "⚠️ da controllare"
STATUS_WORKING = "⏳ in corso"

RECEIPT_PROGRESS = "📷 Ricevuto! Lo leggo subito…"
CORRECTED_COPY_FAILED = (
    "⚠️ Ho conservato la ricevuta originale, ma non sono riuscito a salvare "
    "la copia corretta. L'operazione prosegue con l'originale."
)
ANALYSIS_WAITING = (
    "⏳ Sto ancora analizzando la ricevuta ({seconds} secondi). "
    "Non ho ancora ricevuto dati: la preparazione può richiedere tempo. "
    "Puoi aprire il menu o l’aiuto mentre aspetti."
)
ANALYSIS_PREPARING_IMAGE = (
    "⏳ Analisi in corso ({seconds} secondi).\n"
    "Fase 1 di 4: preparo l’immagine per il modello."
)
ANALYSIS_LOADING_MODEL = (
    "⏳ Analisi in corso ({seconds} secondi).\n"
    "Fase 2 di 4: preparo la richiesta per Gemini."
)
ANALYSIS_READING_IMAGE = (
    "⏳ Analisi in corso ({seconds} secondi).\n"
    "Fase 3 di 4: OCR locale in corso per recuperare il testo della ricevuta."
)
ANALYSIS_RECEIVING = (
    "⏳ Analisi in corso ({seconds} secondi).\n"
    "Fase 4 di 4: Gemini sta scrivendo i dati ({characters} caratteri finora). "
    "Aspetto la risposta completa prima di usarla."
)
ANALYSIS_FINISHED = "📷 Attesa dell’analisi terminata. Trovi il risultato o le istruzioni nel messaggio successivo."
RECEIPT_TRANSFER_FAILED = (
    "⚠️ Non sono riuscito a completare il trasferimento della foto o del PDF "
    "da Telegram. L'analisi non è iniziata e non ho registrato nulla. "
    "Attendi un momento e invia di nuovo il file."
)
RECEIPT_DETAILS = (
    "Ecco i dati dell’operazione. Potrai correggerli nel riepilogo finale con «Modifica»."
)
RECEIPT_REVIEW = (
    "🧾 Ecco i dati letti dalla ricevuta.\n"
    "Se sono giusti premi «Conferma», altrimenti tocca il dato da correggere."
)
LOW_CONFIDENCE = "⚠️ Non sono sicuro di aver letto bene: controlla con attenzione."
MISSING_DATE = (
    "Non sono riuscito a leggere la data. Me la scrivi come giorno-mese-anno? "
    "(es. 29-10-2026 o 29/10/26)"
)
MISSING_MERCHANT = "Non sono riuscito a leggere il negozio. Come si chiama?"
MISSING_AMOUNT = "Non sono riuscito a leggere il totale. Quanto hai speso? (es. 12,50)"
EDIT_DATE = "Qual è la data giusta? Scrivila come giorno-mese-anno (es. 29-10-2026 o 29/10/26)"
EDIT_MERCHANT = "Come si chiama il negozio?"
EDIT_AMOUNT = "Qual è l'importo giusto? (es. 12,50)"
MANUAL_DATE = (
    "📅 Scrivi la data come "
    "giorno-mese-anno, ad esempio 29-10-2026, 29/10/26 o 2-3-26."
)
MANUAL_MERCHANT = "✏️ Scrivi la descrizione dell’operazione."
MANUAL_AMOUNT = (
    "💶 Scrivi l’importo (es. 12,50)."
)
FIELD_SUGGESTION = (
    "\n\nValore attuale: {value}\n"
    "Scrivi il valore corretto oppure premi «⬅️ Torna al riepilogo»."
)
HOME_BUTTON = "🏠 Menu principale"
INSERTING = "⏳ Sto inserendo…"
MANUAL_DESCRIPTION = "Prova Telegram"
MANUAL_CONFIRMATION = (
    "✍️ Operazione a mano, senza ricevuta\n\n{details}\n\n"
    "Puoi cambiare i dati prima di scegliere la cassa e la tipologia."
)
MANUAL_FORM_TITLE = "✍️ Nuova operazione a mano (entrata o uscita)"
INCOME_FORM_TITLE = "💰 Nuova entrata a mano"
MANUAL_FORM_HINT = (
    "Usa i tasti sotto il modulo oppure scrivi il dato richiesto: il modulo si aggiorna "
    "e i pulsanti restano in fondo. Per cambiare un campo premi il suo pulsante."
)
MANUAL_FORM_DATE = "📅 Data: {value}"
MANUAL_FORM_DESCRIPTION = "✏️ Descrizione: {value}"
MANUAL_FORM_AMOUNT = "💶 Importo: {value}"
MANUAL_FORM_AMOUNT_TYPING = "{value} € ✏️"
MANUAL_FORM_EMPTY = "da inserire"
MANUAL_FORM_INVALID = "⚠️ Non ho capito il valore scritto. Riprova."
MANUAL_FORM_NEXT = "👉 {prompt}"
MANUAL_FORM_FOOTER = (
    "Poi sceglierai cassa e tipologia e controllerai il riepilogo: registro solo con «Accetta»."
)
MANUAL_FORM_DONE = "✍️ Dati inseriti:\n\n{details}"
TODAY_BUTTON = "📅 Oggi"
YESTERDAY_BUTTON = "Ieri"
CALENDAR_WEEKDAYS = ("L", "M", "M", "G", "V", "S", "D")
AMOUNT_CONFIRM_BUTTON = "✅ Conferma importo"
UNSUPPORTED_MESSAGE = (
    "Sticker, audio, video e altri file non sono supportati e non li conservo. "
    "Usa i pulsanti, scrivi il dato richiesto oppure invia una foto o un PDF della ricevuta."
)
MANUAL_FAILED = (
    "❌ Non sono riuscito a salvare la spesa e non riproverò da solo.\n\n"
    "{details}\n\n👉 Controlla nel portale se compare. Se non c'è, riparti con "
    "«Nuova entrata» oppure, da ⚙️ Amministrazione, con «Inserisci a mano»."
)
MANUAL_FORMAT = (
    "Non ho capito 🙂 Scrivi data (giorno-mese-anno), descrizione e importo separati da «|», "
    "per esempio: 26-10-2026 | Pranzo | 12,50"
)
ACCOUNTS_PROGRESS = "⏳ Cerco le casse…"
ACCOUNT_PROMPT = "🏦 Quale cassa vuoi usare? (1 di 3)"
INCOME_ONLY_PROMPT = (
    "➕ Senza ricevuta puoi registrare solo un’entrata. Premi Entrata per continuare."
)
EXPENSE_RECEIPT_REQUIRED = (
    "⚠️ Per registrare un’uscita serve una ricevuta allegata. "
    "Non ho registrato nulla. Premi «Annulla operazione» nel menu e invia una foto o un PDF: "
    "se non riesco a leggerlo, puoi compilare i dati a mano."
)
DIRECTION_PROMPT = "↔️ È un'entrata o un'uscita? (2 di 3)"
TYPE_PROGRESS = (
    "⏳ Carico le tipologie, può servire fino a un minuto. "
    "Aspetta senza premere altro 🙏"
)
TYPE_POPULAR = "🏷️ Che tipo di spesa è? (3 di 3)\nTi mostro le più usate."
TYPE_ALL = "🏷️ Che tipo di spesa è? (3 di 3)"
TYPE_MORE = "🏷️ Altre tipologie"
MORE_TYPES_BUTTON = "➕ Mostra altre ({count})"
INCOME = "⬇️ Entrata"
EXPENSE = "⬆️ Uscita"
SAVE_PROGRESS = (
    "⏳ Salvo nel portale e controllo che sia andato tutto bene… "
    "Un attimo, non premere altro."
)
TRIAL_PROGRESS = (
    "🧪 Faccio la prova nel portale (non salvo nulla)… " "Un attimo, non premere altro."
)
MANUAL_CONFIRM_BUTTON = "✅ Va bene"
RECEIPT_CONFIRM_BUTTON = "✅ Conferma"
DATE_BUTTON = EDIT_DATE_BUTTON = "📅 Data"
AMOUNT_BUTTON = EDIT_AMOUNT_BUTTON = "💶 Importo"
DESCRIPTION_BUTTON = "✏️ Descrizione"
EDIT_MERCHANT_BUTTON = "🏪 Negozio"
CANCEL_BUTTON = "Annulla"
NO_CATEGORY_BUTTON = "Senza categoria"

LATEST_HEADER = "🧾 Le tue ultime spese"
LATEST_EMPTY = "Ancora nessuna spesa. Mandami uno scontrino per iniziare 📷"
QUEUE_HEADER = "⏳ Spese ancora da salvare o da controllare"
QUEUE_EMPTY = "Tutto a posto: non ci sono spese in attesa ✅"
QUEUE_FOOTER = "Premi «Riprova operazioni» per riprovare quelle in attesa."
QUEUE_WAITING = "in attesa"
NOTHING_TO_RETRY = "Non c'è niente da riprovare."
RETRY_DONE = "Ho riprovato: {saved} salvata ✅, {pending} ancora in attesa ⏳, {failed} da controllare ⚠️."
RETRY_DONE_WITH_TRIALS = (
    "Ho riprovato: {saved} salvate ✅, {trials} in prova 🧪 "
    "(nel portale non è stato salvato nulla per queste prove), "
    "{pending} ancora in attesa ⏳, {failed} da controllare ⚠️."
)
RETRY_TRIAL = "🧪 Nuovo tentativo in prova: nel portale non è stato salvato nulla."
RETRY_SAVED = "✅ Salvata: {description}, {amount} ({day})."
RETRY_EXHAUSTED = (
    "⚠️ Non sono riuscito a salvare «{description}, {amount}». "
    "L'amministratore la controllerà: non devi fare altro."
)
ADMIN_RETRY_EXHAUSTED = (
    "⚠️ Una spesa non è stata salvata dopo vari tentativi. Controlla «Operazioni in attesa» nel menu."
)
ADMIN_TRIAL_UNCONFIRMED = "⚠️ La modalità prova non è stata rispettata. Controlla il portale prima di riprovare."
HEALTH_OK = "✅ Il portale risponde correttamente. Non ho inserito nulla."
HEALTH_MANUAL = "⚠️ Il portale chiede una verifica manuale (es. un codice). Non ho inserito nulla.\nDettagli: {folder}{diagnostic_file}"
HEALTH_FAILED = (
    "❌ Non riesco ad accedere al portale. Non ho inserito nulla.\nDettagli: {folder}"
)
ADMIN_DIAGNOSTIC = "{message} ({filename})"
DIAGNOSTIC_DETAILS = "Dettagli: {path}"
REFRESH_PROGRESS = (
    "🔄 Aggiorno casse e tipologie in sottofondo, ti scrivo quando ho finito. "
    "Non inserisco nessuna spesa."
)
REFRESH_DONE = "✅ Fatto! Trovate {count} casse."
REFRESH_ROW = "• {account}: {income} entrate, {expense} uscite"
UNREAD_COUNT = "da rileggere"
REFRESH_PARTIAL = "Alcune tipologie non si sono caricate: le rileggo quando servono."
REFRESH_FAILED = "❌ Non riesco a leggere le casse dal portale. Riprova più tardi."
CLEAN_PROMPT = (
    "🧹 Vuoi svuotare coda, ultime spese e registri del bot?\n"
    "Il portale e le ricevute archiviate non vengono toccati."
)
CLEAN_YES = "Sì, pulisci"
CLEAN_NO = "No, lascia stare"
CLEAN_DONE = "🧹 Fatto! Eliminate {expenses} spese ({pending} in attesa) e {files} file temporanei. Portale e ricevute intatti."
CLEAN_CANCELLED = "Ok, non ho toccato niente."
CANCELLED = "Ok, ho annullato. Quando vuoi, mandami un altro scontrino."
NOTHING_TO_CANCEL = "Non c'è niente da annullare."
RECEIPT_ALREADY_ACTIVE = (
    "Stiamo già lavorando su uno scontrino: completalo oppure premi «Annulla operazione» nel menu."
)
MANUAL_RECEIPT_ACTIVE = "Prima finiamo lo scontrino in corso, oppure premi «Annulla operazione» nel menu."
MANUAL_PREVIOUS_CANCELLED = "Avevi un inserimento manuale rimasto aperto: l'ho chiuso."
USE_BUTTONS = "Usa i pulsanti qui sopra, oppure premi «Annulla operazione» nel menu."
ANALYZER_UNAVAILABLE = (
    "Al momento non riesco a leggere gli scontrini. Riprova più tardi."
)
SEND_RECEIPT = "Mandami una foto o un PDF dello scontrino 📷"
FILE_TOO_LARGE = "Il file è troppo grande (massimo 20 MB). Ne provi uno più leggero?"
FILE_UNREADABLE = (
    "Non riesco ad aprire questo file. Controlla che sia una foto o un PDF e riprova."
)
RECEIPT_DUPLICATE = "Questo scontrino l'ho già ricevuto, non lo rielaboro."
ANALYSIS_FAILED = (
    "⚠️ Non sono riuscito a leggere lo scontrino con OCR+Gemini. "
    "Ho conservato il file temporaneo. "
    "Inseriamo i dati a mano: data, descrizione e importo, poi cassa e tipologia. "
    "La ricevuta sarà archiviata quando confermi l’operazione; Annulla elimina "
    "la copia temporanea.\n\n{prompt}"
)
ADMIN_ANALYSIS_FAILED = "L'analisi OCR+Gemini di una ricevuta non è riuscita."
AI_FAILURE_REASONS = {
    "invalid_request": "Gemini ha rifiutato la richiesta (HTTP 400): può dipendere dal formato o dai parametri. Non dimostra che il modello sia incompatibile.",
    "authentication": "Gemini ha rifiutato l'accesso: verifica privatamente chiave API e autorizzazioni del progetto.",
    "quota": "Gemini ha segnalato un limite di richieste o quota. Verifica i limiti del progetto e riprova più tardi.",
    "timeout": "Gemini non ha risposto entro il tempo configurato.",
    "connection": "Il bot non riesce a comunicare con Gemini. Verifica la connessione Internet del NAS.",
    "model_not_found": "Gemini non trova il modello richiesto. Controlla GEMINI_MODEL.",
    "memory": "Il servizio AI ha segnalato risorse insufficienti. Riprova dopo qualche minuto.",
    "unsupported_model": "Gemini ha segnalato un modello o una funzione non supportati. Verifica GEMINI_MODEL.",
    "service_error": "Gemini ha restituito un errore non classificato. Il corpo grezzo non viene mostrato: può contenere dati privati.",
    "invalid_response": "Il servizio non ha restituito una risposta JSON valida nel formato atteso.",
    "incomplete_response": "Gemini ha restituito una risposta incompleta o interrotta. I dati non vengono accettati.",
    "empty_result": "La risposta di Gemini non contiene un risultato utilizzabile per la ricevuta.",
    "invalid_receipt": "Il risultato del modello non rispetta lo schema dei dati: JSON non valido o valori non ammessi. Nessun dato viene inventato.",
    "not_configured": "Gemini non è configurato nel bot. Imposta GEMINI_API_KEY e ricrea il container bot.",
    "preprocessing": "Il file non può essere preparato per l'analisi: formato, immagine o PDF non supportati o non leggibili.",
}
AI_ADMIN_DIAGNOSTIC = (
    "🔧 Diagnosi AI — solo amministratore\n"
    "Codice: {code}{status}\n"
    "Timeout configurato: {timeout} secondi.\n{reason}"
)
AI_HTTP_STATUS = " — HTTP {status}"
AI_FAILURE_WITH_DIAGNOSTIC = "{message}\n\n{diagnostic}\n\n{prompt}"


def ai_admin_diagnostic(code: str, status: int | None, timeout: int) -> str:
    if code not in AI_FAILURE_REASONS:
        code = "service_error"
    return AI_ADMIN_DIAGNOSTIC.format(
        code=code,
        status=AI_HTTP_STATUS.format(status=status) if status is not None else "",
        timeout=timeout,
        reason=AI_FAILURE_REASONS[code],
    )


STALE_BUTTON = (
    "Questo pulsante non è più attivo. Se vuoi, mandami di nuovo lo scontrino."
)
BUSY = "⏳ Sto ancora lavorando, un attimo!"
CHOOSE_DIRECTION = "Prima scegli entrata o uscita 🙂"
ADMIN_ONLY = "Questo comando è riservato all'amministratore."
PORTAL_UNAVAILABLE = (
    "Il collegamento al portale non è ancora attivo. Non ho salvato nulla."
)
PORTAL_UNAVAILABLE_RECEIPT = (
    "Il collegamento al portale non è ancora attivo. "
    "Non ho salvato nulla: lo scontrino resta in attesa."
)
PORTAL_READ_FAILED = "Non riesco a leggere {subject} dal portale adesso. Non ho salvato nulla e i dati dello scontrino restano qui: premi «Conferma» per riprovare oppure Annulla."
PORTAL_EDIT_READ_FAILED = (
    "⚠️ Non riesco a leggere {subject} dal portale adesso: ho lasciato i dati come prima.\n\n"
)
PORTAL_MANUAL_READ_FAILED = "Non riesco a leggere {subject} dal portale. Non ho inserito nulla e ho conservato i dati: premi «Va bene» per riprovare oppure Annulla."
ACCOUNTS_SUBJECT = "le casse"
TYPES_SUBJECT = "le tipologie"
ADMIN_ACCOUNTS_FAILED = "Non riesco a leggere le casse. Dettagli: {folder}"
ADMIN_TYPES_FAILED = "Non riesco a leggere le tipologie. Dettagli: {folder}"
ADMIN_UNVERIFIED = "⚠️ Registrazione non verificata. Controlla {folder} e «Operazioni in attesa» nel menu."
SUBMISSION_QUEUED = (
    "⚠️ Non sono sicuro che la spesa sia stata salvata. L'ho messa in attesa "
    "e riproverò io: non devi rifare nulla."
)
SUBMISSION_FAILED = (
    "⚠️ Non sono riuscito a completare l'operazione. Lo scontrino è al sicuro: "
    "avvisa l'amministratore."
)
TRIAL_UNCONFIRMED = (
    "⚠️ Il portale non ha confermato che fosse una prova, quindi mi sono fermato "
    "per sicurezza. Avvisa l'amministratore e controlla il portale prima di riprovare."
)
UNEXPECTED_ERROR = "⚠️ Errore imprevisto durante l'elaborazione di un messaggio."

SUMMARY_PROGRESS = "⏳ Leggo i saldi dal portale (circa un minuto). Non modifico nulla."
SUMMARY_FAILED = "❌ Non riesco a leggere i saldi adesso. Riprova tra poco."
SUMMARY_BUSY = "⏳ Lo sto già preparando, un attimo!"
SUMMARY_HEADER = "📊 Bilancio {year}\nDati aggiornati adesso dal portale."
PREVIOUS_YEAR = "⬅️ Anno {year}"
NEXT_YEAR = "Anno {year} ➡️"
REFRESH_BUTTON = "🔄 Aggiorna"
DETAILS_BUTTON = "📈 Dettagli"
ACCOUNT_HEADER = "🏦 {account}"
ACCOUNT_BALANCE = "💰 Saldo: {balance}"
ACCOUNT_TOTALS = "Entrate {income} · uscite {expense}"
OPENING_BALANCE = "Saldo iniziale: {amount}"
MONTHLY_HEADER = "📈 Andamento mensile"
OPERATIONS_HEADER = "🧾 Ultime operazioni"
MONTHLY_ROW = "• {month} {year}: entrate {income} · uscite {expense} → saldo {balance}"
PARTIAL_LEDGER = (
    "⚠️ Il portale mostra solo {visible} righe su {total}: i dati sono parziali."
)
NO_MOVEMENTS = "Nessuna operazione dall'inizio dell'anno."
LEDGER_ROW = "• {day:%d/%m} {description}: {amount}"
LAST_MOVEMENT = "Ultima operazione: {day:%d/%m} {description} {amount}"
NO_ACCOUNTS = "{header}\n\nNon trovo casse nel portale."
MONTH_NAMES = (
    "Gennaio",
    "Febbraio",
    "Marzo",
    "Aprile",
    "Maggio",
    "Giugno",
    "Luglio",
    "Agosto",
    "Settembre",
    "Ottobre",
    "Novembre",
    "Dicembre",
)
EMPTY_VALUE = "—"
EURO_AMOUNT = "{sign}{digits} €"
ELLIPSIS = "{text}…"
DATE_LINE = "📅 {value}"
MERCHANT_LINE = "🏪 {value}"
DESCRIPTION_LINE = "📝 {value}"
AMOUNT_LINE = "💶 {value}"
DIRECTION_AMOUNT = "{amount} ({direction})"
CATEGORY_LINE = "🏷️ {value}"
ACCOUNT_LINE = "🏦 {value}"
UNIT_LINE = "👥 {value}"
PORTAL_CATEGORY_LINE = "🗂️ {value}"
NOTES_LINE = "🗒️ {value}"
NO_NOTES_LINE = "🗒️ Nessuna nota"
FINAL_SUMMARY_TITLE = "📋 Riepilogo finale: controlla i dati prima di registrare."
FINAL_SUMMARY_HINT = (
    "Premi «✅ Accetta» per registrare l’operazione, oppure «✏️ Modifica» per cambiare "
    "un dato o aggiungere una nota. Non salvo nulla finché non premi «Accetta»."
)
FINAL_ACCEPT_BUTTON = "✅ Accetta"
FINAL_MODIFY_BUTTON = "✏️ Modifica"
FINAL_EDIT_PROMPT = (
    "{details}\n\n✏️ Cosa vuoi modificare?\n"
    "Dopo la modifica torni al riepilogo: non salvo nulla finché non premi «Accetta»."
)
BACK_TO_SUMMARY_BUTTON = "⬅️ Torna al riepilogo"
ACCOUNT_BUTTON = "🏦 Cassa"
TYPE_BUTTON = "🏷️ Tipologia"
DIRECTION_BUTTON = "↔️ Entrata/Uscita"
NOTE_BUTTON = "🗒️ Nota"
CLEAR_NOTE_BUTTON = "🧹 Togli la nota"
NOTE_EDIT_PROMPT = (
    "🗒️ Scrivi la NOTA per il portale (massimo {limit} caratteri).\n{current}"
    "Dopo l’invio torni al riepilogo: non salvo nulla finché non premi «Accetta»."
)
NOTE_CURRENT = "Nota attuale: {value}\n"
DATE_YEAR_CHANGED = (
    "📅 La nuova data è in un altro anno scout: scegli di nuovo cassa e tipologia.\n\n"
)
NOTES_TOO_LONG = "La nota è troppo lunga: scrivila in massimo {limit} caratteri."
ROW_SEPARATOR = " · "
INCOME_LABEL = "entrata"
EXPENSE_LABEL = "uscita"


def safe_text(value: object, limit: int = 200) -> str:
    return " ".join(str(value).split())[:limit] if value is not None else ""


def account_text(value: object, limit: int = 200) -> str:
    # A space prevents Telegram from detecting the abbreviation as a domain.
    return safe_text(value, limit).replace("Co.ca", "Co. ca").replace("co.ca", "co. ca")


def date_text(value: object, *, short: bool = False) -> str:
    if not value:
        return ""
    day = value if isinstance(value, date) else date.fromisoformat(str(value))
    return day.strftime("%d/%m" if short else "%d/%m/%Y")


def format_euro(amount: Decimal, *, signed: bool = False) -> str:
    sign = "-" if amount < 0 else ("+" if signed and amount > 0 else "")
    digits = f"{abs(amount):,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")
    return EURO_AMOUNT.format(sign=sign, digits=digits)


def amount_text(payload: Mapping[str, object]) -> str:
    amount = payload.get("total_amount")
    if amount is None or amount == "":
        return ""
    currency = payload.get("currency")
    if currency in (None, "", "EUR"):
        return format_euro(Decimal(str(amount)))
    return f"{Decimal(str(amount)):.2f} {safe_text(currency)}".replace(".", ",")


def operation_details(
    payload: Mapping[str, object],
    selections: Mapping[str, object],
    *,
    unit_left_empty: bool = False,
) -> str:
    lines: list[str] = []
    for template, value in (
        (DATE_LINE, date_text(payload.get("date"))),
        (
            DESCRIPTION_LINE if payload.get("manual_entry") is True else MERCHANT_LINE,
            safe_text(payload.get("merchant")),
        ),
    ):
        if value:
            lines.append(template.format(value=value))
    amount = amount_text(payload)
    if amount:
        direction = selections.get("direction")
        if direction:
            amount = DIRECTION_AMOUNT.format(
                amount=amount, direction=safe_text(direction)
            )
        lines.append(AMOUNT_LINE.format(value=amount))
    for key, template in (
        ("account", ACCOUNT_LINE),
        ("transaction_type", CATEGORY_LINE),
        ("unit", UNIT_LINE),
        ("category", PORTAL_CATEGORY_LINE),
    ):
        if key == "unit" and unit_left_empty:
            continue
        option = selections.get(key)
        value = safe_text(option.get("label")) if isinstance(option, dict) else ""
        if key == "account" and isinstance(option, dict):
            value = account_text(option.get("label"))
        if value:
            lines.append(template.format(value=value))
    notes = safe_text(selections.get("portal_notes"), limit=500)
    if notes:
        lines.append(NOTES_LINE.format(value=notes))
    return "\n".join(lines)


def manual_form(
    payload: Mapping[str, object],
    *,
    income_only: bool,
    prompt: str | None,
    mode: str,
    invalid: bool = False,
    amount_input: str | None = None,
) -> str:
    description = safe_text(payload.get("merchant"))
    amount = (
        MANUAL_FORM_AMOUNT_TYPING.format(value=safe_text(amount_input))
        if amount_input
        else amount_text(payload)
    )
    sections = [
        INCOME_FORM_TITLE if income_only else MANUAL_FORM_TITLE,
        MANUAL_FORM_HINT,
        "\n".join((
            MANUAL_FORM_DATE.format(value=date_text(payload.get("date")) or MANUAL_FORM_EMPTY),
            MANUAL_FORM_DESCRIPTION.format(value=description or MANUAL_FORM_EMPTY),
            MANUAL_FORM_AMOUNT.format(value=amount or MANUAL_FORM_EMPTY),
        )),
    ]
    if invalid:
        sections.append(MANUAL_FORM_INVALID)
    if prompt:
        sections.append(MANUAL_FORM_NEXT.format(prompt=prompt))
    sections.extend((MANUAL_FORM_FOOTER, mode))
    return "\n\n".join(sections)


def receipt_confirmation(
    payload: Mapping[str, object], confidence: float, *, header: str = RECEIPT_DETAILS
) -> str:
    details = operation_details(payload, {})
    category = safe_text(payload.get("suggested_category"))
    if category:
        details += "\n" + CATEGORY_LINE.format(value=category)
    text = f"{header}\n\n{details}"
    if confidence < 0.7:
        text += f"\n\n{LOW_CONFIDENCE}"
    return text


def expense_row(
    payload: Mapping[str, object], status: str, *, queued: bool = False
) -> str:
    fields = [
        date_text(payload.get("date"), short=True),
        safe_text(payload.get("merchant"), 80),
        amount_text(payload),
    ]
    account = payload.get("account")
    if not queued and isinstance(account, dict):
        fields.append(account_text(account.get("label"), 80))
    if queued:
        fields.append(QUEUE_WAITING if status == "queued" else STATUS_REVIEW)
        return ROW_SEPARATOR.join(field for field in fields if field)
    icon, suffix = {
        "submitted": (SAVED_ICON, ""),
        "dry_run": (TRIAL_ICON, "(prova)"),
        "queued": ("⏳", "in attesa di essere salvata"),
        "failed": (REVIEW_ICON, "da controllare"),
        "confirmed": ("⏳", "in corso"),
    }[status]
    if suffix:
        fields.append(suffix)
    return f"{icon} {ROW_SEPARATOR.join(field for field in fields if field)}"
