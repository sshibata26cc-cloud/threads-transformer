"""
Threadsの投稿内容から、Instagramストーリーズ用のPNG画像を生成するモジュール。

文章は「書式つきの文書」（rich_text.py。行ごとの寄せ・部分的な文字サイズを
持てる）として受け取り、1080x1920pxの画像に描画する。文字量が多い場合は
文字サイズを自動的に小さくして、全文が収まるようにする。

背景画像・文字色・文字の背景色・文字サイズ（標準）・背景の暗さ・フォントは、
呼び出し側（Streamlit画面）からユーザーが指定できる。

背景は「横いっぱい」「縦いっぱい」のどちらかで差し込み、位置（オフセット）も
指定できる。文字だけを描いた透明レイヤー（render_story_text_layer）と背景の
配置計算（compute_background_placement）を分けてあるので、ブラウザ上の
プレビュー・PNG書き出し・動画書き出し（story_video.py）のすべてが
同じ計算結果を使う。

フォントの選択肢・同梱フォントファイル・ライセンスについては
app_fonts.py と fonts/FONTS_NOTICE.txt を参照。
"""

import io

from PIL import Image, ImageFilter, ImageOps

from app_fonts import DEFAULT_FONT_KEY
from rich_text import RichTextError, render_rich_text_layer

CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1920

BACKGROUND_COLOR = (250, 250, 248)  # カルーセルで背景画像が指定されていない場合の白背景
# ストーリーズの地の色（#1E1E1E）。背景を指定しない場合に全面へ使う。
# 背景（画像・動画）を差し込んだときの余白は、この色ではなく
# make_background_fill()のぼかし背景で埋める。
LETTERBOX_COLOR = (30, 30, 30)
# 余白を埋めるぼかし背景の作り方（キャンバスの1/20に縮小してからぼかす）。
BG_FILL_DOWNSCALE = 20
BG_FILL_BLUR = 10  # 縮小後の画像に対するぼかしの強さ（px）

MARGIN_X = 72
MARGIN_TOP = 90
MARGIN_BOTTOM = 90

# 背景の差し込み方。
BG_FIT_WIDTH = "width"  # 横いっぱい（背景の横幅をキャンバスの横幅に合わせる）
BG_FIT_HEIGHT = "height"  # 縦いっぱい（背景の高さをキャンバスの高さに合わせる）
DEFAULT_BG_FIT = BG_FIT_WIDTH

DEFAULT_TEXT_COLOR = "#1E1E1E"  # カルーセル（白背景）の文字色の初期値
STORY_DEFAULT_TEXT_COLOR = "#FFFFFF"  # ストーリーズ（黒地）の文字色の初期値
DEFAULT_TEXT_BG_COLOR = "#FFFFFF"  # 「色を設定」を選んだときのカラーピッカー初期値
DEFAULT_MAX_FONT_SIZE = 35
MAX_OVERLAY_OPACITY = 0.8

# ストーリーズ・カルーセルの画像生成中に発生したエラー。
# 文字の描画（rich_text.py）のエラーと同じものとして扱う。
StoryImageError = RichTextError


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


def compute_background_placement(
    src_w,
    src_h,
    fit_mode=DEFAULT_BG_FIT,
    offset_x=0,
    offset_y=0,
    canvas_w=CANVAS_WIDTH,
    canvas_h=CANVAS_HEIGHT,
):
    """
    背景（画像・動画）をキャンバスへ差し込むときの、拡大縮小後のサイズと
    左上座標を計算する。戻り値: (幅, 高さ, 左, 上)（いずれもキャンバスのpx）。

    fit_modeがBG_FIT_WIDTHなら横幅を、BG_FIT_HEIGHTなら高さをキャンバスに
    ぴったり合わせる（縦横比は保つ）。offset_x・offset_yは「中央に置いた
    状態からのずれ」で、背景がキャンバスの外へ出ていかない範囲
    （はみ出している軸ははみ出しぶん、足りない軸は余白ぶん）に丸める。

    プレビュー用コンポーネント（story_preview_component/index.html）も
    これと同じ式で配置しているため、変更する場合は両方を揃えること。
    """
    if fit_mode == BG_FIT_HEIGHT:
        scale = canvas_h / src_h
    else:
        scale = canvas_w / src_w
    new_w = max(1, round(src_w * scale))
    new_h = max(1, round(src_h * scale))

    max_dx = abs(canvas_w - new_w) / 2
    max_dy = abs(canvas_h - new_h) / 2
    dx = max(-max_dx, min(max_dx, offset_x or 0))
    dy = max(-max_dy, min(max_dy, offset_y or 0))

    left = round((canvas_w - new_w) / 2 + dx)
    top = round((canvas_h - new_h) / 2 + dy)
    return new_w, new_h, left, top


