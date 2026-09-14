"""
Threadsの投稿内容から、Instagramストーリーズ用のPNG画像を生成するモジュール。

文章はThreadsから取得した原文をそのまま使い、要約・言い換え・省略は行わない。
文字量に応じてフォントサイズを自動的に小さくし、1080x1920pxの画像内に
全文が収まるようにする。

背景画像・文字色・文字の背景色・文字サイズ（の上限）・背景の暗さ・フォントは、
呼び出し側（Streamlit画面）からユーザーが指定できる。

フォントの選択肢・同梱フォントファイル・ライセンスについては
app_fonts.py と fonts/FONTS_NOTICE.txt を参照。
"""

import io
import re

import requests
import streamlit as st
from PIL import Image, ImageDraw, ImageFont, ImageOps

from app_fonts import DEFAULT_FONT_KEY, get_font

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
DEFAULT_TEXT_BG_COLOR = "#FFFFFF"  # 「色を設定」を選んだときのカラーピッカー初期値
DEFAULT_MAX_FONT_SIZE = 44
MIN_BODY_FONT_SIZE = 20
FONT_STEP = 2
MAX_OVERLAY_OPACITY = 0.8

LINE_HEIGHT_RATIO = 1.55

# 文字の背景色（文字の下に敷く帯）の余白・角丸の大きさ。
# いずれもフォントサイズに対する比率で指定するため、
# 文字サイズが自動調整されても見た目のバランスが崩れない。
TEXT_BG_PAD_X_RATIO = 0.30
TEXT_BG_PAD_Y_RATIO = 0.12
TEXT_BG_RADIUS_RATIO = 0.22

class StoryImageError(Exception):
    """ストーリーズ画像の生成中に発生したエラー。"""

    def __init__(self, friendly_message, detail=None):
        super().__init__(friendly_message)
        self.friendly_message = friendly_message
        self.detail = detail


def _load_font(size: int, font_key: str = DEFAULT_FONT_KEY, sample_text: str = "") -> ImageFont.FreeTypeFont:
    """
    フォントを読み込む。実際のフォント選択肢・ファイルの対応はapp_fonts.pyに
    集約されており、Story・Carouselの両方からこの関数（または
    app_fonts.get_font）経由で同じ実装を利用する。
    """
    try:
        return get_font(font_key, size, sample_text=sample_text)
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


@st.cache_data(show_spinner=False, max_entries=10)
def _fetch_circular_profile_image(url, diameter: int):
    """
    プロフィール画像を取得し円形に切り抜く。取得できない場合はNoneを返す。

    プロフィール画像はThreadsアカウントを変換し直すまで変わらないが、
    文字サイズ・フォント・背景の暗さなど他のデザイン設定を変えるたびに
    Story画像全体が再生成され、そのたびにこの関数も呼ばれてしまう。
    (url, diameter)だけで結果が一意に決まる純粋な処理なので、
    ネットワーク取得を毎回繰り返さないようキャッシュする。
    """
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


def _draw_text_backgrounds(draw, text_items, bg_rgb):
    """
    文字の背後に、指定された色の帯（角丸の長方形）を敷く。

    text_itemsは (x, y, 行の文字列, フォント) のリストで、
    実際に文字を描画するときとまったく同じ座標を受け取る。
    そのため、この処理を行っても文字の位置・改行・余白・
    自動文字サイズ調整には一切影響しない。

    帯の高さはフォントの実際の高さ（ascent + descent）を基準にしており、
    行間（フォントサイズ x LINE_HEIGHT_RATIO）より必ず小さくなるので、
    上下の行の帯どうしが重なることはない。
    """
    for x, y, line, font in text_items:
        if not line.strip():
            continue  # 空行には帯を敷かない（改行の見た目はそのまま）

        width = draw.textlength(line, font=font)
        if width <= 0:
            continue

        ascent, descent = font.getmetrics()
        pad_x = max(4, round(font.size * TEXT_BG_PAD_X_RATIO))
        pad_y = max(2, round(font.size * TEXT_BG_PAD_Y_RATIO))
        radius = max(2, round(font.size * TEXT_BG_RADIUS_RATIO))

        draw.rounded_rectangle(
            (
                x - pad_x,
                y - pad_y,
                x + width + pad_x,
                y + ascent + descent + pad_y,
            ),
            radius=radius,
            fill=bg_rgb,
        )


