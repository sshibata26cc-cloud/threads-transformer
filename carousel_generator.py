"""
Instagramカルーセル投稿用のページ画像（1080x1350 / 4:5）を生成するモジュール。

背景画像のcover/crop、文字の折り返し、フォントサイズの自動縮小、
文字の背景色（帯）の描画など、画像処理の中身はStoryズ機能
（story_image.py）の処理をそのまま再利用しており、重複実装はしていない。

Storyズ機能と違い、カルーセルの各ページにはプロフィール画像・
アカウント名のヘッダーは表示せず、ページの文章のみを
縦方向に中央寄せして表示する。
"""

import io

from PIL import Image, ImageDraw

from app_fonts import DEFAULT_FONT_KEY
from story_image import (
    BACKGROUND_COLOR,
    DEFAULT_MAX_FONT_SIZE,
    DEFAULT_TEXT_COLOR,
    FONT_STEP,
    LINE_HEIGHT_RATIO,
    MARGIN_X,
    MAX_OVERLAY_OPACITY,
    MIN_BODY_FONT_SIZE,
    _cover_resize,
    _draw_text_backgrounds,
    _hex_to_rgb,
    _load_font,
    _wrap_text_block,
)

CAROUSEL_WIDTH = 1080
CAROUSEL_HEIGHT = 1350

# 左右の余白はStoryズと共通、上下の余白はカルーセル用に別途定義する。
CAROUSEL_MARGIN_X = MARGIN_X
CAROUSEL_MARGIN_Y = 100


def generate_carousel_page_image(
    text,
    background_image=None,
    text_color=DEFAULT_TEXT_COLOR,
    text_bg_color=None,
    max_font_size=DEFAULT_MAX_FONT_SIZE,
    overlay_opacity=0.0,
    font_key=DEFAULT_FONT_KEY,
):
    """
    カルーセルの1ページ分（1080x1350）のPNG画像を生成する。

    textの内容は一切変更せず、そのまま描画する。
    文字量が多い場合、max_font_sizeを上限としてフォントサイズを自動的に
    縮小し、画像の外へはみ出さないようにする（Storyズと同じ考え方）。

    引数:
        background_image: load_background_image()で読み込み済みのRGB画像、
                           またはNone（Noneの場合は白背景を使用）。
        text_color: 文字色（"#RRGGBB"形式）。
        text_bg_color: 文字の背景色（"#RRGGBB"形式）。
                       None（既定値）の場合は文字背景を描画しない（透明）。
        max_font_size: ユーザーが希望するフォントサイズの上限。
        overlay_opacity: 背景画像の上に重ねる黒レイヤーの不透明度（0.0〜0.8）。
                         background_imageがNoneの場合は無視される。
        font_key: app_fonts.FONT_OPTIONSのいずれか（全ページ共通の1つを想定）。

    戻り値: (PNGのバイト列, 警告メッセージ または None)
    """
    canvas = Image.new("RGBA", (CAROUSEL_WIDTH, CAROUSEL_HEIGHT), BACKGROUND_COLOR + (255,))

    if background_image is not None:
        covered = _cover_resize(background_image, CAROUSEL_WIDTH, CAROUSEL_HEIGHT)
        canvas.paste(covered.convert("RGBA"), (0, 0))

        opacity = max(0.0, min(overlay_opacity or 0.0, MAX_OVERLAY_OPACITY))
        if opacity > 0:
            overlay = Image.new("RGBA", canvas.size, (0, 0, 0, round(opacity * 255)))
            canvas = Image.alpha_composite(canvas, overlay)

    draw = ImageDraw.Draw(canvas)
    text_rgb = _hex_to_rgb(text_color)
    text_bg_rgb = _hex_to_rgb(text_bg_color) if text_bg_color else None

    max_width = CAROUSEL_WIDTH - CAROUSEL_MARGIN_X * 2
    available_height = CAROUSEL_HEIGHT - CAROUSEL_MARGIN_Y * 2

    font_size = max(MIN_BODY_FONT_SIZE, max_font_size or DEFAULT_MAX_FONT_SIZE)
    body_font = _load_font(font_size, font_key=font_key, sample_text=text)
    lines = _wrap_text_block(draw, text or "", body_font, max_width)
    line_height = int(font_size * LINE_HEIGHT_RATIO)
    total_height = len(lines) * line_height

    while total_height > available_height and font_size > MIN_BODY_FONT_SIZE:
        font_size -= FONT_STEP
        body_font = _load_font(font_size, font_key=font_key, sample_text=text)
        lines = _wrap_text_block(draw, text or "", body_font, max_width)
        line_height = int(font_size * LINE_HEIGHT_RATIO)
        total_height = len(lines) * line_height

    warning = None
    if total_height > available_height:
        warning = (
            "文章量が多いため、文字サイズがかなり小さくなっています。"
            "ページを分けるか、文章を短くすることをおすすめします。"
        )

    # 文章全体を縦方向の中央に配置する。
    y = CAROUSEL_MARGIN_Y + max(0, (available_height - total_height) // 2)

    text_items = [(CAROUSEL_MARGIN_X, y + i * line_height, line, body_font) for i, line in enumerate(lines)]

    if text_bg_rgb is not None:
        _draw_text_backgrounds(draw, text_items, text_bg_rgb)

    for item_x, item_y, line, item_font in text_items:
        draw.text((item_x, item_y), line, font=item_font, fill=text_rgb)

    buffer = io.BytesIO()
    canvas.convert("RGB").save(buffer, format="PNG")
    return buffer.getvalue(), warning
