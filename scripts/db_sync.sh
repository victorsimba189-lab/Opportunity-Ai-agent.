#!/usr/bin/env bash
# Persist the SQLite database between GitHub Actions runs on the `scout-data` branch.
#   pull: restore data/scout.db from the branch (fresh DB if the branch doesn't exist yet)
#   push: replace the branch with a single commit holding the current DB (keeps the repo small),
#         plus replies.json (command replies the Cloudflare Worker serves) when it exists
set -euo pipefail

BRANCH="${SCOUT_DATA_BRANCH:-scout-data}"
DB="${SCOUT_DB:-data/scout.db}"
REPLIES="$(dirname "$DB")/replies.json"

case "${1:-}" in
  pull)
    mkdir -p "$(dirname "$DB")"
    if git fetch --quiet --depth=1 origin "$BRANCH" 2>/dev/null; then
      git show "FETCH_HEAD:scout.db" > "$DB"
      echo "Restored $DB from $BRANCH ($(du -h "$DB" | cut -f1))"
    else
      echo "No $BRANCH branch yet: starting with a fresh database"
    fi
    ;;
  push)
    if [ ! -f "$DB" ]; then
      echo "No database to save"; exit 0
    fi
    export GIT_AUTHOR_NAME="opportunity-scout[bot]" GIT_AUTHOR_EMAIL="actions@users.noreply.github.com"
    export GIT_COMMITTER_NAME="$GIT_AUTHOR_NAME" GIT_COMMITTER_EMAIL="$GIT_AUTHOR_EMAIL"
    db_blob=$(git hash-object -w "$DB")
    readme_blob=$(printf '%s\n' "# scout-data" "" \
      "Bot-managed branch holding the SQLite database (scout.db) used by the daily workflow" \
      "and replies.json, the command replies served by the Cloudflare Worker." \
      "It is force-pushed as a single commit on every run. Do not edit by hand." | git hash-object -w --stdin)
    entries=$(printf '100644 blob %s\tREADME.md\n' "$readme_blob")
    if [ -f "$REPLIES" ]; then
      entries+=$(printf '\n100644 blob %s\treplies.json' "$(git hash-object -w "$REPLIES")")
    fi
    entries+=$(printf '\n100644 blob %s\tscout.db' "$db_blob")
    tree=$(printf '%s\n' "$entries" | git mktree)
    commit=$(git commit-tree "$tree" -m "Update scout database $(date -u +%Y-%m-%dT%H:%M:%SZ)")
    for attempt in 1 2 3 4; do
      if git push --quiet --force origin "$commit:refs/heads/$BRANCH"; then
        echo "Saved $DB to $BRANCH"; exit 0
      fi
      sleep $((2 ** attempt))
    done
    echo "Failed to push $BRANCH" >&2; exit 1
    ;;
  *)
    echo "usage: $0 pull|push" >&2; exit 2
    ;;
esac