def generate_story_image(
    profile_image_url,
    account_name,
    original_text,
    own_replies,
    background_image=None,
    text_color=DEFAULT_TEXT_COLOR,
    text_bg_color=None,
    max_font_size=DEFAULT_MAX_FONT_SIZE,
    overlay_opacity=0.0,
    font_key=DEFAULT_FONT_KEY,
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
        text_bg_color: 文字の背景色（"#RRGGBB"形式）。
                       None（既定値）の場合は文字背景を描画せず、
                       これまでとまったく同じ見た目になる。
        max_font_size: ユーザーが希望する本文フォントサイズの上限。
        overlay_opacity: 背景画像の上に重ねる黒レイヤーの不透明度（0.0〜0.8）。
                         background_imageがNoneの場合は無視される。
        font_key: app_fonts.FONT_OPTIONSのいずれか。本文・アカウント名の
                  両方に適用する（現状どちらも同じフォントを使っているため）。

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
    text_bg_rgb = _hex_to_rgb(text_bg_color) if text_bg_color else None

    max_width = CANVAS_WIDTH - MARGIN_X * 2

    blocks = [original_text or ""] + [r or "" for r in own_replies]
    # 「Arial」選択時に日本語が含まれるかどうかの判定用。
    # アカウント名も本文もまとめて渡し、どちらかに日本語が含まれていれば
    # 日本語対応フォントへ自動的に切り替える。
    sample_text = (account_name or "") + "".join(blocks)
    name_font = _load_font(NAME_FONT_SIZE, font_key=font_key, sample_text=sample_text)

    header_height = max(PROFILE_DIAMETER, NAME_FONT_SIZE + 10)
    available_height = (
        CANVAS_HEIGHT - MARGIN_TOP - MARGIN_BOTTOM - header_height - SECTION_GAP
    )

    font_size = max(MIN_BODY_FONT_SIZE, max_font_size or DEFAULT_MAX_FONT_SIZE)
    body_font = _load_font(font_size, font_key=font_key, sample_text=sample_text)
    line_height = int(font_size * LINE_HEIGHT_RATIO)
    wrapped_blocks, total_height = _measure_blocks(
        draw, blocks, body_font, max_width, line_height
    )

    while total_height > available_height and font_size > MIN_BODY_FONT_SIZE:
        font_size -= FONT_STEP
        body_font = _load_font(font_size, font_key=font_key, sample_text=sample_text)
        line_height = int(font_size * LINE_HEIGHT_RATIO)
        wrapped_blocks, total_height = _measure_blocks(
            draw, blocks, body_font, max_width, line_height
        )

    warning = None
    if total_height > available_height:
        warning = (
            "文章量が多いため、文字サイズがかなり小さくなっています。"
            "画像が見づらい場合は、投稿を分けることをおすすめします。"
        )

    # --- コンテンツ全体（アイコン＋アカウント名＋本文）の上下中央配置 ---
    # 「最終的なフォントサイズを決める（自動縮小）→ その結果で高さを測る→
    #  中央位置を計算する」という順序を守るため、この計算は必ず自動縮小
    # ループより後（＝font_size・wrapped_blocks・total_heightが確定した後）
    # に行う。ここより後でfont_sizeを変更してはならない。
    #
    # total_height（_measure_blocksの合計）は、各行を「行間
    # （line_height = font_size × LINE_HEIGHT_RATIO）」という枠1つぶんとして
    # 積み上げた値であり、最後の行の枠の中では、実際の文字の高さ
    # （font.getmetrics()のascent+descent。文字背景ONの場合はその余白pad_yも
    # 含む）より下に、次の行のための余白が使われずに残っている。
    # 中央配置の計算にそのままtotal_heightを使うと、実際には描画されていない
    # この「最後の行の余った下余白」ぶんだけ、見た目の内容全体が本来の中央より
    # 上へずれてしまう（＝下側の余白が実際より広く見える）。
    # そのため、最後の行についてだけ「枠の高さ」ではなく「実際に描画される
    # 高さ」に置き換えてから中央位置を計算する。
    content_total_height = header_height + SECTION_GAP + total_height
    if wrapped_blocks and wrapped_blocks[-1]:
        ascent, descent = body_font.getmetrics()
        real_last_line_height = ascent + descent
        if text_bg_rgb is not None:
            # 文字背景の帯は、行の実際の文字より下（pad_yぶん）まで描画される。
            # 帯ごと中央に揃えるため、帯の下端までを「実際の高さ」とみなす。
            real_last_line_height += max(2, round(font_size * TEXT_BG_PAD_Y_RATIO))
        trailing_slack = max(0, line_height - real_last_line_height)
        content_total_height -= trailing_slack
    # 極端に文章量が多く自動縮小しても収まりきらない場合は、これまで通り
    # 上（MARGIN_TOP）を基準にする（中央寄せしようとして上端がマージンより
    # 上にはみ出さないようにするための安全策）。
    start_y = max(MARGIN_TOP, (CANVAS_HEIGHT - content_total_height) // 2)

    # --- ヘッダー（プロフィール画像 + アカウント名） ---
    profile_circle = _fetch_circular_profile_image(profile_image_url, PROFILE_DIAMETER)
    header_y = start_y
    if profile_circle:
        canvas.paste(profile_circle, (MARGIN_X, header_y), mask=profile_circle)
        name_x = MARGIN_X + PROFILE_DIAMETER + HEADER_GAP
    else:
        name_x = MARGIN_X

    name_y = header_y + (PROFILE_DIAMETER - NAME_FONT_SIZE) // 2

    # 文字の描画位置を先にすべて決めてから、
    # 「背景の帯 → 文字」の順に描画する（帯が文字を覆わないようにするため）。
    text_items = [(name_x, name_y, account_name or "", name_font)]

    # --- 本文・返信 ---
    # line_heightは、上の中央配置計算で使ったものと必ず同じ値を使う
    # （自動縮小ループの後に確定したfont_sizeから計算済みのものを再利用する）。
    y = start_y + header_height + SECTION_GAP

    for i, lines in enumerate(wrapped_blocks):
        for line in lines:
            text_items.append((MARGIN_X, y, line, body_font))
            y += line_height
        if i < len(wrapped_blocks) - 1:
            y += BLOCK_GAP

    if text_bg_rgb is not None:
        _draw_text_backgrounds(draw, text_items, text_bg_rgb)

    for item_x, item_y, line, item_font in text_items:
        draw.text((item_x, item_y), line, font=item_font, fill=text_rgb)

    buffer = io.BytesIO()
    canvas.convert("RGB").save(buffer, format="PNG")
    return buffer.getvalue(), warning


DEFAULT_JPEG_QUALITY = 95


def convert_png_to_jpeg(png_bytes: bytes, quality: int = DEFAULT_JPEG_QUALITY) -> bytes:
    """
    generate_story_image()が生成したPNG画像を、Instagram投稿用に
    高画質のJPEG（RGB、1080x1920）へ変換する。見た目は変更しない。
    """
    image = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()
