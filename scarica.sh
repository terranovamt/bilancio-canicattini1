#!/bin/sh
set -eu

fail() {
    printf 'ERRORE: %s\n' "$*" >&2
    exit 1
}

# Shared with automatico.sh: a lock whose PID is gone was left by a crash.
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

# Read the whole function before Git can update this script on disk.
main() {
    [ "$#" -eq 1 ] || fail "Uso: sh scarica.sh PERCORSO_PROGETTO_INSTALLATO"
    [ "$(id -u)" != "0" ] || fail "Esegui senza sudo: Git deve usare la chiave del tuo utente NAS."
    source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
    target_dir=$(CDPATH= cd -- "$1" && pwd -P) || fail "Progetto installato non trovato."
    [ -f "$target_dir/.env" ] || fail "Nella destinazione manca .env."
    case "$source_dir/" in
        "$target_dir/"*) fail "La copia Git deve stare fuori dal progetto attivo." ;;
    esac
    case "$target_dir/" in
        "$source_dir/"*) fail "Il progetto attivo deve stare fuori dalla copia Git." ;;
    esac
    command -v git >/dev/null 2>&1 || fail "Installa Git Server dal Centro pacchetti DSM."
    cd "$source_dir"
    git_dir=$(git rev-parse --absolute-git-dir) || fail "Questa cartella non e una copia Git."
    lock_dir="$git_dir/nas-deploy.lock"
    acquire_lock "$lock_dir" || fail "Un aggiornamento e gia in corso. Non avviarne un altro."
    trap 'release_lock "$lock_dir"' EXIT
    trap 'exit 1' HUP INT TERM
    [ "$(git symbolic-ref --short HEAD)" = "deploy" ] \
        || fail "La copia NAS deve essere sul ramo deploy."
    [ -z "$(git status --porcelain --untracked-files=normal)" ] \
        || fail "La copia Git contiene modifiche locali: non le sovrascrivo."
    git remote get-url origin >/dev/null || fail "Il remoto origin non e configurato."
    printf 'Scarico il ramo deploy. Nessun aggiornamento parte senza questo comando.\n'
    GIT_TERMINAL_PROMPT=0 git fetch --no-tags origin \
        refs/heads/deploy:refs/remotes/origin/deploy \
        || fail "Download fallito: controlla rete, chiave GitHub e presenza del ramo deploy."
    git merge --ff-only refs/remotes/origin/deploy \
        || fail "Il ramo NAS e divergente. Non eseguo reset o sovrascritture."
    revision=$(git rev-parse HEAD)
    commit_summary=$(git log -1 --format='%h - %s (%cd)' --date=format:'%d/%m/%Y %H:%M' HEAD)
    printf 'Versione selezionata: %s\n' "$revision"
    printf 'Ultimo commit: %s\n' "$commit_summary"
    # The updater may replace files; execute it from the fetched Git checkout.
    sudo sh "$source_dir/aggiorna.sh" "$target_dir" \
        || fail "Aggiornamento non completato. Conserva dati e backup; puoi ritentare lo stesso comando dopo la diagnosi."
    printf 'Versione installata: %s\n' "$revision"
    printf 'Ultimo commit installato: %s\n' "$commit_summary"
    printf 'Verifica ora /start su Telegram.\n'
}

main "$@"
