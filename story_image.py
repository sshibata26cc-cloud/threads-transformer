"""
Threadsの投稿内容から、Instagramストーリーズ用のPNG画像を生成するモジュール。

文章はThreadsから取得した原文をそのまま使い、要約・言い換え・省略は行わない。
文字量に応じてフォントサイズを自動的に小さくし、1080x1920pxの画像内に
全文が収まるようにする。

背景画像・文字色・文字サイズ（の上限）・背景の暗さは、
呼び出し側（Streamlit画面）からユーザーが指定できる。

同梱フォント: fonts/ipaexg.ttf（IPAexゴシック）
ライセンス: fonts/IPA_Font_License_Agreement_v1.0.txt を参照。
"""

import io
import os
import re

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1920

BACKGROUND_COLOR = (250, 250, 248)  # 背景画像が指定されていない場合の白背景

MARGIN_X = 72
MARGIN_TOP = 90
MARGIN_BOTTOM = 90

PROFILE_DIAMETER = 108
HEADER_GAP = 28  # プロフィール画像とアカウント名の間
SECTION_GAP = 56  # ヘッダーと本文の間
BLOCK_GAP = 40  # 本文と返信、返信同士の間

NAME_FONT_SIZE = 42

DEFAULT_TEXT_COLOR = "#1E1E1E"
DEFAULT_MAX_FONT_SIZE = 44
MIN_BODY_FONT_SIZE = 20
FONT_STEP = 2
MAX_OVERLAY_OPACITY = 0.8

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


def _hex_to_rgb(hex_color):
    """"#RRGGBB" 形式の文字列をRGBのタプルに変換する。不正な値の場合は標準の文字色を使う。"""
    text = (hex_color or "").lstrip("#")
    if len(text) == 6:
        try:
            return tuple(int(text[i : i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            pass
    return _hex_to_rgb(DEFAULT_TEXT_COLOR)


def load_background_image(file_bytes):
    """
    アップロードされた背景画像を読み込み、ストーリーズ画像で
    安全に使える形（EXIF回転を反映したRGB画像）に変換する。

    画像モードがRGB/RGBA/Pなどのいずれであっても正しく処理する。
    透過部分がある場合は、白色を敷いてから合成する。

    読み込みに失敗した場合はStoryImageErrorを送出する。
    """
    try:
        image = Image.open(io.BytesIO(file_bytes))
        image = ImageOps.exif_transpose(image)
    except Exception as e:
        raise StoryImageError(
            "背景画像を読み込めませんでした。別の画像でお試しください。",
            detail=str(e),
        )

    has_alpha = image.mode in ("RGBA", "LA") or (
        image.mode == "P" and "transparency" in image.info
    )
    if has_alpha:
        image = image.convert("RGBA")
        flattened = Image.new("RGB", image.size, (255, 255, 255))
        flattened.paste(image, mask=image.split()[-1])
        image = flattened
    else:
        image = image.convert("RGB")

    return image


def _cover_resize(image: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """
    CSSのbackground-size: coverと同じ考え方で、
    画像を引き伸ばさずに縦横比を保ったまま、指定サイズ全体を余白なく埋める。
    はみ出した部分は中央基準でトリミングする。
    """
    src_w, src_h = image.size
    scale = max(target_w / src_w, target_h / src_h)
    new_w = max(1, round(src_w * scale))
    new_h = max(1, round(src_h * scale))
    resized = image.resize((new_w, new_h), Image.LANCZOS)

    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return resized.crop((left, top, left + target_w, top + target_h))


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


def generate_story_image(
    profile_image_url,
    account_name,
    original_text,
    own_replies,
    background_image=None,
    text_color=DEFAULT_TEXT_COLOR,
    max_font_size=DEFAULT_MAX_FONT_SIZE,
    overlay_opacity=0.0,
):
    """
    Threadsの投稿内容から1080x1920のInstagramストーリーズ用PNG画像を生成する。

    original_text・own_repliesの文字列は一切変更せず、そのまま描画する。
    文字量が多い場合、max_font_sizeを上限としてフォントサイズを自動的に縮小し、
    全文が画像内に収まるようにする（max_font_sizeより大きくすることはない）。

    引数:
        background_image: load_background_image()で読み込み済みのRGB画像、
                           またはNone（Noneの場合は白背景を使用）。
        text_color: 本文・アカウント名の文字色（"#RRGGBB"形式）。
        max_font_size: ユーザーが希望する本文フォントサイズの上限。
        overlay_opacity: 背景画像の上に重ねる黒レイヤーの不透明度（0.0〜0.8）。
                         background_imageがNoneの場合は無視される。

    戻り値: (PNGのバイト列, 警告メッセージ または None)
    """
    canvas = Image.new("RGBA", (CANVAS_WIDTH, CANVAS_HEIGHT), BACKGROUND_COLOR + (255,))

    if background_image is not None:
        covered = _cover_resize(background_image, CANVAS_WIDTH, CANVAS_HEIGHT)
        canvas.paste(covered.convert("RGBA"), (0, 0))

        opacity = max(0.0, min(overlay_opacity or 0.0, MAX_OVERLAY_OPACITY))
        if opacity > 0:
            overlay = Image.new("RGBA", canvas.size, (0, 0, 0, round(opacity * 255)))
            canvas = Image.alpha_composite(canvas, overlay)

    draw = ImageDraw.Draw(canvas)
    text_rgb = _hex_to_rgb(text_color)

    max_width = CANVAS_WIDTH - MARGIN_X * 2
    name_font = _load_font(NAME_FONT_SIZE)

    header_height = max(PROFILE_DIAMETER, NAME_FONT_SIZE + 10)
    available_height = (
        CANVAS_HEIGHT - MARGIN_TOP - MARGIN_BOTTOM - header_height - SECTION_GAP
    )

    blocks = [original_text or ""] + [r or "" for r in own_replies]

    font_size = max(MIN_BODY_FONT_SIZE, max_font_size or DEFAULT_MAX_FONT_SIZE)
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
    draw.text((name_x, name_y), account_name or "", font=name_font, fill=text_rgb)

    # --- 本文・返信 ---
    line_height = int(font_size * LINE_HEIGHT_RATIO)
    y = MARGIN_TOP + header_height + SECTION_GAP

    for i, lines in enumerate(wrapped_blocks):
        for line in lines:
            draw.text((MARGIN_X, y), line, font=body_font, fill=text_rgb)
            y += line_height
        if i < len(wrapped_blocks) - 1:
            y += BLOCK_GAP

    buffer = io.BytesIO()
    canvas.convert("RGB").save(buffer, format="PNG")
    return buffer.getvalue(), warning
