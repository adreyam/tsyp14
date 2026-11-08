#!/usr/bin/env bash
# Put this triplet folder on GitHub (first push and every later update).
#   bash publish_to_github.sh                                   -> https://github.com/adreyam/tsyp14.git
#   bash publish_to_github.sh https://github.com/<you>/<repo>.git
# Needs git and a GitHub login (a personal access token as the password, or `gh auth login`).
set -euo pipefail
cd "$(dirname "$0")"
REMOTE="${1:-https://github.com/adreyam/tsyp14.git}"

command -v git >/dev/null || { echo "git is not installed: sudo apt install git"; exit 1; }
[ -d .git ] || git init -b main
git config user.name  >/dev/null || { echo "Set your name first:  git config --global user.name \"Your Name\""; exit 1; }
git config user.email >/dev/null || { echo "Set your email first: git config --global user.email you@example.com"; exit 1; }

# The specification wants the owners file at .github/CODEOWNERS.
if [ -f CODEOWNERS ]; then mkdir -p .github && mv CODEOWNERS .github/CODEOWNERS; fi

git add -A

# GitHub refuses any file over 100 MB: stop before committing one.
too_big=$(git diff --cached --name-only -z | xargs -0 -r -I{} sh -c '[ -f "{}" ] && [ $(stat -c %s "{}") -gt 95000000 ] && echo "{}"' || true)
if [ -n "$too_big" ]; then
  echo "These files are over 95 MB; add them to .gitignore first:"; echo "$too_big"; exit 1
fi

if git diff --cached --quiet; then
  echo "Nothing new to commit."
else
  git commit -q -m "TSYP14 triplet: Twin, City Brain, Guardian, Test Book, scenario logs"
  echo "Committed $(git ls-files | wc -l) files."
fi

if git remote get-url origin >/dev/null 2>&1; then git remote set-url origin "$REMOTE"; else git remote add origin "$REMOTE"; fi
git config http.postBuffer 524288000

# If the GitHub repository was created with a README or a licence, keep our files and add its history.
if git ls-remote --exit-code --heads origin main >/dev/null 2>&1; then
  git fetch -q origin main
  git merge -q --allow-unrelated-histories -X ours --no-edit origin/main || true
fi

git push -u origin main
echo "Done: ${REMOTE%.git}"
