#!/usr/bin/env bash
set -euo pipefail

git fetch --prune origin
git diff --check
git fsck --full --no-reflogs

if git log --all --oneline -- data/backups/user_delete_backup_20260630_080911.json | grep -q .; then
    echo "Blocked: the deleted sensitive backup is still reachable from Git history." >&2
    exit 1
fi

read -r ahead behind < <(git rev-list --left-right --count main...origin/main)
if [[ "$behind" != "0" ]]; then
    echo "Blocked: local main is behind origin/main by $behind commit(s)." >&2
    exit 1
fi

git status -sb
echo "Git health check passed."
