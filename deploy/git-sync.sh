#!/usr/bin/env bash
# Sinkronisasi otomatis VPS -> GitHub.
#
# Hanya commit & push kalau ada perubahan DAN unit test lolos.
# Dijalankan oleh cron (lihat deploy/screener2-git.cron).
#
# Log: /home/ubuntu/screener2-git.log
# Uji manual: ./deploy/git-sync.sh

set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SRC"

BRANCH="${GIT_SYNC_BRANCH:-main}"
REMOTE="${GIT_SYNC_REMOTE:-origin}"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "bukan repo git: $SRC" >&2
  exit 1
fi

# Tidak ada perubahan (termasuk file untracked)?
if git diff --quiet && git diff --cached --quiet \
   && [[ -z "$(git ls-files --others --exclude-standard)" ]]; then
  exit 0
fi

# Jangan pernah mendorong rahasia/DB walau .gitignore salah diubah.
if git ls-files --error-unmatch .env >/dev/null 2>&1; then
  echo "BAHAYA: .env ter-track; push dibatalkan." >&2
  exit 1
fi

TEST_LOG="$(mktemp)"
trap 'rm -f "$TEST_LOG"' EXIT

if ! python3 -m unittest discover -s tests -q >"$TEST_LOG" 2>&1; then
  echo "unit test gagal; perubahan TIDAK di-push" >&2
  tail -n 20 "$TEST_LOG" >&2 || true
  exit 1
fi

git add -A
if ! git commit -m "auto: sync $(date -u +%Y-%m-%dT%H:%M:%SZ)" >/dev/null; then
  exit 0
fi

if ! git push "$REMOTE" "$BRANCH"; then
  echo "push gagal (mungkin remote punya commit baru); perlu merge manual" >&2
  exit 1
fi

echo "sinkron: $(git rev-parse --short HEAD) -> $REMOTE/$BRANCH"
