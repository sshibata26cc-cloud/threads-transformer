"""
ストーリーズ・カルーセルの文章を「書式つきの文書」として扱うためのモジュール。

文書（doc）は、上から順に並べた項目のリストで表す。
    段落:     {"align": "left" | "center" | "right",
               "runs": [{"t": 文字列, "s": 文字サイズ(px) または None}, ...]}
    改ページ: {"pb": True}   （ストーリーズのみ）

"s" がNoneの部分は、画面の「文字サイズ」で指定した標準サイズで描画する。
編集はブラウザ側の編集画面（page_editor.py）で行い、このモジュールは
文書の整形と、Pillowでの描画（文字だけの透明レイヤー）を受け持つ。
"""

from PIL import Image, ImageDraw

from app_fonts import DEFAULT_FONT_KEY, get_font

ALIGNMENTS = ("left", "center", "right")

MIN_BODY_FONT_SIZE = 20  # 自動縮小で標準サイズをここまで下げる
FONT_STEP = 2
LINE_HEIGHT_RATIO = 1.55

# 部分的に指定できる文字サイズの範囲（px。編集欄の「小さく／大きく」と揃える）。
MIN_RUN_SIZE = 12
MAX_RUN_SIZE = 160

# 文字の背景色（文字の下に敷く帯）の余白・角丸の大きさ。
# いずれもその行の文字サイズに対する比率で指定する。
TEXT_BG_PAD_X_RATIO = 0.30
TEXT_BG_PAD_Y_RATIO = 0.12
TEXT_BG_RADIUS_RATIO = 0.22

_ALIGN_FACTOR = {"left": 0.0, "center": 0.5, "right": 1.0}


class RichTextError(Exception):
    """文字の描画中に発生したエラー（フォントを読み込めない等）。"""

    def __init__(self, friendly_message, detail=None):
        super().__init__(friendly_message)
        self.friendly_message = friendly_message
        self.detail = detail


# --------------------------------------------------------------------
# 文書の整形
# --------------------------------------------------------------------
def text_to_doc(text: str):
    """プレーンテキストを、書式なし・左寄せの文書へ変換する（1行＝1段落）。"""
    # 編集画面（ブラウザ）で読み取ったときと同じ形になるよう、改行以外の
    # 特殊な空白（ノーブレークスペース・ゼロ幅スペース・CR）は先に整えておく。
    text = (text or "").replace("\r", "").replace("\u00a0", " ").replace("\u200b", "")
    return [
        {"align": "left", "runs": [{"t": line, "s": None}] if line else []}
        for line in text.split("\n")
    ]


def doc_to_text(doc) -> str:
    """文書から文字だけを取り出す（改ページは無視する）。"""
    return "\n".join(
        "".join(run["t"] for run in item["runs"]) for item in doc if not item.get("pb")
    )


def normalize_doc(raw, allow_pagebreak=True):
    """
    ブラウザの編集欄から受け取った値を、決まった形の文書に整える。
    想定外の値（型が違う・範囲外のサイズ等）は捨てるか既定値にする。
    """
    doc = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        if item.get("pb"):
            if allow_pagebreak:
                doc.append({"pb": True})
            continue
        align = item.get("align") if item.get("align") in ALIGNMENTS else "left"
        runs = []
        for run in item.get("runs") or []:
            if not isinstance(run, dict) or not isinstance(run.get("t"), str):
                continue
            text = run["t"].replace("\r", "").replace("\n", "")
            if not text:
                continue
            size = run.get("s")
            if isinstance(size, (int, float)) and not isinstance(size, bool) and size > 0:
                size = int(max(MIN_RUN_SIZE, min(MAX_RUN_SIZE, size)))
            else:
                size = None
            if runs and runs[-1]["s"] == size:
                runs[-1]["t"] += text
            else:
                runs.append({"t": text, "s": size})
        doc.append({"align": align, "runs": runs})
    return doc


