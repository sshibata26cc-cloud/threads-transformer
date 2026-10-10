"""
Instagramカルーセル投稿用のページ画像（1080x1350 / 4:5）を生成するモジュール。

背景画像のcover/crop、文字の折り返し、文字サイズの自動縮小、行ごとの寄せ、
文字の背景色（帯）の描画など、画像処理の中身はStoryズ機能
（story_image.py / rich_text.py）の処理をそのまま再利用しており、
重複実装はしていない。

Storyズ機能と同じく、ページの文章のみを縦方向に中央寄せして表示する
（背景は常にcover＝全面を埋める差し込み方で、位置調整はない）。
"""

from PIL import Image

from app_fonts import DEFAULT_FONT_KEY
from rich_text import render_rich_text_layer
from story_image import (
    BACKGROUND_COLOR,
    DEFAULT_MAX_FONT_SIZE,
    DEFAULT_TEXT_COLOR,
    MARGIN_X,
    MAX_OVERLAY_OPACITY,
    _cover_resize,
    image_to_png_bytes,
)

CAROUSEL_WIDTH = 1080
CAROUSEL_HEIGHT = 1350

# 左右の余白はStoryズと共通、上下の余白はカルーセル用に別途定義する。
CAROUSEL_MARGIN_X = MARGIN_X
CAROUSEL_MARGIN_Y = 100


def generate_carousel_page_image(
    doc,
    background_image=None,
    text_color=DEFAULT_TEXT_COLOR,
    text_bg_color=None,
    max_font_size=DEFAULT_MAX_FONT_SIZE,
    overlay_opacity=0.0,
    font_key=DEFAULT_FONT_KEY,
):
    """
    カルーセルの1ページ分（1080x1350）のPNG画像を生成する。

    引数:
        doc: 1ページぶんの文書（rich_text.pyの形式）。
        background_image: load_background_image()で読み込み済みのRGB画像、
                           またはNone（Noneの場合は白背景を使用）。
        text_color: 文字色（"#RRGGBB"形式）。
        text_bg_color: 文字の背景色（"#RRGGBB"形式）。
                       None（既定値）の場合は文字背景を描画しない（透明）。
        max_font_size: 標準の文字サイズ（部分的にサイズを指定していない文字に使う）。
        overlay_opacity: 背景画像の上に重ねる黒レイヤーの不透明度（0.0〜0.8）。
                         background_imageがNoneの場合は無視される。
        font_key: app_fonts.FONT_OPTIONSのいずれか（全ページ共通の1つを想定）。

    戻り値: (PNGのバイト列, 警告メッセージ または None)
    """
    canvas = Image.new("RGB", (CAROUSEL_WIDTH, CAROUSEL_HEIGHT), BACKGROUND_COLOR)

    if background_image is not None:
        canvas.paste(_cover_resize(background_image, CAROUSEL_WIDTH, CAROUSEL_HEIGHT), (0, 0))

        opacity = max(0.0, min(overlay_opacity or 0.0, MAX_OVERLAY_OPACITY))
        if opacity > 0:
            canvas = Image.blend(canvas, Image.new("RGB", canvas.size, (0, 0, 0)), opacity)

    text_layer, warning = render_rich_text_layer(
        doc,
        (CAROUSEL_WIDTH, CAROUSEL_HEIGHT),
        CAROUSEL_MARGIN_X,
        CAROUSEL_MARGIN_Y,
        CAROUSEL_MARGIN_Y,
        text_color=text_color,
        text_bg_color=text_bg_color,
        base_size=max_font_size,
        font_key=font_key,
        overflow_hint="ページを分けるか、文章を短くしてください。",
    )
    canvas.paste(text_layer, (0, 0), mask=text_layer)

    return image_to_png_bytes(canvas), warning
