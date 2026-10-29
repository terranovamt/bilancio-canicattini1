"""Italian Telegram copy shared by commands, workflows and notifications."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Mapping

USER_START = (
    "👋 Ciao! Qui tieni in ordine le spese.\n\n"
    "📷 Mandami la foto o il PDF di uno scontrino: lo leggo io e ti faccio "
    "controllare i dati prima di salvare.\n\n"
    "Cosa puoi fare:\n"
    "• /ultime – le ultime spese\n"
    "• /riepilogo – saldi delle casse\n"
    "• /help – tutti i comandi"
)
ADMIN_START = (
    '👋 Ciao! Sei collegato come amministratore.\n\n📷 Mandami uno scontrino per registrare una spesa.\n✍️ /testspesa – inserisci una spesa a mano\n📊 /riepilogo – saldi delle casse\n\n{mode}\n\n{version}\n\nAltri strumenti: /help'
)
MODE_TRIAL = "🧪 Modalità PROVA: non viene salvato nulla nel portale."
MODE_REAL = "⚠️ Modalità REALE: le spese confermate vengono salvate nel portale."
MANUAL_MODE_NOTICE = "/testspesa salva davvero, anche in modalità prova, dopo la tua conferma."
USER_HELP = (
    "📋 Cosa posso fare per te\n\n"
    "📷 Scontrino – mandami una foto o un PDF\n"
    "/ultime – le ultime 10 spese\n"
    "/riepilogo – saldi e andamento delle casse\n"
    "/coda – spese in attesa o da controllare\n"
    "/riprova – riprovo le spese in attesa\n"
    "/annulla – interrompo quello che stiamo facendo"
)
ADMIN_HELP = (
    '{user_help}\n\n🔧 Solo amministratore\n/testspesa – inserisci una spesa a mano\n/testbilancio – verifico il collegamento al portale\n    /aggiornaopzioni – aggiorno casse e tipologie\n/aggiorna – installo l\'ultima versione del bot\n/pulisci – svuoto coda, ultime spese e registri'
)
COMMAND_START = "Apri il menu del bot"
COMMAND_HELP = "Guida e comandi"
COMMAND_LATEST = "Le tue ultime spese"
COMMAND_SUMMARY = "Saldi e andamento delle casse"
COMMAND_CANCEL = "Interrompi quello che stiamo facendo"
COMMAND_QUEUE = "Spese in attesa o da controllare"
COMMAND_RETRY = "Riprova le spese in attesa"
COMMAND_MANUAL = "Inserisci davvero una spesa a mano"
COMMAND_HEALTH = "Verifica il collegamento al portale"
COMMAND_REFRESH = "Aggiorna casse e tipologie"
COMMAND_CLEAN = "Svuota spese e registri del bot"
COMMAND_UPDATE = "Installa l'ultima versione del bot"

VERSION_WITH_DATE = "{short} del {date}"
VERSION_RUNNING = "🔖 Versione attiva: {version}"
VERSION_UNKNOWN = "🔖 Versione attiva: non indicata (installata senza Git)."
VERSION_UNKNOWN_SHORT = "versione non indicata"
VERSION_NOT_CHECKED = "ℹ️ Il controllo automatico degli aggiornamenti non è ancora attivo."
VERSION_CHECK_FAILED = "⚠️ L'ultimo controllo degli aggiornamenti non è riuscito ({checked})."
VERSION_NEW = "🆕 Nuova versione disponibile: {summary}\nPer installarla: /aggiorna"
VERSION_LATEST = "✅ È l'ultima versione (controllo del {checked})."
VERSION_PUBLISHED = "ℹ️ Ultima versione pubblicata: {summary} (controllo del {checked})."
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
UPDATE_AVAILABLE = "🆕 È disponibile una nuova versione del bot: {summary}\nPer installarla: /aggiorna"
UPDATE_PROMPT = (
    "🔄 Vuoi installare l'ultima versione?\n\n{status}\n\n"
    "Il bot si riavvia per circa un minuto: controlla che nessuno stia registrando una spesa."
)
UPDATE_YES = "Sì, installa"
UPDATE_NO = "No, lascia stare"
UPDATE_REQUESTED = (
    "⏳ Richiesta registrata. Il NAS la esegue al prossimo controllo, "
    "di solito entro 10 minuti, e ti scrivo l'esito."
)
UPDATE_ALREADY_REQUESTED = "⏳ C'è già una richiesta in attesa: il NAS la esegue al prossimo controllo."
UPDATE_CANCELLED = "Ok, nessun aggiornamento."
UPDATE_LIVE_MODE = (
    "⚠️ Il bot è in modalità REALE: l'aggiornamento automatico è bloccato. "
    "Riporta prima DRY_RUN=true."
)
UPDATE_NOT_CONFIGURED = (
    "ℹ️ Il controllo automatico non è attivo sul NAS: la richiesta non verrebbe eseguita. "
    "Aggiorna con il comando manuale."
)
UPDATE_REQUEST_FAILED = "❌ Non riesco a registrare la richiesta. Riprova più tardi."
USER_KEYBOARD = (("/ultime", "/riepilogo"), ("/coda", "/help"), ("/annulla",))
ADMIN_KEYBOARD = (("/testspesa", "/testbilancio"), *USER_KEYBOARD)

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
RECEIPT_CONFIRMATION = "Ecco cosa ho letto, è tutto giusto?"
LOW_CONFIDENCE = "⚠️ Non sono sicuro di aver letto bene: controlla con attenzione."
MISSING_DATE = "Non sono riuscito a leggere la data. Me la scrivi? (es. 29/10/2026)"
MISSING_MERCHANT = "Non sono riuscito a leggere il negozio. Come si chiama?"
MISSING_AMOUNT = "Non sono riuscito a leggere il totale. Quanto hai speso? (es. 12,50)"
EDIT_DATE = "Qual è la data giusta? (es. 29/10/2026)"
EDIT_MERCHANT = "Come si chiama il negozio?"
EDIT_AMOUNT = "Qual è l'importo giusto? (es. 12,50)"
EDIT_CATEGORY = 'Che categoria vuoi? Scrivi "-" per lasciarla vuota.'
MANUAL_DATE = "📅 Qual è la data? (es. 29/10/2026)"
MANUAL_MERCHANT = "✏️ Come descrivo la spesa?"
MANUAL_AMOUNT = "💶 Qual è l'importo? (es. 12,50)"
MANUAL_DESCRIPTION = "Prova Telegram"
MANUAL_CONFIRMATION = (
    "✍️ Spesa a mano\n\n{details}\n\n"
    "Va bene così o vuoi cambiare qualcosa?\n"
    "⚠️ Verrà salvata davvero nel portale: prima ti chiedo un'ultima conferma."
)
MANUAL_FINAL = (
    'Ultimo controllo prima di salvare:\n\n{details}\n\n⚠️ Se confermi, la spesa viene salvata DAVVERO nel portale.'
)
MANUAL_FAILED = (
    "❌ Non sono riuscito a salvare la spesa e non riproverò da solo.\n\n"
    "{details}\n\n👉 Controlla nel portale se compare. Se non c'è, ripeti /testspesa."
)
MANUAL_FORMAT = (
    "Non ho capito 🙂 Scrivi data, descrizione e importo separati da «|», "
    "per esempio: 26/10/2026 | Pranzo | 12,50"
)
ACCOUNTS_PROGRESS = "⏳ Cerco le casse…"
ACCOUNT_PROMPT = "🏦 Da quale cassa è uscita la spesa? (1 di 3)"
DIRECTION_PROMPT = "↔️ È un'entrata o un'uscita? (2 di 3)"
TYPE_PROGRESS = (
    "⏳ Carico le tipologie, può servire fino a un minuto. "
    "Aspetta senza premere altro 🙏"
)
TYPE_POPULAR = "🏷️ Che tipo di spesa è? (3 di 3)\nTi mostro le più usate."
TYPE_ALL = "🏷️ Che tipo di spesa è? (3 di 3)"
TYPE_MORE = "🏷️ Altre tipologie"
MORE_TYPES_BUTTON = '➕ Mostra altre ({count})'
INCOME = "⬇️ Entrata"
EXPENSE = "⬆️ Uscita"
SAVE_PROGRESS = (
    "⏳ Salvo nel portale e controllo che sia andato tutto bene… "
    "Un attimo, non premere altro."
)
TRIAL_PROGRESS = (
    "🧪 Faccio la prova nel portale (non salvo nulla)… "
    "Un attimo, non premere altro."
)
SAVE_BUTTON = "✅ Salva davvero"
MANUAL_CONFIRM_BUTTON = "✅ Va bene"
RECEIPT_CONFIRM_BUTTON = "✅ Sì, è giusto"
DATE_BUTTON = EDIT_DATE_BUTTON = "📅 Data"
AMOUNT_BUTTON = EDIT_AMOUNT_BUTTON = "💶 Importo"
DESCRIPTION_BUTTON = "✏️ Descrizione"
EDIT_MERCHANT_BUTTON = "🏪 Negozio"
EDIT_CATEGORY_BUTTON = "🏷️ Categoria"
CANCEL_BUTTON = "Annulla"
NO_CATEGORY_BUTTON = "Senza categoria"

LATEST_HEADER = "🧾 Le tue ultime spese"
LATEST_EMPTY = "Ancora nessuna spesa. Mandami uno scontrino per iniziare 📷"
QUEUE_HEADER = "⏳ Spese ancora da salvare o da controllare"
QUEUE_EMPTY = "Tutto a posto: non ci sono spese in attesa ✅"
QUEUE_FOOTER = "Scrivi /riprova per riprovare quelle in attesa."
QUEUE_WAITING = "in attesa"
NOTHING_TO_RETRY = "Non c'è niente da riprovare."
RETRY_DONE = (
    'Ho riprovato: {saved} salvata ✅, {pending} ancora in attesa ⏳, {failed} da controllare ⚠️.'
)
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
    "⚠️ Una spesa non è stata salvata dopo vari tentativi. Controlla con /coda."
)
ADMIN_TRIAL_UNCONFIRMED = (
    "⚠️ La modalità prova non è stata rispettata. Controlla il portale prima di riprovare."
)
HEALTH_OK = "✅ Il portale risponde correttamente. Non ho inserito nulla."
HEALTH_MANUAL = (
    '⚠️ Il portale chiede una verifica manuale (es. un codice). Non ho inserito nulla.\nDettagli: {folder}{diagnostic_file}'
)
HEALTH_FAILED = (
    '❌ Non riesco ad accedere al portale. Non ho inserito nulla.\nDettagli: {folder}'
)
ADMIN_DIAGNOSTIC = '{message} ({filename})'
DIAGNOSTIC_DETAILS = "Dettagli: {path}"
REFRESH_PROGRESS = (
    "🔄 Aggiorno casse e tipologie in sottofondo, ti scrivo quando ho finito. "
    "Non inserisco nessuna spesa."
)
REFRESH_DONE = '✅ Fatto! Trovate {count} casse.'
REFRESH_ROW = '• {account}: {income} entrate, {expense} uscite'
UNREAD_COUNT = "da rileggere"
REFRESH_PARTIAL = "Alcune tipologie non si sono caricate: le rileggo quando servono."
REFRESH_FAILED = "❌ Non riesco a leggere le casse dal portale. Riprova più tardi."
CLEAN_PROMPT = (
    "🧹 Vuoi svuotare coda, ultime spese e registri del bot?\n"
    "Il portale e le ricevute archiviate non vengono toccati."
)
CLEAN_YES = "Sì, pulisci"
CLEAN_NO = "No, lascia stare"
CLEAN_DONE = (
    '🧹 Fatto! Eliminate {expenses} spese ({pending} in attesa) e {files} file temporanei. Portale e ricevute intatti.'
)
CLEAN_CANCELLED = "Ok, non ho toccato niente."
CANCELLED = "Ok, ho annullato. Quando vuoi, mandami un altro scontrino."
NOTHING_TO_CANCEL = "Non c'è niente da annullare."
RECEIPT_ALREADY_ACTIVE = (
    "Stiamo già lavorando su uno scontrino: completalo oppure scrivi /annulla."
)
MANUAL_RECEIPT_ACTIVE = "Prima finiamo lo scontrino in corso, oppure scrivi /annulla."
MANUAL_PREVIOUS_CANCELLED = "Avevi una prova rimasta aperta: l'ho chiusa."
USE_BUTTONS = "Usa i pulsanti qui sopra, oppure scrivi /annulla."
ANALYZER_UNAVAILABLE = "Al momento non riesco a leggere gli scontrini. Riprova più tardi."
SEND_RECEIPT = "Mandami una foto o un PDF dello scontrino 📷"
FILE_TOO_LARGE = "Il file è troppo grande (massimo 20 MB). Ne provi uno più leggero?"
FILE_UNREADABLE = (
    "Non riesco ad aprire questo file. Controlla che sia una foto o un PDF e riprova."
)
RECEIPT_DUPLICATE = "Questo scontrino l'ho già ricevuto, non lo rielaboro."
ANALYSIS_FAILED = (
    "Non sono riuscito a leggere lo scontrino. Prova con una foto più nitida, "
    "oppure inserisci i dati a mano."
)
ADMIN_ANALYSIS_FAILED = (
    "L'analisi locale di una ricevuta non è riuscita. Il file non è stato inviato a servizi online."
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
PORTAL_READ_FAILED = (
    'Non riesco a leggere {subject} dal portale adesso. Non ho salvato nulla e i dati dello scontrino restano qui: premi «Sì, è giusto» per riprovare oppure Annulla.'
)
PORTAL_MANUAL_READ_FAILED = (
    'Non riesco a leggere {subject} dal portale. Ho annullato la prova, non è stato inserito nulla. Riprova più tardi con /testspesa.'
)
ACCOUNTS_SUBJECT = "le casse"
TYPES_SUBJECT = "le tipologie"
ADMIN_ACCOUNTS_FAILED = 'Non riesco a leggere le casse. Dettagli: {folder}'
ADMIN_TYPES_FAILED = 'Non riesco a leggere le tipologie. Dettagli: {folder}'
ADMIN_UNVERIFIED = '⚠️ Registrazione non verificata. Controlla {folder} e /coda.'
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
SUMMARY_HEADER = '📊 Bilancio {year}\nDati aggiornati adesso dal portale.'
PREVIOUS_YEAR = '⬅️ Anno {year}'
NEXT_YEAR = 'Anno {year} ➡️'
REFRESH_BUTTON = "🔄 Aggiorna"
DETAILS_BUTTON = "📈 Dettagli"
ACCOUNT_HEADER = '🏦 {account}'
ACCOUNT_BALANCE = '💰 Saldo: {balance}'
ACCOUNT_TOTALS = "Entrate {income} · uscite {expense}"
OPENING_BALANCE = "Saldo iniziale: {amount}"
MONTHLY_HEADER = "📈 Andamento mensile"
OPERATIONS_HEADER = "🧾 Ultime operazioni"
MONTHLY_ROW = '• {month} {year}: entrate {income} · uscite {expense} → saldo {balance}'
PARTIAL_LEDGER = '⚠️ Il portale mostra solo {visible} righe su {total}: i dati sono parziali.'
NO_MOVEMENTS = "Nessuna operazione dall'inizio dell'anno."
LEDGER_ROW = '• {day:%d/%m} {description}: {amount}'
LAST_MOVEMENT = "Ultima operazione: {day:%d/%m} {description} {amount}"
NO_ACCOUNTS = '{header}\n\nNon trovo casse nel portale.'
MONTH_NAMES = (
    "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno",
    "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre",
)
EMPTY_VALUE = "—"
EURO_AMOUNT = "{sign}{digits} €"
ELLIPSIS = '{text}…'
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
ROW_SEPARATOR = " · "
INCOME_LABEL = "entrata"
EXPENSE_LABEL = "uscita"


def safe_text(value: object, limit: int = 200) -> str:
    return " ".join(str(value).split())[:limit] if value is not None else ""


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
        (DESCRIPTION_LINE if payload.get("manual_entry") is True else MERCHANT_LINE,
         safe_text(payload.get("merchant"))),
    ):
        if value:
            lines.append(template.format(value=value))
    amount = amount_text(payload)
    if amount:
        direction = selections.get("direction")
        if direction:
            amount = DIRECTION_AMOUNT.format(amount=amount, direction=safe_text(direction))
        lines.append(AMOUNT_LINE.format(value=amount))
    for key, template in (
        ("account", ACCOUNT_LINE), ("transaction_type", CATEGORY_LINE),
        ("unit", UNIT_LINE), ("category", PORTAL_CATEGORY_LINE),
    ):
        if key == "unit" and unit_left_empty:
            continue
        option = selections.get(key)
        value = safe_text(option.get("label")) if isinstance(option, dict) else ""
        if value:
            lines.append(template.format(value=value))
    notes = safe_text(selections.get("portal_notes"))
    if notes:
        lines.append(NOTES_LINE.format(value=notes))
    return "\n".join(lines)


def receipt_confirmation(payload: Mapping[str, object], confidence: float) -> str:
    details = operation_details(payload, {})
    category = safe_text(payload.get("suggested_category"))
    if category:
        details += "\n" + CATEGORY_LINE.format(value=category)
    text = f"{RECEIPT_CONFIRMATION}\n\n{details}"
    if confidence < 0.7:
        text += f"\n\n{LOW_CONFIDENCE}"
    return text


def expense_row(payload: Mapping[str, object], status: str, *, queued: bool = False) -> str:
    fields = [
        date_text(payload.get("date"), short=True),
        safe_text(payload.get("merchant"), 80),
        amount_text(payload),
    ]
    account = payload.get("account")
    if not queued and isinstance(account, dict):
        fields.append(safe_text(account.get("label"), 80))
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
