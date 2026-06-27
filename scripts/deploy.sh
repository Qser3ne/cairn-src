#!/usr/bin/env bash
set -euo pipefail

DEV_DIR="/home/qser3ne/Application/carin-dev"
PROD_DIR="/home/qser3ne/Application/carin"

usage() {
  cat <<'USAGE'
Usage: ./scripts/deploy.sh [-h|--help]

Directly overwrite the local production directory from carin-dev:
  /home/qser3ne/Application/carin

Excluded from synchronization:
  .git/
  .github/
  .agents/
  .superpowers/
  .worktrees/
  .pytest_cache/
  datas/
  datas.backup/

Options:
  -h, --help  Show this help text.
USAGE
}

log() {
  printf '[deploy] %s\n' "$*"
}

fail() {
  printf '[deploy] ERROR: %s\n' "$*" >&2
  exit 1
}

while (($#)); do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      fail "unknown argument: $1"
      ;;
  esac
  shift
done

[[ -d "$DEV_DIR" ]] || fail "$DEV_DIR does not exist"
[[ -d "$PROD_DIR" ]] || fail "$PROD_DIR does not exist"
command -v rsync >/dev/null 2>&1 || fail "rsync is required"

log "overwriting $PROD_DIR from $DEV_DIR"
rsync -a --delete \
  --exclude='.git/' \
  --exclude='.github/' \
  --exclude='.agents/' \
  --exclude='.superpowers/' \
  --exclude='.worktrees/' \
  --exclude='.pytest_cache/' \
  --exclude='datas/' \
  --exclude='datas.backup/' \
  --exclude='.gitignore' \
  "$DEV_DIR/" "$PROD_DIR/"

log "deployment completed"
