#!/bin/sh
set -eu

fail() {
    printf 'ERRORE: %s\n' "$*" >&2
    exit 1
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
command -v docker >/dev/null 2>&1 || fail "Docker non trovato. Controlla Container Manager."
docker compose version >/dev/null || fail "Docker Compose non disponibile."

lock_dir="$target_dir/.update.lock"
mkdir "$lock_dir" 2>/dev/null || fail "Un aggiornamento e gia in corso. Non avviarne un altro."
trap 'rmdir "$lock_dir"' EXIT
trap 'exit 1' HUP INT TERM

items="Dockerfile docker-compose.yml requirements.txt config.py logging_config.py .dockerignore README.md aggiorna.sh scarica.sh bot bilancio db storage vision docs"
for item in $items; do
    [ -e "$source_dir/$item" ] || fail "Pacchetto incompleto: manca $item."
    [ ! -L "$target_dir/$item" ] || fail "Destinazione con collegamento simbolico: $item."
    links=$(find "$source_dir/$item" -type l -print)
    [ -z "$links" ] || fail "Il pacchetto contiene collegamenti simbolici: $item."
    if [ -e "$target_dir/$item" ]; then
        links=$(find "$target_dir/$item" -type l -print)
        [ -z "$links" ] || fail "Il programma installato contiene collegamenti simbolici: $item."
    fi
done

cd "$target_dir"
# Validate privately: rendered Compose output contains credentials.
docker compose config --quiet || fail "Configurazione installata non valida."
running_bot=$(docker compose ps --status running -q bot) \
    || fail "Non riesco a verificare lo stato del bot."
if [ -n "$running_bot" ]; then
    dry_run=$(docker compose exec -T bot python -c \
        'from config import Settings; print("true" if Settings.from_env().dry_run else "false")') \
        || fail "Non riesco a verificare la modalita del bot in esecuzione."
    [ "$dry_run" = "true" ] || fail "Il bot e in modalita REALE: fermati e concorda prima l'aggiornamento."
else
    printf 'Il bot e spento: verifico la configurazione del prossimo avvio senza avviare il bot.\n'
fi
docker compose run --rm --no-deps --entrypoint python bot -c \
    'from config import Settings; assert Settings.from_env().dry_run, "DRY_RUN must be true before updating"' \
    || fail "La configurazione del prossimo avvio non e una simulazione valida."

backup_root="$target_dir/.update-backups"
[ ! -L "$backup_root" ] || fail "La cartella di backup non puo essere un collegamento simbolico."
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
    cp -R "$source_dir/$item" "$target_dir/"
done

docker compose config --quiet || fail "Nuova configurazione non valida. Bot precedente non ricreato; conserva il backup."
printf 'Costruisco la nuova immagine senza interrompere un eventuale bot in esecuzione.\n'
docker compose build bot || fail "Costruzione fallita. Bot precedente non ricreato; conserva il backup e segnala l'errore."
docker compose up -d --no-deps --force-recreate bot \
    || fail "Avvio fallito. Non cancellare i dati; conserva il backup e segnala l'errore."

printf 'Attendo il controllo di salute del container, fino a tre minuti.\n'
container_id=$(docker compose ps -q bot)
[ -n "$container_id" ] || fail "Container bot non trovato dopo l'avvio."
attempt=0
while [ "$attempt" -lt 60 ]; do
    status=$(docker inspect --format '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' "$container_id")
    case "$status" in
        "running healthy")
            printf 'Aggiornamento installato: container acceso e database verificato.\n'
            printf 'Ora invia /start su Telegram: la risposta conferma il collegamento al bot.\n'
            printf 'La cache dei menu riparte vuota. Nessuna prova sul portale e stata eseguita.\n'
            exit 0
            ;;
        "running starting") ;;
        *) fail "Container non pronto ($status). Esegui: sudo docker compose logs --tail=40 bot" ;;
    esac
    attempt=$((attempt + 1))
    sleep 3
done
fail "Controllo di salute non concluso. Esegui: sudo docker compose logs --tail=40 bot"
