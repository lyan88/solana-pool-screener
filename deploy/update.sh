#!/usr/bin/env bash
# Tarik update dari GitHub lalu deploy ke /opt/screener2.
#
#   ./deploy/update.sh          # sebagai user pemilik repo
#   sudo ./deploy/update.sh     # tetap pull sebagai pemilik repo
#
# Skrip ini TIDAK dijalankan otomatis oleh cron. Auto-sync yang aktif adalah
# arah sebaliknya (VPS -> GitHub), supaya produksi tidak berubah tanpa dicek.

set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OWNER="$(stat -c '%U' "$SRC")"

if [[ $EUID -eq 0 ]]; then
  sudo -u "$OWNER" git -C "$SRC" pull --ff-only
else
  git -C "$SRC" pull --ff-only
fi

exec bash "$SRC/deploy/install.sh" --force
