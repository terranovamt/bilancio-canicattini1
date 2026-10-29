#!/bin/sh
set -eu

fail() {
    printf 'ERRORE: %s\n' "$*" >&2
    exit 1
}

health_interval=3
health_attempts=60
items="Dockerfile docker-compose.yml requirements.txt config.py logging_config.py .dockerignore README.md aggiorna.sh scarica.sh automatico.sh bot bilancio db storage vision docs"

# Lock directories hold the owner PID so a lock left by a crash can be removed safely.
acquire_lock() {
    if mkdir "$1" 2>/dev/null; then
        printf '%s\n' "$$" > "$1/pid"
        return 0
    fi
    old_pid=$(cat "$1/pid" 2>/dev/null || true)
    case "$old_pid" in
        ''|*[!0-9]*) return 1 ;;
    esac
    if kill -0 "$old_pid" 2>/dev/null || [ -d "/proc/$old_pid" ]; then
        return 1
    fi
    printf 'Rimuovo il blocco lasciato da un aggiornamento interrotto (processo %s non attivo).\n' "$old_pid"
    rm -f "$1/pid" && rmdir "$1" 2>/dev/null || return 1
    mkdir "$1" 2>/dev/null || return 1
    printf '%s\n' "$$" > "$1/pid"
}

release_lock() {
    rm -f "$1/pid"
    rmdir "$1" 2>/dev/null || true
}

read_field() {
    [ -f "$2" ] || return 0
    sed -n "s/^$1=//p" "$2" | head -n 1
}

prepare_status_dir() {
    [ ! -L "$target_dir/data" ] || fail "La cartella data non puo essere un collegamento simbolico."
    [ ! -L "$status_dir" ] || fail "La cartella data/update non puo essere un collegamento simbolico."
    if [ ! -d "$status_dir" ]; then
        mkdir "$status_dir"
        # The bot writes install requests here, so it must keep the data owner.
        if [ "$(id -u)" = "0" ]; then
            chown "$(stat -c %u:%g "$target_dir/data")" "$status_dir"
        fi
    fi
}

record_install() {
    [ ! -L "$status_dir" ] || return 1
    tmp=$(mktemp "$status_dir/.install.XXXXXX") || return 1
    {
        printf 'result=%s\n' "$1"
        printf 'finished_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        printf 'revision=%s\n' "$revision"
        printf 'summary=%s\n' "$revision_summary"
        printf 'installed_revision=%s\n' "$installed_after"
        printf 'reason=%s\n' "$2"
    } > "$tmp"
    chmod 644 "$tmp"
    mv -f "$tmp" "$status_dir/install"
}

# Record the outcome for Telegram, then stop.
abort() {
    record_install failed "$1" || printf 'Avviso: esito non registrato in data/update.\n' >&2
    fail "$2"
}

restore_code() {
    printf 'Ripristino i file del programma precedente dal backup.\n' >&2
    for item in $items; do
        rm -rf "${target_dir:?}/$item"
        if [ -e "$backup_dir/$item" ]; then
            cp -Rp "$backup_dir/$item" "$target_dir/"
        fi
    done
}

wait_healthy() {
    container_id=$(docker compose ps -q bot) || return 1
    health_status="assente"
    [ -n "$container_id" ] || return 1
    attempt=0
    while [ "$attempt" -lt "$health_attempts" ]; do
        health_status=$(docker inspect --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' "$container_id") \
            || return 1
        case "$health_status" in
            "running healthy") return 0 ;;
            "running starting") ;;
            *) return 1 ;;
        esac
        attempt=$((attempt + 1))
        sleep "$health_interval"
    done
    health_status="controllo di salute scaduto"
    return 1
}

