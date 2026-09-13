"""
カルーセル編集UI（横スクロールの「コンベア」プレビュー・キーボードショートカット）
向けの、ごく小さなCSS / JavaScriptをまとめたモジュール。

Streamlit標準の部品だけでは実現しにくい、以下の見た目・操作だけを
必要最小限のHTML / JavaScriptで補っている。

- 選択中（中央）のページを大きく、前後のページを段階的に小さく見せる表示
  （CSSの width / transform / opacity / box-shadow のみ。3D的な変形はしない）
- カルーセルカード列を、マウスのクリック＋ドラッグで横方向にパンする操作（JS）
- 選択中ページが変わったときに、そのカードを横スクロール領域の中央へ
  自動的にスクロールする操作（JS。scroll-snapと組み合わせて使う）
- Ctrl+Z / Ctrl+Y（Macは Cmd+Z / Cmd+Shift+Z）による元に戻す・やり直す（JS）

大規模なフロントエンドフレームワークや新しいReactアプリ、外部の並び替え
コンポーネントは使用していない。ページの並び替えは「前に移動」「後ろに移動」
ボタン（streamlit_app.py側）で行い、ドラッグ＆ドロップでの並び替えは廃止した。

横スクロール自体はStreamlit標準の `st.container(horizontal=True, wrap=False)`
が提供するネイティブの挙動（マウスホイール・トラックパッド・タッチスワイプに対応）を
そのまま利用しており、JavaScriptで再実装していない。
"""

import json

import streamlit as st
import streamlit.components.v1 as components

# 選択中ページの前後、何ページ分までを横スクロール領域に表示するか。
# 大量のページがあっても、毎回この範囲分だけ画像を生成すればよいため、
# 無駄な再計算を避けられる（性能面の配慮）。
COVERFLOW_RADIUS = 3

# キーボードショートカットから探すボタンのラベル文字列。
# streamlit_app.py側のボタンラベルと必ず一致させること。
UNDO_BUTTON_LABEL = "↶ 元に戻す"
REDO_BUTTON_LABEL = "やり直す ↷"

# 各段階（中央 / 隣接 / それ以遠）のカード幅の目安。
# 中央: 画面幅の約25〜35%程度、隣接: 中央の約80%、それ以遠: 中央の約65%。
# clamp(最小, 画面幅に対する割合, 最大) で、極端に狭い/広い画面でも破綻しない
# ようにしている。スマートフォン（幅600px以下）ではさらに別の値を使う。
_WIDTH_CENTER = "clamp(190px, 30vw, 320px)"
_WIDTH_NEAR = "clamp(150px, 24vw, 256px)"
_WIDTH_FAR = "clamp(120px, 19vw, 210px)"

_WIDTH_CENTER_MOBILE = "74vw"
_WIDTH_NEAR_MOBILE = "58vw"
_WIDTH_FAR_MOBILE = "46vw"


