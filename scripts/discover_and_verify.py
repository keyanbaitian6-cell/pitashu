#!/usr/bin/env python3
"""
トレカ・推し活収納データベース (products.json) 自動探索＆決定論的検証ゲートスクリプト

1. Gemini API (gemini-3.8-flash + google_search + url_context + Structured Outputs) で
   未登録のトレカ・推し活収納グッズ候補を探索・構造化する。
2. 決定論的CIゲート（HTTP 200確認・HTML本文内のミリ数値実在照合・物理境界・重複排除）で検証する。
3. 合格品は products.json へ直接追加し、数値裏付けが曖昧な候補は quarantine_candidates.json へ隔離する。
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import unicodedata
from pathlib import Path
from typing import List, Literal, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from google import genai
from pydantic import BaseModel, Field

ROOT_DIR = Path(__file__).resolve().parent.parent
PRODUCTS_PATH = ROOT_DIR / "products.json"
QUARANTINE_PATH = ROOT_DIR / "quarantine_candidates.json"
NEW_VERIFIED_PATH = ROOT_DIR / "last_verified_items.json"

USER_AGENT = (
    "Mozilla/5.0 (compatible; StorageFinderBot/1.0; +https://github.com/)"
)


class StorageCandidate(BaseModel):
    id: str = Field(description="英小文字ケバブケースの一意ID (例: toyger-deck-slim)")
    brand: str = Field(description="メーカー・ブランド名")
    name: str = Field(description="商品名（型番・入数を含む）")
    storage_type: Literal["portable", "bulk", "moisture"]
    mode: Literal["pocket", "lane", "box"]
    inner_w_mm: float = Field(description="内寸の幅 (mm)")
    inner_h_mm: float = Field(description="内寸の高さ (mm)")
    inner_d_mm: float = Field(description="内寸の奥行または厚み (mm)")
    slots_per_unit: Optional[int] = Field(
        default=None, description="modeがpocketの場合の1セットあたりポケット数"
    )
    lanes: Optional[int] = Field(
        default=None, description="modeがlaneまたはboxの場合の列数"
    )
    allow_flat_lay: bool = Field(description="平置き（寝かせ収納）可否")
    msrp_note: str = Field(description="メーカー希望小売価格・公式参考収納目安")
    features: List[str] = Field(description="特徴や干渉注意点のリスト（2〜4件）")
    official_url: str = Field(description="公式または販売元の個別商品ページURL")
    amazon_keyword: str = Field(description="Amazon検索用の正確な商品名・型番")
    rakuten_keyword: str = Field(description="楽天市場検索用の正確な商品名・型番")


class DiscoveryResult(BaseModel):
    candidates: List[StorageCandidate]


def normalize_text(text: str) -> str:
    """全角英数・記号を半角に正規化し、連続空白を1つにまとめる。"""
    normalized = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", normalized)


def number_variants(mm: float) -> List[str]:
    """ミリ数値とcm換算値の文字列表記バリエーションを生成する。"""
    variants = set()
    if mm.is_integer():
        variants.add(str(int(mm)))
    else:
        variants.add(f"{mm:.1f}".rstrip("0").rstrip("."))
        variants.add(f"{mm:.2f}".rstrip("0").rstrip("."))

    cm = mm / 10.0
    if cm.is_integer():
        variants.add(str(int(cm)))
    else:
        variants.add(f"{cm:.1f}".rstrip("0").rstrip("."))
    return [v for v in variants if v]


def verify_physical_bounds(item: StorageCandidate) -> Optional[str]:
    """物理的なあり得ない値・スキーマ不整合を弾く。"""
    for label, val in [
        ("inner_w_mm", item.inner_w_mm),
        ("inner_h_mm", item.inner_h_mm),
        ("inner_d_mm", item.inner_d_mm),
    ]:
        if val <= 0 or val > 1200:
            return f"{label} が物理範囲外です ({val}mm)"

    if item.mode == "pocket":
        if not item.slots_per_unit or item.slots_per_unit <= 0:
            return "mode=pocket ですが slots_per_unit が正の整数ではありません"
        if item.inner_d_mm > 50:
            return f"pocketモードで厚みが50mm超 ({item.inner_d_mm}mm) です"
    else:
        if not item.lanes or item.lanes <= 0:
            return f"mode={item.mode} ですが lanes が正の整数ではありません"

    return None


def verify_url_and_dimensions(item: StorageCandidate) -> Optional[str]:
    """Gate 1 & Gate 2: URL到達性とHTML本文内のミリ数値実在を照合する。"""
    parsed = urlparse(item.official_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return "official_url の形式が不正です"
    if any(k in parsed.path.lower() for k in ("/search", "/s?", "category", "/tag/")):
        return "個別商品ページではなく検索・カテゴリURLの可能性があります"

    try:
        resp = requests.get(
            item.official_url,
            headers={"User-Agent": USER_AGENT},
            timeout=15,
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        return f"URL取得失敗: {exc}"

    if resp.status_code != 200:
        return f"HTTPステータス異常: {resp.status_code}"

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    body_text = normalize_text(soup.get_text(" "))

    missing = []
    for axis, mm in [
        ("幅", item.inner_w_mm),
        ("高さ", item.inner_h_mm),
        ("奥行/厚", item.inner_d_mm),
    ]:
        patterns = number_variants(mm)
        matched = any(
            re.search(rf"(?<![0-9.]){re.escape(p)}(?![0-9.])", body_text)
            for p in patterns
        )
        if not matched:
            missing.append(f"{axis}={mm}mm")

    if missing:
        return (
            "HTML本文内に寸法数値の一致が見つかりません "
            f"(画像内表記または外寸混同の可能性): {', '.join(missing)}"
        )

    return None


def check_rakuten_availability(keyword: str) -> Optional[bool]:
    """楽天ウェブサービスAPIが設定されている場合のみ市場流通を確認する。"""
    app_id = os.getenv("RAKUTEN_APP_ID", "").strip()
    if not app_id:
        return None
    params = {
        "applicationId": app_id,
        "keyword": keyword,
        "hits": 1,
        "format": "json",
    }
    aff_id = os.getenv("RAKUTEN_AFFILIATE_ID", "").strip()
    if aff_id:
        params["affiliateId"] = aff_id
    try:
        r = requests.get(
            "https://app.rakuten.co.jp/services/api/IchibaItem/Search/20220601",
            params=params,
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            return int(data.get("count", 0)) > 0
    except requests.RequestException:
        return None
    return None


def fetch_candidates_via_gemini(existing_ids: List[str], existing_names: List[str]) -> List[StorageCandidate]:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("環境変数 GEMINI_API_KEY が設定されていません。")

    client = genai.Client(api_key=api_key)
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).strftime("%Y-%m-%d")

    prompt = f"""