rollback() {
    printf 'Container non pronto (%s): ripristino la versione precedente.\n' "$health_status" >&2
    restore_code
    if [ -z "$rollback_image" ]; then
        record_install failed rollback || true
        fail "Nessuna immagine precedente da ripristinare. Esegui: sudo docker compose logs --tail=40 bot"
    fi
    docker image tag "$rollback_image" "$image_name" || {
        record_install failed rollback || true
        fail "Ripristino dell'immagine fallito. Esegui: sudo docker compose logs --tail=40 bot"
    }
    if [ -n "$running_bot" ]; then
        if docker compose up -d --no-deps --force-recreate --no-build bot && wait_healthy; then
            record_install rolled_back start || true
            fail "La nuova versione non e partita: ripristinata e riavviata la versione precedente."
        fi
        record_install failed rollback || true
        fail "Anche la versione precedente non e pronta ($health_status). Esegui: sudo docker compose logs --tail=40 bot"
    fi
    docker compose up --no-start --no-deps --force-recreate --no-build bot || {
        record_install failed rollback || true
        fail "Ripristino della versione precedente fallito. Esegui: sudo docker compose logs --tail=40 bot"
    }
    record_install rolled_back start || true
    fail "La nuova versione non e partita: ripristinata la versione precedente, lasciata spenta come prima."
}

[ "$#" -eq 1 ] || fail "Uso: sudo sh aggiorna.sh PERCORSO_PROGETTO_INSTALLATO"
source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
target_dir=$(CDPATH= cd -- "$1" && pwd -P) || fail "Cartella installata non trovata."
[ "$target_dir" != "/" ] || fail "La destinazione non puo essere la radice del NAS."
case "$source_dir/" in
    "$target_dir/"*) fail "Estrai lo ZIP fuori dal progetto installato, non sopra i dati." ;;
esac
case "$target_dir/" in
    "$source_dir/"*) fail "La destinazione non puo essere dentro il pacchetto estratto." ;;
esac
[ -f "$target_dir/.env" ] || fail "Nella destinazione manca .env: non sembra il progetto installato."
[ -f "$target_dir/docker-compose.yml" ] || fail "Nella destinazione manca docker-compose.yml."
[ -d "$target_dir/data" ] || fail "Nella destinazione manca la cartella data."
[ -d "$target_dir/scontrini" ] || fail "Nella destinazione manca la cartella scontrini."

status_dir="$target_dir/data/update"
prepare_status_dir
lock_dir="$target_dir/.update.lock"
acquire_lock "$lock_dir" || fail "Un aggiornamento e gia in corso. Non avviarne un altro."
trap 'release_lock "$lock_dir"' EXIT
trap 'exit 1' HUP INT TERM

revision=""
revision_date=""
revision_subject=""
revision_summary=""
if [ -e "$source_dir/.git" ] && command -v git >/dev/null 2>&1; then
    # Root may run this on a checkout owned by the NAS user.
    if revision=$(git -c safe.directory="$source_dir" -C "$source_dir" rev-parse HEAD 2>/dev/null); then
        revision_date=$(git -c safe.directory="$source_dir" -C "$source_dir" log -1 --format=%cI HEAD)
        revision_subject=$(git -c safe.directory="$source_dir" -C "$source_dir" log -1 --format=%s HEAD)
        revision_summary=$(git -c safe.directory="$source_dir" -C "$source_dir" log -1 \
            --format='%h - %s (%cd)' --date=format:'%d/%m/%Y %H:%M' HEAD)
    else
        revision=""
    fi
fi
installed_after=$(read_field installed_revision "$status_dir/install")
case "$installed_after" in
    *[!0-9a-f]*) installed_after="" ;;
esac

for item in $items; do
    [ -e "$source_dir/$item" ] || abort package "Pacchetto incompleto: manca $item."
    [ ! -L "$target_dir/$item" ] || abort package "Destinazione con collegamento simbolico: $item."
    links=$(find "$source_dir/$item" -type l -print)
    [ -z "$links" ] || abort package "Il pacchetto contiene collegamenti simbolici: $item."
    if [ -e "$target_dir/$item" ]; then
        links=$(find "$target_dir/$item" -type l -print)
        [ -z "$links" ] || abort package "Il programma installato contiene collegamenti simbolici: $item."
    fi
done
command -v docker >/dev/null 2>&1 || abort "" "Docker non trovato. Controlla Container Manager."
docker compose version >/dev/null || abort "" "Docker Compose non disponibile."

