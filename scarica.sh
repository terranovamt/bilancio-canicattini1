#!/bin/sh
set -eu

fail() {
    printf 'ERRORE: %s\n' "$*" >&2
    exit 1
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
    mkdir "$lock_dir" 2>/dev/null || fail "Un aggiornamento e gia in corso. Non avviarne un altro."
    trap 'rmdir "$lock_dir"' EXIT
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
    printf 'Versione selezionata: %s\n' "$revision"
    # The updater may replace files; execute it from the fetched Git checkout.
    sudo sh "$source_dir/aggiorna.sh" "$target_dir" \
        || fail "Aggiornamento non completato. Conserva dati e backup; puoi ritentare lo stesso comando dopo la diagnosi."
    printf 'Versione installata: %s\n' "$revision"
    printf 'Verifica ora /start su Telegram.\n'
}

main "$@"