def _trim_blank_paragraphs(paragraphs):
    """前後の空行（文字のない段落）を取り除く。"""
    start, end = 0, len(paragraphs)
    while start < end and not paragraphs[start]["runs"]:
        start += 1
    while end > start and not paragraphs[end - 1]["runs"]:
        end -= 1
    return paragraphs[start:end]


def split_pages(doc):
    """
    文書を改ページの位置で分け、ページごとの文書（段落のリスト）にして返す。
    各ページの前後の空行は取り除き、文字のないページは作らない。
    必ず1ページ以上を返す（全体が空なら空の1ページ）。
    """
    pages, current = [], []
    for item in doc:
        if item.get("pb"):
            pages.append(current)
            current = []
        else:
            current.append(item)
    pages.append(current)
    pages = [_trim_blank_paragraphs(page) for page in pages]
    pages = [page for page in pages if page]
    return pages or [[]]


# --------------------------------------------------------------------
# 描画
# --------------------------------------------------------------------
def _hex_to_rgb(hex_color, fallback=(30, 30, 30)):
    """"#RRGGBB" 形式の文字列をRGBのタプルに変換する。不正な値の場合はfallbackを返す。"""
    text = (hex_color or "").lstrip("#")
    if len(text) == 6:
        try:
            return tuple(int(text[i : i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            pass
    return fallback


def _split_tokens(text: str):
    """
    折り返しの単位に分ける。英数字の並びは途中で切らないよう1つにまとめ、
    それ以外（日本語・記号・空白）は1文字ずつにする。
    """
    tokens, word = [], ""
    for ch in text:
        if ch.isascii() and ch.isalnum():
            word += ch
            continue
        if word:
            tokens.append(word)
            word = ""
        tokens.append(ch)
    if word:
        tokens.append(word)
    return tokens


def _layout(draw, paragraphs, base_size, scale, max_width, font_for):
    """
    段落を折り返して、描画する行のリストを作る。

    各行は {"segs": [(文字列, フォント), ...], "width", "size", "ascent", "descent",
    "align"}。sizeはその行でいちばん大きい文字サイズで、行の高さの基準になる。
    """
    lines = []
    for paragraph in paragraphs:
        pieces = []  # (折り返し単位の文字列, フォント, 幅)
        for run in paragraph["runs"]:
            font = font_for(max(1, round((run["s"] or base_size) * scale)))
            for token in _split_tokens(run["t"]):
                pieces.append((token, font, draw.textlength(token, font=font)))

        rows, row, row_width = [], [], 0.0
        for token, font, width in pieces:
            if row and row_width + width > max_width:
                rows.append(row)
                row, row_width = [], 0.0
            if width <= max_width:
                row.append((token, font))
                row_width += width
                continue
            # 1単位だけで幅を超える（長い英数字など）場合は1文字ずつ折り返す。
            for ch in token:
                ch_width = draw.textlength(ch, font=font)
                if row and row_width + ch_width > max_width:
                    rows.append(row)
                    row, row_width = [], 0.0
                row.append((ch, font))
                row_width += ch_width
        if row or not rows:
            rows.append(row)  # 文字のない段落は、空の1行として残す

        base_font = font_for(max(1, round(base_size * scale)))
        for row in rows:
            # 同じフォントが続く部分は1つにまとめて描く（文字間の詰めを保つため）。
            segs = []
            for token, font in row:
                if segs and segs[-1][1] is font:
                    segs[-1] = (segs[-1][0] + token, font)
                else:
                    segs.append((token, font))
            fonts = [font for _, font in segs] or [base_font]
            lines.append(
                {
                    "segs": segs,
                    "width": sum(draw.textlength(text, font=font) for text, font in segs),
                    "size": max(font.size for font in fonts),
                    "ascent": max(font.getmetrics()[0] for font in fonts),
                    "descent": max(font.getmetrics()[1] for font in fonts),
                    "align": paragraph["align"],
                }
            )
    return lines


def render_rich_text_layer(
    doc,
    canvas_size,
    margin_x,
    margin_top,
    margin_bottom,
    text_color,
    text_bg_color=None,
    base_size=35,
    font_key=DEFAULT_FONT_KEY,
    overflow_hint="",
):
    """
    文書の文字（と文字の背景の帯）だけを描いた、canvas_sizeの透明なRGBA画像を返す。

    文章の内容は変更しない。全体が上下の余白の内側に収まらない場合は、
    すべての文字サイズ（標準サイズ・部分指定のサイズとも）を同じ比率で
    縮小していく（標準サイズがMIN_BODY_FONT_SIZEになるまで）。
    行は段落ごとの寄せ（左・中央・右）で配置し、全体は上下中央に置く。

    戻り値: (RGBA画像, 警告メッセージ または None)
    """
    canvas_w, canvas_h = canvas_size
    layer = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    text_rgb = _hex_to_rgb(text_color)
    text_bg_rgb = _hex_to_rgb(text_bg_color) if text_bg_color else None

    paragraphs = [item for item in doc if not item.get("pb")]
    # 「Arial」選択時に日本語が含まれるかどうかの判定用。
    sample_text = doc_to_text(paragraphs)

    def font_for(size):
        try:
            return get_font(font_key, size, sample_text=sample_text)
        except OSError as e:
            raise RichTextError("画像生成用のフォントファイルを読み込めませんでした。", detail=str(e))

    max_width = canvas_w - margin_x * 2
    available_height = canvas_h - margin_top - margin_bottom

    base_size = max(MIN_BODY_FONT_SIZE, int(base_size or MIN_BODY_FONT_SIZE))
    shrunk_size = base_size
    while True:
        lines = _layout(draw, paragraphs, base_size, shrunk_size / base_size, max_width, font_for)
        pitches = [int(line["size"] * LINE_HEIGHT_RATIO) for line in lines]
        total_height = sum(pitches)
        if total_height <= available_height or shrunk_size <= MIN_BODY_FONT_SIZE:
            break
        shrunk_size = max(MIN_BODY_FONT_SIZE, shrunk_size - FONT_STEP)

    warning = None
    if total_height > available_height:
        warning = "文章量が多いため、画像に収まりきっていません。" + overflow_hint

    # 上下中央に置く。最後の行は「行の高さ（行間込み）」ではなく実際に描く高さで
    # 数える（行間ぶんの余りを含めると、全体が中央より上へずれて見えるため）。
    content_height = total_height
    if lines:
        last = lines[-1]
        drawn = last["ascent"] + last["descent"]
        if text_bg_rgb is not None:
            drawn += max(2, round(last["size"] * TEXT_BG_PAD_Y_RATIO))
        content_height -= max(0, pitches[-1] - drawn)
    y = max(margin_top, margin_top + (available_height - content_height) // 2)

    placed = []  # (左端x, 行の上端y, 行)
    for line, pitch in zip(lines, pitches):
        x = margin_x + (max_width - line["width"]) * _ALIGN_FACTOR[line["align"]]
        placed.append((x, y, line))
        y += pitch

    # 「背景の帯 → 文字」の順に描く（帯が文字を覆わないようにするため）。
    if text_bg_rgb is not None:
        for x, top, line in placed:
            if line["width"] <= 0 or not "".join(text for text, _ in line["segs"]).strip():
                continue  # 空行には帯を敷かない
            pad_x = max(4, round(line["size"] * TEXT_BG_PAD_X_RATIO))
            pad_y = max(2, round(line["size"] * TEXT_BG_PAD_Y_RATIO))
            draw.rounded_rectangle(
                (
                    x - pad_x,
                    top - pad_y,
                    x + line["width"] + pad_x,
                    top + line["ascent"] + line["descent"] + pad_y,
                ),
                radius=max(2, round(line["size"] * TEXT_BG_RADIUS_RATIO)),
                fill=text_bg_rgb,
            )

    for x, top, line in placed:
        baseline = top + line["ascent"]  # 大きさの違う文字はベースラインで揃える
        for text, font in line["segs"]:
            draw.text((x, baseline), text, font=font, fill=text_rgb, anchor="ls")
            x += draw.textlength(text, font=font)

    return layer, warning


# --------------------------------------------------------------------
# ブラウザで決めた配置どおりに描く
# --------------------------------------------------------------------
# プレビュー上の編集（page_editor.py）では、ブラウザが同じフォントで文字を
# 折り返し・配置し、その結果（行ごとのベースラインと、1文字ごとのx座標）を
# 返してくる。画像は、その配置どおりにここで描くことで、編集中の見た目と
# 完成画像を一致させる。
#
# 配置（lines）は、上から順に並べた行のリスト。
#     {"y": ベースラインのy, "x0": 行の左端, "x1": 行の右端,
#      "g": [[x, 文字, 文字サイズ], ...]}     （座標・サイズはキャンバスのpx）
_MAX_LINES = 400
_MAX_GLYPHS_PER_LINE = 400


def _number(value, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or not low <= value <= high:  # NaN・範囲外
        return None
    return float(value)


def normalize_lines(raw, canvas_size):
    """
    ブラウザから受け取った配置を、決まった形に整える。
    形が想定と違うもの・キャンバスから大きく外れたものは捨てる。
    使える行が1つも取り出せない入力（リストでない等）の場合はNoneを返す。
    """
    if not isinstance(raw, list):
        return None
    canvas_w, canvas_h = canvas_size
    lines = []
    for item in raw[:_MAX_LINES]:
        if not isinstance(item, dict) or not isinstance(item.get("g"), list):
            continue
        y = _number(item.get("y"), -500, canvas_h + 500)
        if y is None:
            continue
        glyphs = []
        for glyph in item["g"][:_MAX_GLYPHS_PER_LINE]:
            if not isinstance(glyph, (list, tuple)) or len(glyph) != 3:
                continue
            x = _number(glyph[0], -500, canvas_w + 500)
            size = _number(glyph[2], 4, 600)
            text = glyph[1]
            if x is None or size is None or not isinstance(text, str) or not 0 < len(text) <= 16:
                continue
            glyphs.append((x, text, max(1, round(size))))
        if not glyphs:
            continue
        x0 = _number(item.get("x0"), -500, canvas_w + 500)
        x1 = _number(item.get("x1"), -500, canvas_w + 500)
        lines.append(
            {
                "y": y,
                "x0": glyphs[0][0] if x0 is None else x0,
                "x1": glyphs[-1][0] if x1 is None else x1,
                "g": glyphs,
            }
        )
    return lines


def render_lines_layer(lines, canvas_size, text_color, text_bg_color=None, font_key=DEFAULT_FONT_KEY):
    """
    normalize_lines()済みの配置どおりに文字（と文字の背景の帯）を描いた、
    canvas_sizeの透明なRGBA画像を返す。折り返し・寄せ・自動縮小はブラウザ側で
    済んでいるので、ここでは一切行わない。

    font_keyは実際に使うフォント（"Arial"で日本語を含むページの代替フォントへの
    切り替えは、呼び出し側で済ませておく）。
    """
    layer = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    text_rgb = _hex_to_rgb(text_color)
    text_bg_rgb = _hex_to_rgb(text_bg_color) if text_bg_color else None

    def font_for(size):
        try:
            return get_font(font_key, size)
        except OSError as e:
            raise RichTextError("画像生成用のフォントファイルを読み込めませんでした。", detail=str(e))

    if text_bg_rgb is not None:
        for line in lines:
            size = max(glyph[2] for glyph in line["g"])
            ascent, descent = font_for(size).getmetrics()
            pad_x = max(4, round(size * TEXT_BG_PAD_X_RATIO))
            pad_y = max(2, round(size * TEXT_BG_PAD_Y_RATIO))
            draw.rounded_rectangle(
                (
                    line["x0"] - pad_x,
                    line["y"] - ascent - pad_y,
                    line["x1"] + pad_x,
                    line["y"] + descent + pad_y,
                ),
                radius=max(2, round(size * TEXT_BG_RADIUS_RATIO)),
                fill=text_bg_rgb,
            )

    for line in lines:
        for x, text, size in line["g"]:
            draw.text((x, line["y"]), text, font=font_for(size), fill=text_rgb, anchor="ls")

    return layer
