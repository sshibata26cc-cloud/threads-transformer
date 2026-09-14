"""
Story・Carousel両方で共通して使う、フォント選択機能をまとめたモジュール。

「MS P明朝 標準」「游明朝体」といったユーザー向けの表示名と、実際に読み込む
フォントファイルの対応関係を、このモジュール1か所だけで管理する。

OSにインストールされたフォント名を指定する方式は、Streamlit Community
Cloudのようなフォント環境が最小限のLinuxコンテナでは文字化け（豆腐文字）の
原因になるため使わず、必ずこのリポジトリのfonts/ディレクトリに同梱した
フォントファイルを、Pillow (ImageFont.truetype) から直接読み込む。

同梱フォントの一覧・配布元・ライセンスは fonts/FONTS_NOTICE.txt を参照。
"""

import os
import re

import streamlit as st
from PIL import ImageFont

FONTS_DIR = os.path.join(os.path.dirname(__file__), "fonts")

# UI表示名 -> 実際に読み込むフォントファイル名（1か所で管理）。
# 「MS P明朝 標準」「游明朝体」「Arial」は、ライセンス上そのまま同梱できない
# 商用フォント（Windows/Office同梱フォント、Microsoft/Monotype系フォント）の
# 代替として、見た目が近く・日本語対応・再配布可能・商用利用可能な
# フォントへマッピングしている。詳細は fonts/FONTS_NOTICE.txt。
#
# 辞書の並び順がそのままUIの選択肢の並び順になる
# （Python 3.7+ の dict は挿入順を保持するため）。
FONT_FILES = {
    "MS P明朝 標準": "KaiseiTokumin-Regular.ttf",  # Kaisei Tokumin（明朝系の代替）
    "游明朝体": "BIZUDPMincho-Regular.ttf",  # BIZ UDPMincho（明朝/Serif系の代替）
    "ゴシック": "ipaexg.ttf",  # IPAexゴシック（既存フォント。表示名のみ変更）
    "Arial": "Arimo-Regular.ttf",  # Arimo（Arial互換のSans。日本語グリフなし）
    "手書き風書体": "Yomogi-Regular.ttf",  # Yomogi（手書き風の代替）
}

# 選択肢の表示順（Streamlitのselectboxにそのまま渡す）。
FONT_OPTIONS = list(FONT_FILES.keys())

DEFAULT_FONT_KEY = "MS P明朝 標準"

# 「Arial」（Arimo）は日本語グリフを持たないため、本文に日本語が含まれる
# 場合はこちらへ自動的に差し替える（豆腐文字「□」を防ぐため）。
# Arialはもともとサンセリフ体なので、代替も同じサンセリフ系（ゴシック）を使う。
_JAPANESE_FALLBACK_KEY = "ゴシック"

# 日本語（ひらがな・カタカナ・漢字・全角記号・長音符など）を含むかどうかの
# ざっくりとした判定に使う文字範囲。厳密な文字種判定ではなく、
# 「Arimoに日本語グリフがない」ことへの実用的な対策として十分な精度で判定する。
_JAPANESE_CHAR_RE = re.compile(
    r"[　-〿"  # 全角記号・句読点
    r"぀-ゟ"  # ひらがな
    r"゠-ヿ"  # カタカナ
    r"㐀-䶿"  # CJK拡張A
    r"一-鿿"  # CJK統合漢字
    r"＀-￯"  # 全角英数・半角カナ等
    r"]"
)


def _contains_japanese(text: str) -> bool:
    return bool(text) and bool(_JAPANESE_CHAR_RE.search(text))


def _resolve_font_key(font_key, sample_text):
    """
    実際に読み込むべきフォントの表示名キーを決定する。

    未知のキーは既定フォントへ、"Arial"は本文が日本語を含む場合だけ
    日本語対応フォントへ自動的に差し替える。
    """
    if font_key not in FONT_FILES:
        return DEFAULT_FONT_KEY
    if font_key == "Arial" and _contains_japanese(sample_text):
        return _JAPANESE_FALLBACK_KEY
    return font_key


@st.cache_resource(show_spinner=False)
def _load_font_file(path: str, size: int) -> ImageFont.FreeTypeFont:
    """
    フォントファイルをディスクから読み込む部分だけをキャッシュする。

    キャッシュキーは(ファイルパス, サイズ)のみで、ユーザーが編集した
    テキスト内容などは含まないため、編集内容が古いキャッシュで
    表示され続ける心配はない（テキストが変わっても、フォントの実体は
    同じファイル・同じサイズであれば使い回して問題ないため）。
    """
    return ImageFont.truetype(path, size)


def get_font(font_key: str, size: int, sample_text: str = "") -> ImageFont.FreeTypeFont:
    """
    表示名（FONT_OPTIONSの値）とサイズから、実際に描画で使うフォントを返す。

    引数:
        font_key: FONT_OPTIONSのいずれか（不明な値は既定フォントを使う）。
        size: フォントサイズ（px）。
        sample_text: 実際に描画するテキスト。"Arial"選択時に日本語が
                     含まれるかどうかの判定にのみ使う（豆腐文字対策）。

    フォントファイルの読み込みに失敗した場合はOSErrorを送出する
    （呼び出し側でStoryImageError等、利用者向けのエラーに変換すること）。
    """
    resolved_key = _resolve_font_key(font_key, sample_text)
    filename = FONT_FILES.get(resolved_key, FONT_FILES[DEFAULT_FONT_KEY])
    path = os.path.join(FONTS_DIR, filename)
    return _load_font_file(path, size)