def inject_carousel_card_css(distance_by_page_id: dict):
    """
    横スクロール行に並ぶ各カードを、選択中ページ（中央）からの距離に応じて
    「小 → 中 → 大（中央） → 中 → 小」という段階的なサイズ・立体感で表示する。

    distance_by_page_id: {ページID: 選択中ページからの距離}の辞書
        （距離0が選択中＝中央のページ）。今回横スクロール領域に実際に
        表示しているページの分だけ渡せばよい。

    st.container(key=...)が自動的に付与する `st-key-<key>` クラスを使って、
    表示中の各カードだけにピンポイントでスタイルを当てる。
    """
    tier_styles = {
        0: {
            "width": _WIDTH_CENTER,
            "width_mobile": _WIDTH_CENTER_MOBILE,
            "border": "2px solid var(--tt-accent)",
            "background": "var(--tt-bg-soft)",
            "shadow": "0 14px 28px rgba(59, 46, 39, 0.22)",
            "transform": "translateY(-10px) scale(1.0)",
            "opacity": "1",
            "z_index": "3",
        },
        1: {
            "width": _WIDTH_NEAR,
            "width_mobile": _WIDTH_NEAR_MOBILE,
            "border": "2px solid var(--tt-border)",
            "background": "var(--tt-card-bg)",
            "shadow": "0 6px 14px rgba(59, 46, 39, 0.12)",
            "transform": "translateY(0) scale(0.94)",
            "opacity": "0.88",
            "z_index": "2",
        },
        2: {
            "width": _WIDTH_FAR,
            "width_mobile": _WIDTH_FAR_MOBILE,
            "border": "1px solid var(--tt-border)",
            "background": "var(--tt-card-bg)",
            "shadow": "none",
            "transform": "translateY(6px) scale(0.88)",
            "opacity": "0.65",
            "z_index": "1",
        },
    }

    rules = []
    mobile_rules = []
    for pid, distance in distance_by_page_id.items():
        style = tier_styles.get(min(distance, 2), tier_styles[2])
        cls = f"st-key-carousel_card_{pid}"
        rules.append(
            f".{cls} {{"
            f"width: {style['width']} !important;"
            f"border: {style['border']} !important;"
            f"background-color: {style['background']} !important;"
            f"box-shadow: {style['shadow']} !important;"
            f"transform: {style['transform']} !important;"
            f"opacity: {style['opacity']} !important;"
            f"z-index: {style['z_index']};"
            f"transition: transform 0.25s ease, opacity 0.25s ease, box-shadow 0.25s ease, width 0.25s ease;"
            f"}}"
            # 画像自体の見た目のサイズも、念のためここで直接指定しておく
            # （st.containerの外側ラッパーの幅計算に関わらず、確実にこの幅で見えるように）。
            f".{cls} img {{ width: {style['width']} !important; height: auto !important; }}"
        )
        mobile_rules.append(
            f".{cls} {{ width: {style['width_mobile']} !important; }}"
            f".{cls} img {{ width: {style['width_mobile']} !important; }}"
        )

    st.markdown(
        f"""
        <style>
        .st-key-carousel_thumb_row {{
            padding: 1.2rem 0.4rem 1.6rem 0.4rem;
            cursor: grab;
            scroll-snap-type: x mandatory;
            align-items: center !important;
        }}
        .st-key-carousel_thumb_row:active {{
            cursor: grabbing;
        }}
        [class*="st-key-carousel_card_"] {{
            border-radius: 14px !important;
            scroll-snap-align: center;
            scroll-snap-stop: always;
        }}
        {"".join(rules)}
        @media (max-width: 600px) {{
            {"".join(mobile_rules)}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def inject_carousel_center_scroll_js(selected_page_id):
    """
    選択中ページが変わったときに、そのカードを横スクロール領域の中央へ
    自動的にスクロールする（scroll-snapと組み合わせて使う想定）。

    「一度だけ登録するイベントリスナー」ではなく「今回の描画1回だけ行いたい
    動作」なので、Ctrl+Z検知のような『親ページへの一度きりの<script>挿入』は
    不要で、iframe内から直接window.parent上で実行してよい。
    """
    if not selected_page_id:
        return

    target_selector = json.dumps(f".st-key-carousel_card_{selected_page_id}")
    html = f"""
    <script>
    (function () {{
        try {{
            var doc = window.parent.document;
            var target = {target_selector};
            function centerSelectedCard() {{
                var el = doc.querySelector(target);
                if (el && el.scrollIntoView) {{
                    el.scrollIntoView({{ behavior: 'smooth', inline: 'center', block: 'nearest' }});
                }}
            }}
            // 直前の描画がDOMへ反映されてから動かすため、少しだけ遅らせる。
            setTimeout(centerSelectedCard, 60);
        }} catch (err) {{
            // 親ページへアクセスできない環境では、何もせず静かに諦める。
        }}
    }})();
    </script>
    """
    components.html(html, height=0)


def inject_carousel_shortcuts_js():
    """
    以下2つの操作を実現する、ごく小さなJavaScriptを埋め込む。

    1. カルーセルカード列（.st-key-carousel_thumb_row）を
       クリック＋ドラッグで横方向にパンできるようにする。
    2. Ctrl+Z / Ctrl+Y（Mac: Cmd+Z / Cmd+Shift+Z）を検知し、
       画面上の「↶ 元に戻す」「やり直す ↷」ボタンをクリックする。
       テキスト入力欄にフォーカスがある間はブラウザ標準のUndoに任せ、
       二重にUndoが走らないようにする。

    StreamlitはHTMLコンポーネントを再実行のたびに新しいiframeを描画し、
    古いiframeは（DOMからは消えても）その場ですぐには破棄されないことがある。
    そのiframeの中から直接 window.parent.document にリスナーを登録すると、
    古いiframeが実際に破棄されたタイミングでリスナーが効かなくなることがあるため、
    実際のイベント処理コードは <script> 要素として親ページ自身に1回だけ挿入し、
    親ページ自身のJavaScriptとして実行させることで、iframeの生死に影響されないようにする。
    """
    inner_js = r"""
    (function () {
        function findButtonByText(text) {
            var buttons = document.querySelectorAll('button');
            for (var i = 0; i < buttons.length; i++) {
                if (buttons[i].textContent.trim() === text) {
                    return buttons[i];
                }
            }
            return null;
        }

        // --- Ctrl+Z / Ctrl+Y（Cmd+Z / Cmd+Shift+Z）---
        document.addEventListener('keydown', function (e) {
            var key = (e.key || '').toLowerCase();
            var isUndo = (e.ctrlKey || e.metaKey) && !e.shiftKey && key === 'z';
            var isRedoWin = e.ctrlKey && !e.shiftKey && key === 'y';
            var isRedoMac = e.metaKey && e.shiftKey && key === 'z';
            if (!isUndo && !isRedoWin && !isRedoMac) {
                return;
            }

            // テキスト入力欄にフォーカスがある間は、ブラウザ標準のUndoに任せる
            // （ここで独自Undoも走らせると二重Undoになってしまうため）。
            var active = document.activeElement;
            var tag = active ? active.tagName : '';
            if (tag === 'TEXTAREA' || tag === 'INPUT' || (active && active.isContentEditable)) {
                return;
            }

            // キーを押しっぱなしにしたときの自動リピートでは反応しない
            // （1回の操作で履歴が2段階以上進んでしまうのを防ぐ）。
            if (e.repeat) {
                return;
            }

            var target = findButtonByText(isUndo ? __UNDO_LABEL__ : __REDO_LABEL__);
            if (target && !target.disabled) {
                e.preventDefault();
                target.click();
            }
        }, true);

        // --- カルーセルカード列のドラッグ横スクロール ---
        var dragState = null;

        document.addEventListener('mousedown', function (e) {
            var row = e.target.closest('.st-key-carousel_thumb_row');
            if (!row) {
                return;
            }
            // ボタンなど、通常クリックさせたい要素の上ではドラッグ開始しない
            if (e.target.closest('button')) {
                return;
            }
            dragState = { row: row, startX: e.clientX, startScrollLeft: row.scrollLeft };
        });

        document.addEventListener('mousemove', function (e) {
            if (!dragState) {
                return;
            }
            var dx = e.clientX - dragState.startX;
            dragState.row.scrollLeft = dragState.startScrollLeft - dx;
        });

        function endDrag() {
            dragState = null;
        }
        document.addEventListener('mouseup', endDrag);
        document.addEventListener('mouseleave', endDrag);
    })();
    """
    inner_js = inner_js.replace("__UNDO_LABEL__", json.dumps(UNDO_BUTTON_LABEL))
    inner_js = inner_js.replace("__REDO_LABEL__", json.dumps(REDO_BUTTON_LABEL))

    outer_html = """
    <script>
    (function () {
        try {
            var doc = window.parent.document;
            if (doc.__ttCarouselJsInstalled) {
                return;
            }
            doc.__ttCarouselJsInstalled = true;

            var script = doc.createElement('script');
            script.textContent = %s;
            doc.head.appendChild(script);
        } catch (err) {
            // 親ページへアクセスできない環境では、何もせず静かに諦める。
        }
    })();
    </script>
    """ % json.dumps(inner_js)

    components.html(outer_html, height=0)
