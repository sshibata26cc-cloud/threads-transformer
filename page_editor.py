"""
ストーリーズ・カルーセルの、プレビュー上で直接文章を編集できる編集画面。

Streamlit標準のウィジェットでは「画像の上で文字を編集する」ことができないため、
page_editor_component/index.html を双方向コンポーネントとして読み込む。

仕組み:
- ブラウザ側で、画像と同じフォント（app_fonts.pyが配信する同梱フォント）を使い、
  画像と同じ大きさ・余白で文章を組んで表示する。文字はそのまま入力・削除でき、
  行ごとの寄せ・選択した文字のサイズ変更・（ストーリーズは）改ページができる。
- 編集内容（rich_text.pyの文書形式）と一緒に、ブラウザが実際に組んだ配置
  （行ごとのベースラインと1文字ごとのx座標）がPython側へ返る。
- 完成画像は、その配置どおりにPython側（rich_text.render_lines_layer）で描く。
  折り返し位置や文字の位置が、編集中の見た目と完成画像で一致する。

背景（画像・動画の1コマ）もここに重ねて表示し、「背景を動かす」を押している
間はドラッグ（スマホではスライド）で位置を調整できる。
"""

import base64
import io
import os

import streamlit.components.v1 as components
from PIL import Image

from rich_text import normalize_doc, normalize_lines
from story_image import make_background_fill

_component = components.declare_component(
    "page_editor",
    path=os.path.join(os.path.dirname(__file__), "page_editor_component"),
)

# プレビュー用に背景を縮小するときの長辺の上限（通信量を抑えるため）。
PREVIEW_BG_MAX_SIDE = 1280

# ページの持ち方。
STRUCTURE_LOCAL = "local"  # 改ページ・結合を編集画面の中で行う（ストーリーズ）
STRUCTURE_HOST = "host"  # ページの追加・削除・並び替えはPython側で行う（カルーセル）

_MAX_PAGES = 60


