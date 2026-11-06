#!/usr/bin/env bash
# Tree guard: refuse to publish from a checkout whose working tree has drifted from HEAD.
#
# Why this exists: this repo is a published artifact (catalog pin). Twice on 2026-11-06 an
# uncommitted variant of correlation_plugin.py appeared in this working tree from an
# unidentified writer (see PROGRESS/incident note). The danger is not the edit itself, it is
# committing or pushing with `-a` while it sits there: the reviewed tree is HEAD, so any
# publish action must start from a working tree that is byte-identical to HEAD.
#
# Usage:  tools/tree_guard.sh          # exit 0 clean, 1 drifted (prints the files)
#         tools/tree_guard.sh --fix    # restore tracked files from HEAD, then re-check
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2

if [ "${1:-}" = "--fix" ]; then
  git checkout -- . || exit 2
fi

drift=$(git status --porcelain --untracked-files=no || true)
if [ -n "$drift" ]; then
  echo "TREE DRIFTED FROM HEAD — do not commit or push yet:" >&2
  echo "$drift" >&2
  echo "" >&2
  echo "review the diff, then either commit it deliberately or run: tools/tree_guard.sh --fix" >&2
  exit 1
fi

head=$(git rev-parse HEAD)
echo "clean: working tree == HEAD ($head)"
exit 0
