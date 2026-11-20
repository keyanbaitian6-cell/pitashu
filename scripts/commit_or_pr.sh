#!/usr/bin/env bash
set -euo pipefail

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

# 1. 検証ゲートを通過した products.json 更新があれば main へ自動反映
if ! git diff --quiet -- products.json; then
  git add products.json
  git commit -m "chore(data): auto-sync verified storage products ($(date -u +%Y-%m-%d))"
  git push origin HEAD:main
  echo "Verified updates pushed to main."
else
  echo "No verified updates to products.json."
fi

# 2. HTML数値裏付けが曖昧だった隔離候補は PR として分岐させる
if [ -f "quarantine_candidates.json" ]; then
  BRANCH="auto/quarantine-$(date -u +%Y%m%d-%H%M)"
  git checkout -b "$BRANCH"
  git add quarantine_candidates.json
  git commit -m "chore(data): quarantine storage candidates needing manual check"
  git push -u origin "$BRANCH"

  gh pr create \
    --title "[自動隔離] 公式HTML数値照合が不一致だった収納候補の確認" \
    --body "Gemini API が発見した候補のうち、公式ページHTML本文からミリ数値が機械照合できなかった商品を隔離しました。画像内スペック等を目視確認し、問題なければ products.json へ転記してマージしてください。" \
    --base main \
    --head "$BRANCH"

  git checkout main
fi