cd "$target_dir"
# Validate privately: rendered Compose output contains credentials.
docker compose config --quiet || abort config "Configurazione installata non valida."
running_bot=$(docker compose ps --status running -q bot) \
    || abort "" "Non riesco a verificare lo stato del bot."
if [ -n "$running_bot" ]; then
    dry_run=$(docker compose exec -T bot python -c \
        'from config import Settings; print("true" if Settings.from_env().dry_run else "false")') \
        || abort "" "Non riesco a verificare la modalita del bot in esecuzione."
    [ "$dry_run" = "true" ] || abort live_mode "Il bot e in modalita REALE: fermati e concorda prima l'aggiornamento."
else
    printf 'Il bot e spento: verifico la configurazione del prossimo avvio senza avviare il bot.\n'
fi
docker compose run --rm --no-deps --entrypoint python bot -c \
    'from config import Settings; assert Settings.from_env().dry_run, "DRY_RUN must be true before updating"' \
    || abort live_mode "La configurazione del prossimo avvio non e una simulazione valida."

# Keep the current image under a rollback tag: the build moves the normal tag.
rollback_image=""
image_name=""
previous_container=$(docker compose ps -a -q bot) || abort "" "Non riesco a leggere il container attuale."
if [ -n "$previous_container" ]; then
    previous_image=$(docker inspect --format '{{.Image}}' "$previous_container") || previous_image=""
    image_name=$(docker inspect --format '{{.Config.Image}}' "$previous_container") || image_name=""
    if [ -n "$previous_image" ] && [ -n "$image_name" ]; then
        case "$image_name" in
            *:*) rollback_image="${image_name%:*}:rollback" ;;
            *) rollback_image="$image_name:rollback" ;;
        esac
        docker image tag "$previous_image" "$rollback_image" || rollback_image=""
    fi
fi
[ -n "$rollback_image" ] || printf 'Avviso: nessuna immagine precedente da conservare, il ripristino automatico non sara possibile.\n'

backup_root="$target_dir/.update-backups"
[ ! -L "$backup_root" ] || abort "" "La cartella di backup non puo essere un collegamento simbolico."
umask 077
mkdir -p "$backup_root"
backup_dir="$backup_root/$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir "$backup_dir"
printf 'Conservo una copia dei file del programma in %s\n' "$backup_dir"
for item in $items; do
    if [ -e "$target_dir/$item" ]; then
        cp -Rp "$target_dir/$item" "$backup_dir/"
    fi
done

printf 'Aggiorno solo il programma. Credenziali e cartelle dati non vengono copiate o cancellate.\n'
umask 022
for item in $items; do
    rm -rf "${target_dir:?}/$item"
    cp -R "$source_dir/$item" "$target_dir/"
done

if ! docker compose config --quiet; then
    restore_code
    abort config "Nuova configurazione non valida. Ripristinati i file precedenti; il bot non e stato toccato."
fi
printf 'Costruisco la nuova immagine senza interrompere un eventuale bot in esecuzione.\n'
if ! docker compose build \
    --build-arg "APP_REVISION=$revision" \
    --build-arg "APP_COMMIT_DATE=$revision_date" \
    --build-arg "APP_COMMIT_SUBJECT=$revision_subject" \
    bot; then
    restore_code
    abort build "Costruzione fallita. Ripristinati i file precedenti; il bot attuale non e stato toccato."
fi

health_status="avvio fallito"
if docker compose up -d --no-deps --force-recreate bot; then
    printf 'Attendo il controllo di salute del container, fino a tre minuti.\n'
    if wait_healthy; then
        installed_after="$revision"
        record_install ok "" || printf 'Avviso: esito non registrato in data/update.\n' >&2
        printf 'Aggiornamento installato: container acceso e database verificato.\n'
        printf 'Ora invia /start su Telegram: la risposta mostra la versione attiva.\n'
        printf 'La cache dei menu riparte vuota. Nessuna prova sul portale e stata eseguita.\n'
        exit 0
    fi
fi
rollback
