#!/usr/bin/env python3
"""
X (旧Twitter) API v2 自動投稿スクリプト（2026年 Pay-Per-Use 課金最適化＋ステマ規制対策）

- 通常日は URL を含まない実寸比較Tipsを投稿（$0.015 / 件）し、プロフィール固定リンクへ誘導する。
- 新商品が検証ゲートを通過して追加された日（last_verified_items.json が存在する場合）のみ、
  TOOL_PUBLIC_URL が設定されていれば URL 付きの速報投稿（$0.200 / 件）を行う。
- 消費者庁ステマ規制に準拠するため、送信直前に Python コード側で `#PR` を強制付与して検証する。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from google import genai
from requests_oauthlib import OAuth1

ROOT_DIR = Path(__file__).resolve().parent.parent
PRODUCTS_PATH = ROOT_DIR / "products.json"
NEW_VERIFIED_PATH = ROOT_DIR / "last_verified_items.json"

X_TWEETS_ENDPOINT = "https://api.x.com/2/tweets"


def weighted_x_length(text: str) -> int:
    """Xの標準文字数カウント概算（ASCII=1, 全角・日本語=2, 上限280）。"""
    total = 0
    for ch in text:
        total += 1 if ord(ch) < 128 else 2
    return total


def enforce_pr_and_length(body: str, url: Optional[str] = None) -> str:
    """ステマ規制対応の #PR 付与と文字数上限チェックをコード側で強制する。"""
    cleaned = body.strip()
    if "#PR" not in cleaned and "PR" not in cleaned.split():
        cleaned = f"{cleaned}\n#PR"

    if url:
        # XではURLはt.co短縮により一律23文字換算だが、安全のためそのまま追記して確認
        candidate = f"{cleaned}\n{url}"
    else:
        candidate = cleaned

    if weighted_x_length(candidate) > 280:
        raise ValueError(
            f"Xの文字数上限(重み280)を超過しました ({weighted_x_length(candidate)}): {candidate}"
        )
    return candidate


def generate_tip_with_gemini(
    item_presets: List[Dict[str, Any]],
    storage_products: List[Dict[str, Any]],
    new_items: List[Dict[str, Any]],
    tool_url: Optional[str],
) -> str:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("環境変数 GEMINI_API_KEY が設定されていません。")

    client = genai.Client(api_key=api_key)
    day_index = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).timetuple().tm_yday

    if new_items:
        target = new_items[0]
        mode_instruction = f"""
新商品追加速報モード:
今回新しく収納判定データベースに追加された以下の商品について、数ミリ単位の収納適性を伝える速報ポストを1本作成してください。
商品データ: {json.dumps(target, ensure_ascii=False)}
条件:
- URLは後段のプログラムが自動付与するため、本文中にURLを含めないこと。
- 110文字以内の日本語で、内寸（mm）の具体的なメリットまたは干渉注意点を1つ入れること。
- ハッシュタグは末尾に #PR のみ（後段でも自動検証するため本文に含めてよい）。
"""
    else:
        preset = item_presets[day_index % len(item_presets)]
        sample_storage = storage_products[day_index % len(storage_products)]
        mode_instruction = f"""
通常Tipsモード（URLなし・$0.015節約モード）:
以下の保護状態別アイテム外寸データと収納ケース内寸データをもとに、「数ミリの干渉で入らない失敗」を防ぐ実践的な豆知識ポストを1本作成してください。
アイテム例: {json.dumps(preset, ensure_ascii=False)}
収納ケース例: {json.dumps(sample_storage, ensure_ascii=False)}
条件:
- URLは絶対に含めないこと（プロフィール固定リンクの無料判定ツールへ誘導する表現にする）。
- 115文字以内の日本語で、具体的なミリ数値（例: 幅66mm、高さ110mm等）を必ず1つ以上引用すること。
- 誇大表現や価格断定は禁止。末尾に #PR を付けること。
"""

    response = client.models.generate_content(
        model=os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),
        contents=mode_instruction,
    )
    raw_text = (response.text or "").strip().strip('"').strip("'")
    use_url = tool_url if (new_items and tool_url) else None
    return enforce_pr_and_length(raw_text, url=use_url)


def post_to_x(text: str) -> dict:
    api_key = os.getenv("X_API_KEY", "").strip()
    api_secret = os.getenv("X_API_SECRET", "").strip()
    access_token = os.getenv("X_ACCESS_TOKEN", "").strip()
    access_secret = os.getenv("X_ACCESS_SECRET", "").strip()

    if not all([api_key, api_secret, access_token, access_secret]):
        raise RuntimeError(
            "X API認証情報 (X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET) が不足しています。"
        )

    auth = OAuth1(api_key, api_secret, access_token, access_secret)
    resp = requests.post(
        X_TWEETS_ENDPOINT,
        auth=auth,
        json={"text": text},
        timeout=15,
    )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"X API投稿エラー ({resp.status_code}): {resp.text}")
    return resp.json()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Xへ実際に投稿せず、生成テキストと検証結果のみを表示する",
    )
    args = parser.parse_args()

    if not PRODUCTS_PATH.exists():
        print(f"ERROR: {PRODUCTS_PATH} が見つかりません。", file=sys.stderr)
        return 1

    master = json.loads(PRODUCTS_PATH.read_text(encoding="utf-8"))
    new_items = []
    if NEW_VERIFIED_PATH.exists():
        new_items = json.loads(NEW_VERIFIED_PATH.read_text(encoding="utf-8"))

    tool_url = os.getenv("TOOL_PUBLIC_URL", "").strip() or None
    post_text = generate_tip_with_gemini(
        master.get("item_presets", []),
        master.get("storage_products", []),
        new_items,
        tool_url,
    )

    print("=== 生成されたX投稿テキスト ===")
    print(post_text)
    print(f"=== 重み付き文字数: {weighted_x_length(post_text)} / 280 ===")

    if args.dry_run:
        print("[DRY-RUN] 実際の投稿はスキップしました。")
        return 0

    result = post_to_x(post_text)
    print(f"[POSTED] Tweet ID: {result.get('data', {}).get('id')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
