#!/usr/bin/env bash
# The repo's `data` branch holds the raw parquet zone and the per-source
# watermarks: the pipeline's only durable state. This script checks it
# out beside main and saves it back.
#
#   datastore.sh open <dir>
#       Check the data branch out into <dir> as a git worktree. When the
#       remote has no data branch yet, start an empty orphan one instead
#       (the first run then seeds it; see scripts/migrate_raw_zone.py).
#       Prints exists=true or exists=false, ready for $GITHUB_OUTPUT.
#
#   datastore.sh save <dir> <message>
#       Commit everything under <dir> and push it to the data branch. A
#       no-op when nothing changed, so a quiet day adds no commit.
#
# Only the tip is fetched (--depth=1): the pipeline needs the current
# files, never the branch's history, and a shallow parent still accepts
# a new commit on top of it.
#
# DATASTORE_REMOTE and DATASTORE_BRANCH override origin and data; the
# tests use them to run against a throwaway bare repository.
set -euo pipefail

remote="${DATASTORE_REMOTE:-origin}"
branch="${DATASTORE_BRANCH:-data}"

usage() { echo "usage: datastore.sh open <dir> | save <dir> <message>" >&2; exit 2; }
[ $# -ge 2 ] || usage
cmd="$1"
dir="$2"

case "$cmd" in
  open)
    if git ls-remote --exit-code --heads "$remote" "$branch" >/dev/null 2>&1; then
      git fetch --quiet --depth=1 "$remote" "+refs/heads/$branch:refs/remotes/$remote/$branch"
      git worktree add --quiet -B "$branch" "$dir" "$remote/$branch"
      echo "exists=true"
    else
      git worktree add --quiet --orphan -b "$branch" "$dir"
      echo "exists=false"
    fi
    ;;
  save)
    [ $# -ge 3 ] || usage
    git -C "$dir" add --all
    if git -C "$dir" diff --cached --quiet; then
      echo "data branch unchanged; nothing to commit" >&2
      exit 0
    fi
    git -C "$dir" commit --quiet -m "$3"
    git -C "$dir" push --quiet "$remote" "HEAD:refs/heads/$branch"
    echo "pushed $(git -C "$dir" rev-parse --short HEAD) to $branch" >&2
    ;;
  *)
    usage
    ;;
esac
