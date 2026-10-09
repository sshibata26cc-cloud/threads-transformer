"""
ストーリーズのプレビュー表示（背景のドラッグ位置調整つき）。

Streamlit標準のウィジェットではドラッグ操作を受け取れないため、
story_preview_component/index.html を双方向コンポーネントとして読み込む。
ブラウザ側では「背景」「暗さ」「文字レイヤー（透明PNG）」を重ねて表示し、
背景をドラッグ（スマホではスライド）すると、その場で背景だけが動く。
指を離した時点の位置がPython側へ返り、PNG・動画の書き出しに使われる。
"""

import base64
import io
import os

import streamlit.components.v1 as components
from PIL import Image

from story_image import CANVAS_HEIGHT, CANVAS_WIDTH, LETTERBOX_COLOR

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


def story_preview(text_layer_url, bg_url, bg_size, fit, shade, token, key):
    """
    プレビューを表示し、現在の背景のずれ (x, y)（キャンバスのpx）を返す。

    引数:
        text_layer_url: 文字レイヤー（透明PNG）のdata URL。
        bg_url: 背景のdata URL。背景なしの場合はNone。
        bg_size: 背景の元の (幅, 高さ)。背景なしの場合はNone。
        fit: story_image.BG_FIT_WIDTH / BG_FIT_HEIGHT。
        shade: 背景の暗さ（0.0〜0.8）。
        token: 背景と差し込み方を表す識別子。変わると位置は中央へ戻る。
    """
    bg_w, bg_h = bg_size or (CANVAS_WIDTH, CANVAS_HEIGHT)
    value = _component(
        text=text_layer_url,
        bg=bg_url,
        bg_w=bg_w,
        bg_h=bg_h,
        fit=fit,
        shade=shade,
        token=token,
        canvas_w=CANVAS_WIDTH,
        canvas_h=CANVAS_HEIGHT,
        canvas_color="#%02X%02X%02X" % LETTERBOX_COLOR,
        key=key,
        default=None,
    )
    # 別の背景・別の差し込み方のときに返ってきた古い値は使わない。
    if not value or value.get("token") != token:
        return (0, 0)
    return (value.get("x", 0), value.get("y", 0))


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