def to_data_url(data: bytes, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


def background_preview_data_url(image: Image.Image) -> str:
    """背景画像を、プレビュー表示用の軽いJPEG（data URL）に変換する。"""
    preview = image.copy()
    preview.thumbnail((PREVIEW_BG_MAX_SIDE, PREVIEW_BG_MAX_SIDE), Image.LANCZOS)
    buffer = io.BytesIO()
    preview.save(buffer, format="JPEG", quality=82)
    return to_data_url(buffer.getvalue(), "image/jpeg")


def background_fill_data_url(image: Image.Image) -> str:
    """余白を埋めるぼかし背景（小さなJPEGのdata URL）を作る。ブラウザ側で引き伸ばして使う。"""
    buffer = io.BytesIO()
    make_background_fill(image).save(buffer, format="JPEG", quality=90)
    return to_data_url(buffer.getvalue(), "image/jpeg")


def page_editor(pages, rev, style, canvas, key, background=None, structure=STRUCTURE_LOCAL, current=None):
    """
    編集画面を表示し、ブラウザから返ってきた値（未加工）を返す。
    値の読み取りには read_editor_value() を使う。

    引数:
        pages: [{"id": ページID, "doc": 文書}, ...]。編集画面は初回と、revが
               変わったときだけ文書を読み込む（入力中の内容を、再実行のたびに
               上書きしてしまわないため）。STRUCTURE_HOSTでは、ページの増減・
               並び順だけは毎回これに合わせる。
        rev: Python側で文書を作り直すたびに進める番号。
        style: {"key": 組版に影響する設定の識別子, "font": app_fonts.browser_font(),
                "fallback": app_fonts.browser_fallback_font(), "base": 標準の文字サイズ,
                "color": 文字色, "band": 文字の背景色 または None}
        canvas: {"w", "h": 画像の大きさ, "mx", "mt", "mb": 左右・上・下の余白,
                 "color": 地の色}
        background: {"url": 背景のdata URL, "fill": 余白を埋めるぼかし背景 または None,
                     "w", "h": 背景の元の大きさ, "fit": "width" | "height" | "cover",
                     "shade": 背景の暗さ, "token": 背景と差し込み方の識別子}。
                    背景なしの場合はNone。
        structure: STRUCTURE_LOCAL または STRUCTURE_HOST。
        current: STRUCTURE_HOSTのときに表示・編集するページのID。
    """
    return _component(
        pages=pages,
        rev=rev,
        style=style,
        canvas=canvas,
        bg=background,
        structure=structure,
        current=current,
        key=key,
        default=None,
    )


def read_editor_value(value, rev, style_key, canvas):
    """
    編集画面から返ってきた値を読み取る。使えない場合はNoneを返す。

    戻り値: {"pages": [{"id", "doc", "lines", "overflow", "fallback"}, ...],
             "offset": (背景のずれx, y, 背景の識別子)}
    "lines"は完成画像を描くための配置で、いまの設定（style_key）で組まれたもので
    ない場合はNone（その場合はPython側で組んだ画像で代用する）。

    コンポーネントの値は再実行をまたいで残るため、Python側で文書を作り直した
    （revを進めた）後に残っている古い値は、revが違うことで見分けて捨てる。
    """
    if not isinstance(value, dict) or value.get("rev") != rev:
        return None
    raw_pages = value.get("pages")
    if not isinstance(raw_pages, list):
        return None
    lines_usable = value.get("style_key") == style_key
    canvas_size = (canvas["w"], canvas["h"])

    pages = []
    for raw in raw_pages[:_MAX_PAGES]:
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
            continue
        pages.append(
            {
                "id": raw["id"][:64],
                "doc": normalize_doc(raw.get("doc"), allow_pagebreak=False),
                "lines": normalize_lines(raw.get("lines"), canvas_size) if lines_usable else None,
                "overflow": bool(raw.get("overflow")),
                "fallback": bool(raw.get("fallback")),
            }
        )

    raw_offset = value.get("offset") if isinstance(value.get("offset"), dict) else {}
    offset = tuple(
        v if isinstance(v, (int, float)) and not isinstance(v, bool) else 0
        for v in (raw_offset.get("x"), raw_offset.get("y"))
    ) + (raw_offset.get("token"),)
    return {"pages": pages, "offset": offset}


def inject_mobile_media_picker_fix():
    """
    背景の画像・動画アップロード欄で、スマホの写真ライブラリから動画も
    選べるようにする。

    st.file_uploaderは許可するファイルを拡張子（.mp4 など）だけで
    <input accept="..."> に指定する。スマホの写真選択画面は拡張子ではなく
    MIMEタイプ（image/* / video/*）で「写真か動画か」を判断するため、
    拡張子だけの指定だと動画が一覧に出ない・選べないことがある。
    そこで、動画の拡張子を許可しているアップロード欄に限り、acceptへ
    image/*,video/* を追記する（Streamlitが再描画で元に戻しても追記し直す）。

    スクリプトは親ページ側に1度だけ登録する（iframe内で動かすと、再実行で
    iframeが破棄されたときに監視も止まってしまうため。carousel_ui.pyと同じ方式）。
    """
    components.html(
        """
        <script>
        (function () {
            var win = window.parent;
            if (win.__ttMediaPickerFix) return;
            win.__ttMediaPickerFix = true;
            var script = win.document.createElement("script");
            script.textContent = "(" + function () {
                function patch() {
                    var inputs = document.querySelectorAll('input[type="file"]');
                    for (var i = 0; i < inputs.length; i++) {
                        var accept = inputs[i].getAttribute("accept") || "";
                        if (accept.indexOf(".mp4") !== -1 && accept.indexOf("video/*") === -1) {
                            inputs[i].setAttribute("accept", accept + ",image/*,video/*");
                        }
                    }
                }
                new MutationObserver(patch).observe(document.body, {
                    childList: true, subtree: true, attributes: true, attributeFilter: ["accept"]
                });
                patch();
            } + ")();";
            win.document.head.appendChild(script);
        })();
        </script>
        """,
        height=0,
    )
