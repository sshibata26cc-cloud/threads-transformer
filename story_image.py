"""
Threadsの投稿内容から、Instagramストーリーズ用のPNG画像を生成するモジュール。

文章はThreadsから取得した原文をそのまま使い、要約・言い換え・省略は行わない。
文字量に応じてフォントサイズを自動的に小さくし、1080x1920pxの画像内に
全文が収まるようにする。

同梱フォント: fonts/ipaexg.ttf（IPAexゴシック）
ライセンス: fonts/IPA_Font_License_Agreement_v1.0.txt を参照。
"""

import io
import os
import re

import requests
from PIL import Image, ImageDraw, ImageFont

CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1920

BACKGROUND_COLOR = (250, 250, 248)
TEXT_COLOR = (30, 30, 30)
NAME_COLOR = (20, 20, 20)

MARGIN_X = 72
MARGIN_TOP = 90
MARGIN_BOTTOM = 90

PROFILE_DIAMETER = 108
HEADER_GAP = 28  # プロフィール画像とアカウント名の間
SECTION_GAP = 56  # ヘッダーと本文の間
BLOCK_GAP = 40  # 本文と返信、返信同士の間

NAME_FONT_SIZE = 42

MAX_BODY_FONT_SIZE = 44
MIN_BODY_FONT_SIZE = 20
FONT_STEP = 2

LINE_HEIGHT_RATIO = 1.55

FONT_PATH = os.path.join(os.path.dirname(__file__), "fonts", "ipaexg.ttf")


class StoryImageError(Exception):
    """ストーリーズ画像の生成中に発生したエラー。"""

    def __init__(self, friendly_message, detail=None):
        super().__init__(friendly_message)
        self.friendly_message = friendly_message
        self.detail = detail


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except OSError as e:
        raise StoryImageError(
            "画像生成用のフォントファイルを読み込めませんでした。",
            detail=str(e),
        )


def _fetch_circular_profile_image(url, diameter: int):
    """プロフィール画像を取得し円形に切り抜く。取得できない場合はNoneを返す。"""
    if not url:
        return None
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        image = Image.open(io.BytesIO(response.content)).convert("RGBA")
    except Exception:
        return None

    side = min(image.size)
    left = (image.width - side) // 2
    top = (image.height - side) // 2
    image = image.crop((left, top, left + side, top + side)).resize(
        (diameter, diameter), Image.LANCZOS
    )

    mask = Image.new("L", (diameter, diameter), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, diameter, diameter), fill=255)

    circular = Image.new("RGBA", (diameter, diameter))
    circular.paste(image, (0, 0), mask=mask)
    return circular


def _split_tokens(paragraph: str):
    """英数字の並びは1トークン、それ以外は1文字ずつのトークンに分割する。"""
    return re.findall(r"[A-Za-z0-9]+|[^A-Za-z0-9]", paragraph)


def _wrap_paragraph(draw, paragraph: str, font, max_width: int):
    if paragraph == "":
        return [""]

    lines = []
    current = ""
    for token in _split_tokens(paragraph):
        candidate = current + token
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
            continue

        if current:
            lines.append(current)
            current = ""

        if draw.textlength(token, font=font) <= max_width:
            current = token
        else:
            # 1トークンだけでも幅を超える場合は1文字ずつ強制的に折り返す
            for ch in token:
                candidate = current + ch
                if current and draw.textlength(candidate, font=font) > max_width:
                    lines.append(current)
                    current = ch
                else:
                    current = candidate

    if current:
        lines.append(current)
    return lines


def _wrap_text_block(draw, text: str, font, max_width: int):
    lines = []
    for paragraph in text.split("\n"):
        lines.extend(_wrap_paragraph(draw, paragraph, font, max_width))
    return lines


def _measure_blocks(draw, blocks, font, max_width, line_height):
    wrapped_blocks = []
    total_height = 0
    for i, block in enumerate(blocks):
        lines = _wrap_text_block(draw, block, font, max_width)
        wrapped_blocks.append(lines)
        total_height += len(lines) * line_height
        if i < len(blocks) - 1:
            total_height += BLOCK_GAP
    return wrapped_blocks, total_height


def generate_story_image(profile_image_url, account_name, original_text, own_replies):
    """
    Threadsの投稿内容から1080x1920のInstagramストーリーズ用PNG画像を生成する。

    original_text・own_repliesの文字列は一切変更せず、そのまま描画する。
    文字量が多い場合はフォントサイズを自動的に縮小し、画像内に収める。

    戻り値: (PNGのバイト列, 警告メッセージ または None)
    """
    canvas = Image.new("RGB", (CANVAS_WIDTH, CANVAS_HEIGHT), BACKGROUND_COLOR)
    draw = ImageDraw.Draw(canvas)

    max_width = CANVAS_WIDTH - MARGIN_X * 2
    name_font = _load_font(NAME_FONT_SIZE)

    header_height = max(PROFILE_DIAMETER, NAME_FONT_SIZE + 10)
    available_height = (
        CANVAS_HEIGHT - MARGIN_TOP - MARGIN_BOTTOM - header_height - SECTION_GAP
    )

    blocks = [original_text or ""] + [r or "" for r in own_replies]

    font_size = MAX_BODY_FONT_SIZE
    body_font = _load_font(font_size)
    wrapped_blocks, total_height = _measure_blocks(
        draw, blocks, body_font, max_width, int(font_size * LINE_HEIGHT_RATIO)
    )

    while total_height > available_height and font_size > MIN_BODY_FONT_SIZE:
        font_size -= FONT_STEP
        body_font = _load_font(font_size)
        wrapped_blocks, total_height = _measure_blocks(
            draw, blocks, body_font, max_width, int(font_size * LINE_HEIGHT_RATIO)
        )

    warning = None
    if total_height > available_height:
        warning = (
            "文章量が多いため、文字サイズがかなり小さくなっています。"
            "画像が見づらい場合は、投稿を分けることをおすすめします。"
        )

    # --- ヘッダー（プロフィール画像 + アカウント名） ---
    profile_circle = _fetch_circular_profile_image(profile_image_url, PROFILE_DIAMETER)
    header_y = MARGIN_TOP
    if profile_circle:
        canvas.paste(profile_circle, (MARGIN_X, header_y), mask=profile_circle)
        name_x = MARGIN_X + PROFILE_DIAMETER + HEADER_GAP
    else:
        name_x = MARGIN_X

    name_y = header_y + (PROFILE_DIAMETER - NAME_FONT_SIZE) // 2
    draw.text((name_x, name_y), account_name or "", font=name_font, fill=NAME_COLOR)

    # --- 本文・返信 ---
    line_height = int(font_size * LINE_HEIGHT_RATIO)
    y = MARGIN_TOP + header_height + SECTION_GAP

    for i, lines in enumerate(wrapped_blocks):
        for line in lines:
            draw.text((MARGIN_X, y), line, font=body_font, fill=TEXT_COLOR)
            y += line_height
        if i < len(wrapped_blocks) - 1:
            y += BLOCK_GAP

    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG")
    return buffer.getvalue(), warning
