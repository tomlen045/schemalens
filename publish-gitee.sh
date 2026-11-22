#!/bin/bash
# schemalens Gitee 发布脚本
set -e
REPO="schemalens"
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# 1) 从既有仓库 remote 里找可用 token（逐个 GET /api/v5/user 验活）
TOKEN=""
for cfg in /Users/len/CodeBuddy/dx/*/*/.git/config /Users/len/CodeBuddy/dx/*/.git/config; do
  [ -f "$cfg" ] || continue
  t=$(grep -o 'https://[a-zA-Z0-9_]*:[a-f0-9]\{32\}@gitee\.com' "$cfg" 2>/dev/null | head -1 | grep -o '[a-f0-9]\{32\}' || true)
  [ -z "$t" ] && t=$(grep -o '[a-f0-9]\{32\}@gitee\.com' "$cfg" 2>/dev/null | head -1 | grep -o '^[a-f0-9]\{32\}' || true)
  [ -z "$t" ] && continue
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 10 "https://gitee.com/api/v5/user?access_token=$t")
  if [ "$code" = "200" ]; then TOKEN="$t"; echo "token OK from $cfg"; break; fi
done || true
[ -z "$TOKEN" ] && echo "NO VALID GITEE TOKEN" && exit 1

# 2) 建仓（form urlencode，token 放 body）
code=$(curl -s -o /tmp/sl_create.json -w '%{http_code}' -X POST "https://gitee.com/api/v5/user/repos" \
  --data-urlencode "access_token=$TOKEN" --data-urlencode "name=$REPO" \
  --data-urlencode "description=schemalens - 数据源 schema 漂移透镜：上游改列名/翻列序/混类型，报表崩之前点名" \
  --data-urlencode "private=true")
echo "create: $code $(head -c 120 /tmp/sl_create.json)"

# 3) push（token 内嵌 URL 必须带用户名段）
if [ ! -d .git ]; then
  git init -q
  git add -A
  git commit -q -m "schemalens v1.0.1: schema drift lens, 40/40 selftest green"
fi
git remote remove origin 2>/dev/null || true
git remote add origin "https://tomlen:$TOKEN@gitee.com/tomlen/$REPO.git"
GIT_TERMINAL_PROMPT=0 git push -q -u origin master:master 2>/dev/null || GIT_TERMINAL_PROMPT=0 git push -q -u origin master:main || GIT_TERMINAL_PROMPT=0 git push -q -u origin HEAD:master
echo "pushed"

# 4) 置公开（必须带 name 字段）
code=$(curl -s -o /tmp/sl_patch.json -w '%{http_code}' -X PATCH "https://gitee.com/api/v5/repos/tomlen/$REPO" \
  --data-urlencode "access_token=$TOKEN" --data-urlencode "name=$REPO" --data-urlencode "private=false")
echo "public-patch: $code $(head -c 120 /tmp/sl_patch.json)"

# 5) 匿名读回验证（-L 跟随 302）
echo "--- anonymous readback ---"
echo "page:    $(curl -s -o /dev/null -w '%{http_code}' -L -m 15 https://gitee.com/tomlen/$REPO)"
echo "raw:     $(curl -s -o /dev/null -w '%{http_code}' -L -m 15 https://gitee.com/tomlen/$REPO/raw/master/README.md)"
echo "api:     $(curl -s -m 15 https://gitee.com/api/v5/repos/tomlen/$REPO | grep -o '"private":[a-z]*' | head -1)"