本日（{today}）時点で日本国内で購入可能な、トレカ（ポケカ・遊戯王・PSA鑑定スラブ・未開封BOX）
または推し活グッズ（チェキ・L判ブロマイド・缶バッジ・アクスタ）向けの収納ケース・バインダー・ドライボックスから、
以下の既存登録商品と重複しない注目商品または新商品を最大3件だけ調査・抽出してください。

既存ID一覧（除外対象）:
{", ".join(existing_ids)}

既存商品名一覧（除外対象）:
{", ".join(existing_names)}

厳守ルール:
1. 必ず Google Search と URL Context を使い、メーカー公式または大手量販店の個別商品ページを直接確認すること。
2. HTML本文に「内寸（幅・高さ・奥行/厚み mm）」が明記されている商品のみを選ぶこと。外寸しか書かれていない商品や推測値は絶対に含めないこと。
3. 変動するEC実売価格は書かず、msrp_note にはメーカー希望小売価格や公式参考収納目安のみを書くこと。
4. id は英小文字・数字・ハイフンのみのケバブケースにすること。
"""

    interaction = client.interactions.create(
        model=os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),
        input=prompt,
        tools=[{"type": "google_search"}, {"type": "url_context"}],
        response_format={
            "type": "text",
            "mime_type": "application/json",
            "schema": DiscoveryResult.model_json_schema(),
        },
    )
    parsed = DiscoveryResult.model_validate_json(interaction.output_text)
    return parsed.candidates


def main() -> int:
    if not PRODUCTS_PATH.exists():
        print(f"ERROR: {PRODUCTS_PATH} が見つかりません。", file=sys.stderr)
        return 1

    master = json.loads(PRODUCTS_PATH.read_text(encoding="utf-8"))
    existing_items = master.get("storage_products", [])
    existing_ids = {item["id"] for item in existing_items}
    existing_names = {normalize_text(item["name"]) for item in existing_items}

    candidates = fetch_candidates_via_gemini(
        sorted(existing_ids), [item["name"] for item in existing_items]
    )

    verified: List[dict] = []
    quarantined: List[dict] = []

    for cand in candidates:
        if cand.id in existing_ids or normalize_text(cand.name) in existing_names:
            print(f"[SKIP] 重複スキップ: {cand.id}")
            continue

        reason = verify_physical_bounds(cand)
        if not reason:
            reason = verify_url_and_dimensions(cand)

        item_dict = cand.model_dump(exclude_none=True)
        rakuten_found = check_rakuten_availability(cand.rakuten_keyword)
        if rakuten_found is False and not reason:
            reason = "楽天市場APIで該当キーワードの流通在庫が確認できませんでした"

        if reason:
            print(f"[QUARANTINE] {cand.id}: {reason}")
            quarantined.append({"reason": reason, "candidate": item_dict})
        else:
            print(f"[VERIFIED] {cand.id}: 全検証ゲート通過")
            verified.append(item_dict)
            existing_ids.add(cand.id)
            existing_names.add(normalize_text(cand.name))

    if verified:
        master["storage_products"].extend(verified)
        master["updated_at"] = dt.datetime.now(
            dt.timezone(dt.timedelta(hours=9))
        ).strftime("%Y-%m-%d")
        PRODUCTS_PATH.write_text(
            json.dumps(master, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        NEW_VERIFIED_PATH.write_text(
            json.dumps(verified, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    elif NEW_VERIFIED_PATH.exists():
        NEW_VERIFIED_PATH.unlink()

    if quarantined:
        QUARANTINE_PATH.write_text(
            json.dumps(quarantined, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    elif QUARANTINE_PATH.exists():
        QUARANTINE_PATH.unlink()

    print(f"完了: 合格 {len(verified)}件 / 隔離 {len(quarantined)}件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
