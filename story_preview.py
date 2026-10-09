"""
ストーリーズのプレビュー表示（背景のドラッグ位置調整・改行／改ページの操作つき）。

Streamlit標準のウィジェットではドラッグ操作を受け取れないため、
story_preview_component/index.html を双方向コンポーネントとして読み込む。
ブラウザ側では「背景」「暗さ」「文字レイヤー（透明PNG）」を重ねて表示し、
背景をドラッグ（スマホではスライド）すると、その場で背景だけが動く。
指を離した時点の位置がPython側へ返り、PNG・動画の書き出しに使われる。

文字をクリック（タップ）するとその位置にカーソルが立ち、「改行」「改ページ」
ボタンで、カーソル位置への操作がPython側へ返る（実際の文章の変更は
story_pages.pyで行う）。
"""

import base64
import io
import os

import streamlit.components.v1 as components
from PIL import Image

from story_image import CANVAS_HEIGHT, CANVAS_WIDTH, LETTERBOX_COLOR, make_background_fill

_component = components.declare_component(
    "story_preview",
    path=os.path.join(os.path.dirname(__file__), "story_preview_component"),
)

# プレビュー用に背景を縮小するときの長辺の上限（通信量を抑えるため）。
PREVIEW_BG_MAX_SIDE = 1280


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


def story_preview(
    text_layer_url,
    layout,
    bg_url,
    bg_size,
    fit,
    shade,
    token,
    key,
    page_id,
    page_index=0,
    page_count=1,
    can_undo=False,
    fill_url=None,
):
    """
    プレビューを表示する。

    引数:
        text_layer_url: 文字レイヤー（透明PNG）のdata URL。
        layout: render_story_text_layer_with_layout()の行ごとの配置情報。
                プレビュー上で文字の間にカーソルを置くために使う。
        bg_url: 背景のdata URL。背景なしの場合はNone。
        bg_size: 背景の元の (幅, 高さ)。背景なしの場合はNone。
        fill_url: 余白を埋めるぼかし背景のdata URL。背景なしの場合はNone。
        fit: story_image.BG_FIT_WIDTH / BG_FIT_HEIGHT。
        shade: 背景の暗さ（0.0〜0.8）。
        token: 背景と差し込み方を表す識別子。変わると位置は中央へ戻る。
        page_id / page_index / page_count: 表示中のページ。
        can_undo: 「元に戻す」を押せる状態かどうか。

    戻り値: (背景のずれ (x, y)（キャンバスのpx）, 操作 または None)
        操作は {"type": "newline" | "pagebreak" | "undo" | "reset" | "prev" | "next",
        "index": カーソルの文字位置（newline / pagebreakのみ）, "nonce": 操作ごとの識別子}。
        コンポーネントの値は再実行をまたいで残るので、同じ操作を二重に
        適用しないよう、呼び出し側でnonceを見て判定すること。
    """
    bg_w, bg_h = bg_size or (CANVAS_WIDTH, CANVAS_HEIGHT)
    value = _component(
        text=text_layer_url,
        layout=layout,
        bg=bg_url,
        fill=fill_url,
        bg_w=bg_w,
        bg_h=bg_h,
        fit=fit,
        shade=shade,
        token=token,
        page_id=page_id,
        page_index=page_index,
        page_count=page_count,
        can_undo=can_undo,
        canvas_w=CANVAS_WIDTH,
        canvas_h=CANVAS_HEIGHT,
        canvas_color="#%02X%02X%02X" % LETTERBOX_COLOR,
        key=key,
        default=None,
    )
    if not value:
        return (0, 0), None
    # 別の背景・別の差し込み方のときに返ってきた古い位置は使わない。
    offset = (value.get("x", 0), value.get("y", 0)) if value.get("token") == token else (0, 0)
    return offset, value.get("action")


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
