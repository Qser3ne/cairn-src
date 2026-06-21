#!/usr/bin/env bash
set -euo pipefail

DEV_DIR="/home/qser3ne/Application/carin-dev"
PROD_DIR="/home/qser3ne/Application/carin"
BRANCH="main"
HEALTH_URL="http://127.0.0.1:8000/projects"

RUN_TESTS=1
BACKUP_DATA=1

usage() {
  cat <<'USAGE'
Usage: ./scripts/deploy.sh [--skip-tests] [--no-backup] [-h|--help]

Deploy committed carin-dev code to the local production checkout at:
  /home/qser3ne/Application/carin

Options:
  --skip-tests  Skip the dev test suite before deployment.
  --no-backup   Do not copy production datas/ before restarting services.
  -h, --help    Show this help text.
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
    --skip-tests)
      RUN_TESTS=0
      ;;
    --no-backup)
      BACKUP_DATA=0
      ;;
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

require_git_repo() {
  local dir="$1"
  git -C "$dir" rev-parse --is-inside-work-tree >/dev/null 2>&1 || fail "$dir is not a Git repository"
}

git_origin() {
  local dir="$1"
  git -C "$dir" remote get-url origin
}

current_branch() {
  local dir="$1"
  git -C "$dir" rev-parse --abbrev-ref HEAD
}

ensure_clean_tracked_state() {
  local dir="$1"
  local name="$2"

  if ! git -C "$dir" diff --quiet; then
    fail "$name has unstaged tracked changes. Commit, stash, or discard them before deploying."
  fi

  if ! git -C "$dir" diff --cached --quiet; then
    fail "$name has staged changes. Commit or unstage them before deploying."
  fi
}

require_git_repo "$DEV_DIR"
require_git_repo "$PROD_DIR"

DEV_ORIGIN="$(git_origin "$DEV_DIR")"
PROD_ORIGIN="$(git_origin "$PROD_DIR")"
[[ "$DEV_ORIGIN" == "$PROD_ORIGIN" ]] || fail "origin mismatch: dev=$DEV_ORIGIN prod=$PROD_ORIGIN"

[[ "$(current_branch "$DEV_DIR")" == "$BRANCH" ]] || fail "$DEV_DIR must be on branch $BRANCH"
[[ "$(current_branch "$PROD_DIR")" == "$BRANCH" ]] || fail "$PROD_DIR must be on branch $BRANCH"

log "checking dev tracked state"
ensure_clean_tracked_state "$DEV_DIR" "dev checkout"

log "fetching origin in dev"
git -C "$DEV_DIR" fetch origin

if ((RUN_TESTS)); then
  log "running dev test suite"
  (cd "$DEV_DIR/cairn" && uv run --group dev pytest -s)
else
  log "skipping dev test suite"
fi

TARGET_COMMIT="$(git -C "$DEV_DIR" rev-parse "$BRANCH")"
log "pushing dev $BRANCH to origin"
git -C "$DEV_DIR" push origin "$BRANCH"

log "checking production tracked state"
ensure_clean_tracked_state "$PROD_DIR" "production checkout"

log "updating production checkout"
git -C "$PROD_DIR" fetch origin
git -C "$PROD_DIR" pull --ff-only origin "$BRANCH"
PROD_COMMIT="$(git -C "$PROD_DIR" rev-parse "$BRANCH")"
[[ "$PROD_COMMIT" == "$TARGET_COMMIT" ]] || fail "production HEAD is $PROD_COMMIT, expected $TARGET_COMMIT"

if ((BACKUP_DATA)); then
  if [[ -d "$PROD_DIR/datas" ]]; then
    BACKUP_DIR="$PROD_DIR/datas.backup/$(date +%Y%m%d-%H%M%S)"
    log "backing up production datas/ to $BACKUP_DIR"
    mkdir -p "$PROD_DIR/datas.backup"
    cp -a "$PROD_DIR/datas" "$BACKUP_DIR"
  else
    log "production datas/ does not exist; skipping backup"
  fi
else
  log "skipping production datas/ backup"
fi

log "rebuilding and restarting production services"
(cd "$PROD_DIR" && ./start.sh)

log "checking service health at $HEALTH_URL"
curl -f "$HEALTH_URL" >/dev/null

log "deployment completed"
