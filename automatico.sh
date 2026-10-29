#!/bin/sh
set -eu

fail() {
    printf 'ERRORE: %s\n' "$*" >&2
    exit 1
}

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

# Git must run as the checkout owner: the deploy key lives in that user's home.
as_owner() {
    if [ "$(id -u)" = "$owner_uid" ]; then
        "$@"
    else
        sudo -u "$owner_name" -H "$@"
    fi
}

owner_git() {
    as_owner env GIT_TERMINAL_PROMPT=0 git -C "$source_dir" "$@"
}

write_status() {
    name=$1
    shift
    [ ! -L "$status_dir" ] || return 1
    tmp=$(mktemp "$status_dir/.$name.XXXXXX") || return 1
    printf '%s\n' "$@" > "$tmp"
    chmod 644 "$tmp"
    mv -f "$tmp" "$status_dir/$name"
}

record_install() {
    write_status install \
        "result=$1" \
        "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        "revision=$available" \
        "summary=$available_summary" \
        "installed_revision=$installed" \
        "reason=$2" \
        || printf 'Avviso: esito non registrato in data/update.\n' >&2
}

# Read the whole function before Git can update this script on disk.
main() {
    [ "$#" -eq 2 ] || fail "Uso: sh automatico.sh controlla|installa PERCORSO_PROGETTO_INSTALLATO"
    mode=$1
    case "$mode" in
        controlla|installa) ;;
        *) fail "Modalita sconosciuta: usa controlla oppure installa." ;;
    esac
    source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
    target_dir=$(CDPATH= cd -- "$2" && pwd -P) || fail "Progetto installato non trovato."
    [ -f "$target_dir/.env" ] || fail "Nella destinazione manca .env."
    [ -d "$target_dir/data" ] || fail "Nella destinazione manca la cartella data."
    [ ! -L "$target_dir/data" ] || fail "La cartella data non puo essere un collegamento simbolico."
    case "$source_dir/" in
        "$target_dir/"*) fail "La copia Git deve stare fuori dal progetto attivo." ;;
    esac
    case "$target_dir/" in
        "$source_dir/"*) fail "Il progetto attivo deve stare fuori dalla copia Git." ;;
    esac
    command -v git >/dev/null 2>&1 || fail "Installa Git Server dal Centro pacchetti DSM."
    owner_uid=$(stat -c %u "$source_dir")
    owner_name=$(stat -c %U "$source_dir")
    git_dir=$(owner_git rev-parse --absolute-git-dir) || fail "La copia Git non e valida."
    lock_dir="$git_dir/nas-deploy.lock"
    if ! acquire_lock "$lock_dir"; then
        printf 'Un aggiornamento e gia in corso: salto questo controllo.\n'
        exit 0
    fi
    trap 'release_lock "$lock_dir"' EXIT
    trap 'exit 1' HUP INT TERM

    status_dir="$target_dir/data/update"
    [ ! -L "$status_dir" ] || fail "La cartella data/update non puo essere un collegamento simbolico."
    if [ ! -d "$status_dir" ]; then
        mkdir "$status_dir"
        if [ "$(id -u)" = "0" ]; then
            chown "$(stat -c %u:%g "$target_dir/data")" "$status_dir"
        fi
    fi
    requested=no
    if [ -f "$status_dir/request" ]; then
        requested=yes
        rm -f "$status_dir/request"
        printf 'Trovata una richiesta di installazione da Telegram.\n'
    fi

    fetch_error=""
    owner_git fetch --no-tags origin refs/heads/deploy:refs/remotes/origin/deploy \
        || fetch_error=download
    available=$(owner_git rev-parse --verify -q refs/remotes/origin/deploy) || available=""
    available_summary=""
    if [ -n "$available" ]; then
        available_summary=$(owner_git log -1 --format='%h - %s (%cd)' \
            --date=format:'%d/%m/%Y %H:%M' "$available")
    fi
    write_status check \
        "checked_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        "available_revision=$available" \
        "available_summary=$available_summary" \
        "error=$fetch_error" \
        || printf 'Avviso: stato del controllo non registrato.\n' >&2

    installed=$(read_field installed_revision "$status_dir/install")
    case "$installed" in
        *[!0-9a-f]*) installed="" ;;
    esac
    last_result=$(read_field result "$status_dir/install")
    last_revision=$(read_field revision "$status_dir/install")

    if [ -n "$fetch_error" ] || [ -z "$available" ]; then
        [ "$requested" = "no" ] || record_install skipped download
        fail "Download fallito: controlla rete, chiave GitHub senza passphrase e ramo deploy."
    fi
    printf 'Ultima versione pubblicata: %s\n' "$available_summary"
    if [ "$requested" = "no" ] && [ "$mode" = "controlla" ]; then
        exit 0
    fi
    if [ "$available" = "$installed" ]; then
        [ "$requested" = "no" ] || record_install skipped up_to_date
        printf 'Versione gia installata: nessun aggiornamento.\n'
        exit 0
    fi
    if [ "$requested" = "no" ] && [ "$last_result" != "ok" ] && [ "$last_revision" = "$available" ]; then
        printf 'Questa versione non si e installata in precedenza: attendo una nuova versione o una richiesta da Telegram.\n'
        exit 0
    fi

    if [ "$(owner_git symbolic-ref --short HEAD)" != "deploy" ] \
        || [ -n "$(owner_git status --porcelain --untracked-files=normal)" ]; then
        record_install failed dirty
        fail "La copia Git non e pulita sul ramo deploy: non la sovrascrivo."
    fi
    if ! owner_git merge --ff-only refs/remotes/origin/deploy; then
        record_install failed dirty
        fail "Il ramo NAS e divergente. Non eseguo reset o sovrascritture."
    fi
    printf 'Installo %s\n' "$available_summary"
    # aggiorna.sh records the final outcome, including automatic rollback.
    sh "$source_dir/aggiorna.sh" "$target_dir" \
        || fail "Aggiornamento non completato: l'esito e registrato per Telegram."
    printf 'Versione installata: %s\n' "$available"
}

main "$@"
