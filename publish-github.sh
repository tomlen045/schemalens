#!/bin/bash
# schemalens GitHub 发布脚本（token 存钥匙串 GH_TOKEN）
set -e
REPO="schemalens"
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
TOKEN=$(security find-generic-password -s GH_TOKEN -a tomlen045 -w 2>/dev/null) || { echo "NO GH_TOKEN IN KEYCHAIN"; exit 1; }

# 建仓（存在则跳过）
code=$(curl -s -o /tmp/sl_gh_create.json -w '%{http_code}' -X POST https://api.github.com/user/repos \
  -H "Authorization: token $TOKEN" -H "Accept: application/vnd.github+json" \
  -d "{\"name\":\"$REPO\",\"description\":\"schemalens - data-source schema drift lens (zero-dep, single-file)\",\"private\":false}")
echo "create: $code"

git remote remove gh-origin 2>/dev/null || true
git remote add gh-origin "https://tomlen045:$TOKEN@github.com/tomlen045/$REPO.git"
BRANCH=$(git symbolic-ref --short HEAD)
GIT_TERMINAL_PROMPT=0 git push -q -u gh-origin "$BRANCH:$BRANCH"
echo "pushed ($BRANCH)"

echo "--- anonymous readback ---"
echo "api: $(curl -s -o /dev/null -w '%{http_code}' -m 15 https://api.github.com/repos/tomlen045/$REPO)"
echo "raw: $(curl -s -o /dev/null -w '%{http_code}' -L -m 15 https://raw.githubusercontent.com/tomlen045/$REPO/$BRANCH/README.md)"
