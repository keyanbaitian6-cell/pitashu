# ピタ収（Pita-Shu）トレカ・推し活グッズ 実寸ピッタリ収納判定ツール

スマホ向けのミリ単位収納判定Webアプリ（`index.html` + `products.json`）と、その自動運用の仕組みです。

公開URL: https://keyanbaitian6-cell.github.io/pitashu/

## 自動で動くもの

| いつ | 何が | 何をする |
|---|---|---|
| 毎日 20:15 JST | GitHub Actions | `products.json` の数値から文の型で実寸Tipsを作り、`#PR` を付けてXへ1件投稿する（AIは使わない） |
| 毎週月曜 07:50 JST | Claude Code の定期実行 | Web で新しい収納グッズを探し、公式ページで内寸を確かめ、`scripts/verify_candidates.py` に通った商品を追加する PR を作る |
| main に `index.html` / `products.json` が入ったとき | GitHub Actions | GitHub Pages に公開する |

新商品の PR をマージすると、サイトに反映されます。翌日の投稿は、その商品の速報（公開URL付き）になります。

## ファイル

- `index.html`: 判定Webアプリ本体。開いたときに `products.json` を読み込む（読めないときは埋め込みデータで表示）
- `products.json`: 収納したい物の外寸（保護状態別）と、収納商品の内寸
- `scripts/post_x_tip.py`: X API v2 自動投稿（文の型・`#PR` 強制付与・文字数検証・新商品の速報）
- `scripts/verify_candidates.py`: 新商品候補の検証ゲート（形式・物理範囲・重複・HTTP 200・公式ページ本文にミリ数値があるか）
- `.github/workflows/auto-sync.yml`: 毎日の投稿と Pages 公開

## 設定（GitHub の Settings → Secrets and variables → Actions）

- **Secrets**: `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_SECRET`
- **Variables**: `TOOL_PUBLIC_URL`（公開URL）

## 手元での確認

```
pip install -r requirements.txt
python scripts/post_x_tip.py --dry-run
python scripts/verify_candidates.py candidates.json
```

動作確認は `Actions` タブ → `Post X Tips and Deploy Pages` → `Run workflow` で `dry_run` を `true` にして実行します。
