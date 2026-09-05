#!/bin/sh
# 刷新 GitHub 公开仓库: 把当前工作区做成单提交快照, 强推到 origin/main。
# 公开仓库不含历史与陈旧二进制(exe 走 GitHub Releases); 本地 master 历史保留。
# 用法: bash scripts/publish_github.sh "提交说明"
set -e
cd "$(dirname "$0")/.."
MSG="${1:-Update Hello Pinghe! Launcher}"

git checkout --orphan public-tmp
git add -A
git commit -m "$MSG"
git branch -M public-tmp public
git push origin public:main --force
git checkout master
echo "✅ 已推送快照到 origin/main (GitHub 仓库已覆盖更新)"
