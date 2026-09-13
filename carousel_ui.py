"""
カルーセル編集UI（横スクロールカード・ドラッグ操作・キーボードショートカット）向けの
ごく小さなCSS / JavaScriptをまとめたモジュール。

Streamlit標準の部品だけでは実現しにくい、以下の見た目・操作だけを
必要最小限のHTML / JavaScriptで補っている。

- 選択中カードの枠線ハイライト（CSS）
- カルーセルカード列を、マウスのクリック＋ドラッグで横方向にパンする操作（JS）
- Ctrl+Z / Ctrl+Y（Macは Cmd+Z / Cmd+Shift+Z）による元に戻す・やり直す（JS）

大規模なフロントエンドフレームワークや新しいReactアプリは追加していない。
横スクロール自体はStreamlit標準の `st.container(horizontal=True, wrap=False)`
が提供するネイティブの挙動（マウスホイール・トラックパッド・タッチスワイプに対応）を
そのまま利用しており、JavaScriptで再実装していない。
"""

import json

import streamlit as st
import streamlit.components.v1 as components

# カルーセルカード1枚あたりの表示幅（px）。
# PC画面（.block-container の最大幅720px程度）で3〜5枚程度が同時に見える大きさ。
CAROUSEL_CARD_WIDTH = 150

# キーボードショートカットから探すボタンのラベル文字列。
# streamlit_app.py側のボタンラベルと必ず一致させること。
UNDO_BUTTON_LABEL = "↶ 元に戻す"
REDO_BUTTON_LABEL = "↷ やり直す"


def inject_carousel_card_css(selected_page_id):
    """
    カルーセルカード列の見た目（カードの枠・選択中カードのハイライト）を適用する。

    st.container(key=...)が自動的に付与する `st-key-<key>` クラスを利用しており、
    既存のカフェ風UI（styles.py）の配色変数（--tt-accent等）をそのまま使う。
    派手な演出は避け、枠線と背景色だけで選択状態を示す。
    """
    selected_class = f"st-key-carousel_card_{selected_page_id}" if selected_page_id else ""
    st.markdown(
        f"""
        <style>
        /* カード列本体：横スクロール時にドラッグ操作していることが分かるカーソルにする */
        .st-key-carousel_thumb_row {{
            padding-bottom: 0.6rem;
            cursor: grab;
        }}
        .st-key-carousel_thumb_row:active {{
            cursor: grabbing;
        }}
        /* すべてのカードに共通の枠 */
        [class*="st-key-carousel_card_"] {{
            border: 2px solid var(--tt-border) !important;
            border-radius: 12px !important;
            background-color: var(--tt-card-bg) !important;
        }}
        /* 選択中のカードだけアクセントカラーでハイライト（後勝ちで上の規則を上書き） */
        {f'.{selected_class} {{ border: 2px solid var(--tt-accent) !important; background-color: var(--tt-bg-soft) !important; }}' if selected_class else ''}
        </style>
        """,
        unsafe_allow_html=True,
    )


def inject_carousel_shortcuts_js():
    """
    以下2つの操作を実現する、ごく小さなJavaScriptを埋め込む。

    1. カルーセルカード列（.st-key-carousel_thumb_row）を
       クリック＋ドラッグで横方向にパンできるようにする。
    2. Ctrl+Z / Ctrl+Y（Mac: Cmd+Z / Cmd+Shift+Z）を検知し、
       画面上の「↶ 元に戻す」「↷ やり直す」ボタンをクリックする。
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
