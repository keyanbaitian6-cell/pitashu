#!/usr/bin/env python3
"""
X (旧Twitter) API v2 自動投稿スクリプト（AIを使わない文の型方式）

- 投稿文は products.json の数値だけから組み立てる。AIに書かせないので寸法を取り違えない。
- 通常日は URL を含まない実寸Tips（$0.015 / 件）を投稿し、プロフィール固定リンクへ誘導する。
- 直近24時間に products.json へ商品が追加されていれば、その商品の速報を
  TOOL_PUBLIC_URL 付きで投稿する（$0.200 / 件）。
- 消費者庁ステマ規制に準拠するため、送信直前に `#PR` を強制付与して検証する。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from requests_oauthlib import OAuth1

ROOT_DIR = Path(__file__).resolve().parent.parent
PRODUCTS_PATH = ROOT_DIR / "products.json"

X_TWEETS_ENDPOINT = "https://api.x.com/2/tweets"
JST = dt.timezone(dt.timedelta(hours=9))

# 「ギリギリ入る/入らない」が話題になる組み合わせだけを選ぶための余裕の上限 (mm)
MAX_RELEVANT_CLEARANCE_MM = 20.0
NAME_MAX_CHARS = 30
FOOTER_TIP = "数ミリ差の判定はプロフのリンクから無料で。"
FOOTER_NEW = "収納判定ツールに追加しました。"


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

    candidate = f"{cleaned}\n{url}" if url else cleaned

    if weighted_x_length(candidate) > 280:
        raise ValueError(
            f"Xの文字数上限(重み280)を超過しました ({weighted_x_length(candidate)}): {candidate}"
        )
    return candidate


def fmt_mm(value: float) -> str:
    """66.0 → '66'、64.5 → '64.5' のように表記する。"""
    return f"{value:.1f}".rstrip("0").rstrip(".")


def short_name(product: Dict[str, Any]) -> str:
    # 「（レギュラー 3個入）」のような括弧書きは省いて短くする
    base = re.sub(r"\s*[（(].*$", "", product["name"]).strip() or product["name"]
    name = f"{product['brand']} {base}"
    return name if len(name) <= NAME_MAX_CHARS else name[: NAME_MAX_CHARS - 1] + "…"


def prot_label(prot: Dict[str, Any]) -> str:
    # 「公式キャラスリ1重 (66×92)」の寸法部分は本文で別に出すので省く
    return re.sub(r"\s*[（(][^）)]*[）)]\s*$", "", prot["label"]).strip() or prot["label"]


def clearances(product: Dict[str, Any], prot: Dict[str, Any]) -> Tuple[float, float, float]:
    """立てて入れたときの余り (幅, 高さ, 奥行/厚み)。負なら不足。index.html と同じ判定。"""
    return (
        round(product["inner_w_mm"] - prot["w_mm"], 1),
        round(product["inner_h_mm"] - prot["h_mm"], 1),
        round(product["inner_d_mm"] - prot["d_mm"], 1),
    )


def fits(product: Dict[str, Any], prot: Dict[str, Any]) -> bool:
    return all(c >= 0 for c in clearances(product, prot))


def fit_line(product: Dict[str, Any], prot: Dict[str, Any]) -> str:
    cw, ch, cd = clearances(product, prot)
    size = f"{fmt_mm(prot['w_mm'])}×{fmt_mm(prot['h_mm'])}mm"
    if cw >= 0 and ch >= 0 and cd >= 0:
        if cw == 0 and ch == 0:
            return f"○ {prot_label(prot)} {size}（ぴったり）"
        return f"○ {prot_label(prot)} {size}（余り 幅{fmt_mm(cw)}・高さ{fmt_mm(ch)}mm）"
    shortages = []
    if cw < 0:
        shortages.append(f"幅{fmt_mm(-cw)}mm")
    if ch < 0:
        shortages.append(f"高さ{fmt_mm(-ch)}mm")
    if cd < 0:
        shortages.append(f"厚み{fmt_mm(-cd)}mm")
    return f"× {prot_label(prot)} {size}（{'・'.join(shortages)}不足）"


def border_lines(product: Dict[str, Any], preset: Dict[str, Any]) -> List[str]:
    """入る中で一番大きい保護状態と、入らない中で一番小さい保護状態の2行（境目）を返す。"""
    protections = sorted(preset["protections"], key=lambda p: (p["w_mm"] * p["h_mm"], p["d_mm"]))
    fitting = [p for p in protections if fits(product, p)]
    failing = [p for p in protections if not fits(product, p)]
    lines = []
    if fitting:
        lines.append(fit_line(product, fitting[-1]))
    if failing:
        lines.append(fit_line(product, failing[0]))
    return lines


def relevant_pairs(
    presets: List[Dict[str, Any]], products: List[Dict[str, Any]]
) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """1つ以上の保護状態が入り、その余りが小さい（話題になる）商品と収納の組み合わせ。"""
    pairs = []
    for product in products:
        for preset in presets:
            fitting = [p for p in preset["protections"] if fits(product, p)]
            if not fitting:
                continue
            tightest = max(fitting, key=lambda p: p["w_mm"] * p["h_mm"])
            cw, ch, _ = clearances(product, tightest)
            if cw <= MAX_RELEVANT_CLEARANCE_MM and ch <= MAX_RELEVANT_CLEARANCE_MM:
                pairs.append((preset, product))
    return pairs


def compose(header: str, lines: List[str], footer: str, url: Optional[str]) -> str:
    """文字数に収まるまで行を減らして本文を組み立てる。"""
    for n in range(len(lines), -1, -1):
        body = "\n".join([header, *lines[:n], footer])
        try:
            return enforce_pr_and_length(body, url=url)
        except ValueError:
            continue
    raise ValueError(f"投稿文が文字数上限に収まりません: {header}")


def build_tip_text(
    presets: List[Dict[str, Any]], products: List[Dict[str, Any]], day_index: int
) -> str:
    pairs = relevant_pairs(presets, products)
    if not pairs:
        raise RuntimeError("投稿に使える商品と収納の組み合わせがありません。")
    preset, product = pairs[day_index % len(pairs)]
    header = (
        f"【実寸メモ】{preset['short_name']}は{short_name(product)}に入る？\n"
        f"内寸 {fmt_mm(product['inner_w_mm'])}×{fmt_mm(product['inner_h_mm'])}mm"
    )
    return compose(header, border_lines(product, preset), FOOTER_TIP, url=None)


def build_new_item_text(
    presets: List[Dict[str, Any]], product: Dict[str, Any], url: Optional[str]
) -> str:
    header = (
        f"【新着】{short_name(product)}\n"
        f"内寸 {fmt_mm(product['inner_w_mm'])}×{fmt_mm(product['inner_h_mm'])}"
        f"×{fmt_mm(product['inner_d_mm'])}mm"
    )
    lines: List[str] = []
    matched = [preset for preset, prod in relevant_pairs(presets, [product])]
    if matched:
        preset = matched[0]
        lines = [f"{preset['short_name']}なら", *border_lines(product, preset)]
    return compose(header, lines, FOOTER_NEW, url=url)


def find_new_products(current: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """24時間前の main と比べて products.json に増えた商品を返す（毎日1回の実行で1度だけ拾う）。"""
    try:
        rev = subprocess.run(
            ["git", "rev-list", "-1", "--before=24 hours ago", "HEAD"],
            cwd=ROOT_DIR, capture_output=True, text=True, check=True,
        ).stdout.strip()
        if not rev:
            return []
        old_json = subprocess.run(
            ["git", "show", f"{rev}:products.json"],
            cwd=ROOT_DIR, capture_output=True, text=True, check=True, encoding="utf-8",
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    old_ids = {p["id"] for p in json.loads(old_json).get("storage_products", [])}
    return [p for p in current if p["id"] not in old_ids]


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
    parser.add_argument(
        "--day-index",
        type=int,
        default=None,
        help="Tipsの組み合わせを選ぶ番号（試験用。省略時は今日の通し日数）",
    )
    args = parser.parse_args()

    if not PRODUCTS_PATH.exists():
        print(f"ERROR: {PRODUCTS_PATH} が見つかりません。", file=sys.stderr)
        return 1

    master = json.loads(PRODUCTS_PATH.read_text(encoding="utf-8"))
    presets = master.get("item_presets", [])
    products = master.get("storage_products", [])
    tool_url = os.getenv("TOOL_PUBLIC_URL", "").strip() or None

    new_items = find_new_products(products)
    if new_items and tool_url:
        post_text = build_new_item_text(presets, new_items[0], tool_url)
    else:
        day_index = args.day_index
        if day_index is None:
            day_index = dt.datetime.now(JST).timetuple().tm_yday
        post_text = build_tip_text(presets, products, day_index)

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
