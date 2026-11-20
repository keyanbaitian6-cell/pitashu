# トレカ・推し活グッズ 実寸ピッタリ収納判定ツール（全自動運用リポジトリ）

スマホ向けのミリ単位収納判定Webアプリ（`index.html` + `products.json`）と、GitHub Actions による新商品探索・検証・GitHub Pages公開・X自動投稿パイプラインのセットです。

## ディレクトリ構成

- `index.html`: 実寸ピッタリ収納判定Webアプリ本体
- `products.json`: 収納アイテム＆保護状態別外寸マスタ
- `scripts/discover_and_verify.py`: Gemini API探索＋HTML数値照合ゲート
- `scripts/post_x_tip.py`: X API v2 自動投稿（`#PR` 強制付与・文字数検証）
- `scripts/commit_or_pr.sh`: 合格データの自動コミット＆未検証候補の隔離PR作成
- `.github/workflows/auto-sync.yml`: 定期実行（毎週月曜 07:50 JST / 毎日 20:15 JST）＆Pages公開

## セットアップ手順

1. **パブリックリポジトリを作成して push**
   - GitHub で新しい **Public** リポジトリを作成し、このフォルダの中身を `main` ブランチへ push します。
2. **GitHub Pages を有効化**
   - `Settings` → `Pages` → `Build and deployment` の Source を **GitHub Actions** に設定します。
3. **GitHub Secrets / Variables を登録**
   - `Settings` → `Secrets and variables` → `Actions` で以下を登録します：
     - **Secrets**: `GEMINI_API_KEY`（Google AI Studio Paid Tier）, `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_SECRET`（任意で `RAKUTEN_APP_ID`, `RAKUTEN_AFFILIATE_ID`）
     - **Variables**: `TOOL_PUBLIC_URL`（GitHub Pages で公開されたURL）
4. **ドライランで動作確認**
   - `Actions` タブ → `Auto Sync Storage DB and Post X Tips` → `Run workflow` で `dry_run` を `true` にして実行し、エラーがないことを確認してください。