def make_background_fill(image, canvas_w=CANVAS_WIDTH, canvas_h=CANVAS_HEIGHT):
    """
    背景が届かない余白を埋めるための「ぼかし背景」を作る。

    背景をキャンバス全面を覆う大きさ（縦長のキャンバスに横長の画像なら
    「縦いっぱい」と同じ大きさ）にして中央で切り抜き、強くぼかす。
    元の画像の色合いがそのまま上下（左右）の余白へグラデーションのように
    広がって見える。

    戻り値はキャンバスの 1/BG_FILL_DOWNSCALE の小さな画像で、使う側で
    キャンバスサイズへ引き伸ばす（強いぼかしなので細部は不要で、小さい方が
    速い）。プレビュー（ブラウザ）・動画書き出し（ffmpeg）も同じ縮小率・
    同じぼかしの強さで作っているので、変更する場合は揃えること。
    """
    small = _cover_resize(
        image.convert("RGB"), canvas_w // BG_FILL_DOWNSCALE, canvas_h // BG_FILL_DOWNSCALE
    )
    return small.filter(ImageFilter.GaussianBlur(BG_FILL_BLUR))


def _paste_background(canvas, image, fit_mode, offset, overlay_opacity):
    """
    背景画像を指定の差し込み方・位置で貼ったキャンバスを返す。
    余白はぼかし背景で埋め、暗さはキャンバス全体に重ねる。
    """
    offset_x, offset_y = offset or (0, 0)
    new_w, new_h, left, top = compute_background_placement(
        image.width, image.height, fit_mode, offset_x, offset_y, canvas.width, canvas.height
    )
    canvas.paste(make_background_fill(image, canvas.width, canvas.height).resize(canvas.size, Image.BICUBIC))
    canvas.paste(image.convert("RGB").resize((new_w, new_h), Image.LANCZOS), (left, top))

    opacity = max(0.0, min(overlay_opacity or 0.0, MAX_OVERLAY_OPACITY))
    if opacity > 0:
        canvas = Image.blend(canvas, Image.new("RGB", canvas.size, (0, 0, 0)), opacity)
    return canvas


def render_story_text_layer(
    doc,
    text_color=STORY_DEFAULT_TEXT_COLOR,
    text_bg_color=None,
    max_font_size=DEFAULT_MAX_FONT_SIZE,
    font_key=DEFAULT_FONT_KEY,
):
    """
    ストーリーズ1ページぶんの文書（rich_text.pyの形式）の文字だけを描いた、
    1080x1920の透明なRGBA画像を返す。背景は含まない。

    戻り値: (RGBA画像, 警告メッセージ または None)
    """
    return render_rich_text_layer(
        doc,
        (CANVAS_WIDTH, CANVAS_HEIGHT),
        MARGIN_X,
        MARGIN_TOP,
        MARGIN_BOTTOM,
        text_color=text_color,
        text_bg_color=text_bg_color,
        base_size=max_font_size,
        font_key=font_key,
        overflow_hint="文章を減らすか、「改ページ」で2枚以上に分けてください。",
    )


def image_to_png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def generate_story_image(
    doc,
    background_image=None,
    text_color=STORY_DEFAULT_TEXT_COLOR,
    text_bg_color=None,
    max_font_size=DEFAULT_MAX_FONT_SIZE,
    overlay_opacity=0.0,
    font_key=DEFAULT_FONT_KEY,
    bg_fit=DEFAULT_BG_FIT,
    bg_offset=(0, 0),
):
    """
    ストーリーズ1ページぶんの文書から、1080x1920のPNG画像を生成する。

    引数:
        doc: 1ページぶんの文書（rich_text.pyの形式）。
        background_image: load_background_image()で読み込み済みのRGB画像、
                           またはNone（Noneの場合は黒地を使用）。
        text_color: 本文の文字色（"#RRGGBB"形式）。
        text_bg_color: 文字の背景色（"#RRGGBB"形式）。
                       None（既定値）の場合は文字背景を描画しない。
        max_font_size: 標準の文字サイズ（部分的にサイズを指定していない文字に使う）。
        overlay_opacity: 背景（余白のぼかし背景を含む）に重ねる黒の不透明度（0.0〜0.8）。
                         background_imageがNoneの場合は無視される。
        font_key: app_fonts.FONT_OPTIONSのいずれか。
        bg_fit: BG_FIT_WIDTH（横いっぱい）または BG_FIT_HEIGHT（縦いっぱい）。
        bg_offset: 背景を中央からずらす量 (x, y)（キャンバスのpx）。

    戻り値: (PNGのバイト列, 警告メッセージ または None)
    """
    canvas = Image.new("RGB", (CANVAS_WIDTH, CANVAS_HEIGHT), LETTERBOX_COLOR)

    if background_image is not None:
        canvas = _paste_background(canvas, background_image, bg_fit, bg_offset, overlay_opacity)

    text_layer, warning = render_story_text_layer(
        doc,
        text_color=text_color,
        text_bg_color=text_bg_color,
        max_font_size=max_font_size,
        font_key=font_key,
    )
    canvas.paste(text_layer, (0, 0), mask=text_layer)

    return image_to_png_bytes(canvas), warning


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
