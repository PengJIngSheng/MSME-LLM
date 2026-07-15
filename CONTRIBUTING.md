# Git workflow

`main` is the release branch. Keep it linear and only publish reviewed, tested work.

1. Before starting: `git pull --rebase`.
2. Work on a branch: `git switch -c feat/short-description`.
3. Before committing: `bash scripts/git-health-check.sh`.
4. Use meaningful commits, for example `fix(auth): reject cross-user chat access`.
5. Publish with `git push -u origin HEAD`, then merge by rebase or squash. Do not merge
   `main` into a feature branch merely to synchronize it.

This repository uses `.githooks/` to reject known secret paths, backups, and vague commit
messages. Enable them once after cloning with:

```bash
git config core.hooksPath .githooks
git config pull.rebase true
git config rebase.autoStash true
git config fetch.prune true
```
