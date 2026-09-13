"""
カルーセル編集UI（横スクロールの「コンベア」プレビュー・キーボードショートカット）
向けの、ごく小さなCSS / JavaScriptをまとめたモジュール。

Streamlit標準の部品だけでは実現しにくい、以下の見た目・操作だけを
必要最小限のHTML / JavaScriptで補っている。

- 選択中（中央）のページを少し大きく、前後のページを段階的に小さく見せる
  表示（CSSの transform: scale() / opacity / box-shadow のみ。3D的な変形はしない）
- カルーセルカード列を、マウスのクリック＋ドラッグで横方向にパンする操作（JS）
- プレビュー画像そのものをクリックしたら、そのページを選択する操作（JS）
- 横スクロール／スワイプが止まったとき、中央に最も近いカードを自動的に
  選択する操作（JS。スクロール中は毎フレーム送信せず、止まってから
  180ms後に1回だけ判定するdebounce方式）
- 選択中ページが変わったときに、そのカードを横スクロール領域の中央へ
  自動的にスクロールする操作（JS。scroll-snapと組み合わせて使う）
- Ctrl+Z / Ctrl+Y（Macは Cmd+Z / Cmd+Shift+Z）による元に戻す・やり直す（JS）

大規模なフロントエンドフレームワークや新しいReactアプリ、外部の並び替え
コンポーネントは使用していない。ページの並び替えは「前に移動」「後ろに移動」
ボタン（streamlit_app.py側）で行う。

横スクロール自体はStreamlit標準の `st.container(horizontal=True, wrap=False)`
が提供するネイティブの挙動（マウスホイール・トラックパッド・タッチスワイプに対応）を
そのまま利用しており、JavaScriptで再実装していない。

--------------------------------------------------------------------
JavaScript側からPython側（st.session_state）への同期の仕組み
--------------------------------------------------------------------
Streamlitには「JavaScriptの計算結果をそのままPythonへ返す」ための軽量な
標準APIが無いため、非表示のst.text_input（SCROLL_SYNC_KEY）を1つ用意し、
JavaScript側でその値を書き換えて疑似的なinputイベントを発火させることで、
Streamlitの再実行（rerun）とsession_stateへの反映を発生させている。
（Reactが管理する<input>のvalueは、素朴に`.value = ...`するだけでは
  Reactの内部状態に反映されないため、ネイティブのプロパティ記述子を
  経由してvalueを設定してからinputイベントを発火させる、という
  一般的な回避策を使っている。）

このウィジェット自体はCSSで完全に非表示にしており、ユーザーの目には
一切触れない。
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

# JavaScript側でスクロール／クリックによる選択結果を書き込む、
# 非表示の同期用ウィジェットのkey接頭辞（streamlit_app.py側でreset_idを付けて使う）。
SCROLL_SYNC_KEY_PREFIX = "carousel_scroll_sync_"

# カード1枚あたりの基準表示幅（レスポンシブ）。
# 実際の画像生成サイズ（1080x1350）は変えず、表示サイズだけをCSSで縮小する。
_BASE_WIDTH = "clamp(180px, 26vw, 300px)"
_BASE_WIDTH_MOBILE = "62vw"

# 選択中ページ（距離0）からの距離ごとの見た目の段階。
# scale値の目安: 中央1.0 → 隣接0.88 → それ以遠0.78。
_TIER_STYLES = {
    0: {
        "scale": 1.0,
        "opacity": 1.0,
        "shadow": "0 14px 28px rgba(59, 46, 39, 0.22)",
        "translate_y": "-8px",
        "border": "2px solid var(--tt-accent)",
        "background": "var(--tt-bg-soft)",
        "z_index": 3,
    },
    1: {
        "scale": 0.88,
        "opacity": 0.88,
        "shadow": "0 6px 14px rgba(59, 46, 39, 0.12)",
        "translate_y": "0px",
        "border": "2px solid var(--tt-border)",
        "background": "var(--tt-card-bg)",
        "z_index": 2,
    },
    2: {
        "scale": 0.78,
        "opacity": 0.72,
        "shadow": "none",
        "translate_y": "4px",
        "border": "1px solid var(--tt-border)",
        "background": "var(--tt-card-bg)",
        "z_index": 1,
    },
}


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
    rules = []
    for pid, distance in distance_by_page_id.items():
        style = _TIER_STYLES.get(min(distance, 2), _TIER_STYLES[2])
        cls = f"st-key-carousel_card_{pid}"
        rules.append(
            f".{cls} {{"
            f"border: {style['border']} !important;"
            f"background-color: {style['background']} !important;"
            f"box-shadow: {style['shadow']} !important;"
            f"transform: translateY({style['translate_y']}) scale({style['scale']}) !important;"
            f"opacity: {style['opacity']} !important;"
            f"z-index: {style['z_index']};"
            f"transition: transform 0.2s ease, opacity 0.2s ease, box-shadow 0.2s ease;"
            f"}}"
        )

    st.markdown(
        f"""
        <style>
        .st-key-carousel_thumb_row {{
            padding: 1.4rem 1rem 1.8rem 1rem;
            cursor: grab;
            scroll-snap-type: x mandatory;
            align-items: center !important;
            gap: 0.6rem !important;
        }}
        .st-key-carousel_thumb_row:active {{
            cursor: grabbing;
        }}
        [class*="st-key-carousel_card_"] {{
            border-radius: 14px !important;
            scroll-snap-align: center;
            scroll-snap-stop: always;
            cursor: pointer;
        }}
        [class*="st-key-carousel_card_"] img {{
            width: {_BASE_WIDTH} !important;
            height: auto !important;
        }}
        {"".join(rules)}
        @media (max-width: 600px) {{
            [class*="st-key-carousel_card_"] img {{ width: {_BASE_WIDTH_MOBILE} !important; }}
        }}
        /* スクロール／クリックの選択結果をPythonへ伝えるための、
           完全に非表示の同期ウィジェット。 */
        [class*="st-key-carousel_scroll_sync_"] {{
            display: none !important;
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

    ここで行うscrollIntoView()自体も、ブラウザ上ではれっきとした「scroll」
    イベントを発生させる。そのため、もしinject_carousel_shortcuts_js側の
    スクロール監視（中央に来たカードを自動選択する処理）がこれをそのまま
    「ユーザーによる新しいスクロール操作」として検知してしまうと、
    ・ボタン操作（追加／削除／元に戻す等）で選択が変わる
    ・ここで選択カードを中央へ自動スクロールする
    ・そのscrollイベントを検知し、（アニメーションの途中経過や誤差で）
      別のカードを「中央に最も近い」と誤判定して選択し直してしまう
    ・その選択変更でまた再描画→再度ここが呼ばれる→…
    というフィードバックループになり得る。加えて、この連鎖的な再実行の
    合間に本来のボタンクリックの処理が割り込まれ、正しく反映されない
    （＝Undo履歴が二重に積まれる、選択が意図せず巻き戻る等）ことがあった。
    これを防ぐため、scrollIntoView()を呼ぶ直前に「これはプログラムによる
    スクロールである」ことを示すタイムスタンプを親ウィンドウに記録し、
    スクロール監視側はこの直後の一定時間（アニメーションが収まるまでの
    十分な猶予）はスクロールを無視するようにしている。
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
                    // スムーズスクロールのアニメーション（+その後の余韻）が
                    // 収まるのに十分な時間、以降のscrollイベントを
                    // 「プログラムによるものかもしれない」として扱う。
                    window.parent.__ttCarouselProgrammaticScrollUntil = Date.now() + 900;
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


def inject_carousel_shortcuts_js(scroll_sync_key: str = None):
    """
    以下4つの操作を実現する、ごく小さなJavaScriptを埋め込む。

    1. Ctrl+Z / Ctrl+Y（Mac: Cmd+Z / Cmd+Shift+Z）を検知し、
       画面上の「↶ 元に戻す」「やり直す ↷」ボタンをクリックする。
       テキスト入力欄にフォーカスがある間はブラウザ標準のUndoに任せ、
       二重にUndoが走らないようにする。
    2. カルーセルカード列（.st-key-carousel_thumb_row）を
       クリック＋ドラッグで横方向にパンできるようにする
       （並び替えではなく、あくまで閲覧用のスクロール）。
    3. ドラッグを伴わない単純なクリックでは、クリックされたカードの
       ページを選択状態にする（プレビュー画像そのものをクリックして選ぶ）。
    4. 横スクロール／スワイプが止まったとき、カルーセル表示領域の中心に
       最も近いカードを自動的に選択状態にする（スクロール中に毎フレーム
       送信しないよう、止まってから180ms後に1回だけ判定する）。

    2〜4で選択されたページIDは、非表示のst.text_input（scroll_sync_key）の
    値を書き換えることでPython側（st.session_state）へ伝える。

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

        // --- カードID・選択結果をPython（非表示ウィジェット）へ伝える共通処理 ---
        // StreamlitのTextInputは、入力イベント（input）だけではPython側へ値を
        // 送信せず、Enterキー押下（またはフォーカスを外す操作）があって初めて
        // 実際の送信・再実行が行われる。そのため、値を書き換えた直後に
        // Enterキー相当のKeyboardEventも発火させて、送信を確定させる。
        function setNativeInputValue(el, value) {
            var proto = window.HTMLInputElement.prototype;
            var descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
            descriptor.set.call(el, value);
            el.dispatchEvent(new Event('input', { bubbles: true }));
            // Reactの状態更新（onChangeハンドラ）が反映されるのを1ティック待ってから
            // Enterキー押下を発火させる。同じ実行タイミングのまま連続で発火させると、
            // Enterキー側のハンドラがまだ更新前の値を参照してしまい、
            // Streamlit側へ実際の値が送信されないことがある。
            setTimeout(function () {
                var enterOpts = { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true };
                el.dispatchEvent(new KeyboardEvent('keydown', enterOpts));
                el.dispatchEvent(new KeyboardEvent('keyup', enterOpts));
            }, 0);
        }

        function reportSelectedPageId(pid) {
            if (!pid) {
                return;
            }
            var input = document.querySelector(__SYNC_INPUT_SELECTOR__);
            if (input && input.value !== pid) {
                setNativeInputValue(input, pid);
            }
        }

        function pageIdFromCard(card) {
            if (!card || !card.classList) {
                return null;
            }
            var found = null;
            card.classList.forEach(function (c) {
                if (c.indexOf('st-key-carousel_card_') === 0) {
                    found = c.slice('st-key-carousel_card_'.length);
                }
            });
            return found;
        }

        // --- カード列のクリック＋ドラッグによる横スクロール（並び替えではない） ---
        // ドラッグを伴わない単純なクリックは「そのカードを選択する」操作として扱う。
        var dragState = null;

        document.addEventListener('mousedown', function (e) {
            var row = e.target.closest('.st-key-carousel_thumb_row');
            if (!row) {
                return;
            }
            dragState = { row: row, startX: e.clientX, startScrollLeft: row.scrollLeft, moved: false };
        });

        document.addEventListener('mousemove', function (e) {
            if (!dragState) {
                return;
            }
            var dx = e.clientX - dragState.startX;
            if (Math.abs(dx) > 4) {
                dragState.moved = true;
            }
            dragState.row.scrollLeft = dragState.startScrollLeft - dx;
        });

        function endDrag(e) {
            if (dragState && !dragState.moved && e && e.target && e.target.closest) {
                var card = e.target.closest('[class*="st-key-carousel_card_"]');
                reportSelectedPageId(pageIdFromCard(card));
            }
            dragState = null;
        }
        document.addEventListener('mouseup', endDrag);
        document.addEventListener('mouseleave', function () {
            dragState = null;
        });

        // --- スクロール（スワイプ含む）が止まったら、中央に最も近いカードを自動選択 ---
        // スクロール中に毎フレーム送信しないよう、止まってから180ms後に1回だけ判定する
        // （debounce）。
        var scrollDebounceTimer = null;

        function reportCenterCard(row) {
            // inject_carousel_center_scroll_js()自身のscrollIntoView()が
            // 引き起こしたscrollである可能性がある間は、中央カードの再判定・
            // 再選択を行わない（フィードバックループ防止。詳細はcarousel_ui.pyの
            // inject_carousel_center_scroll_jsのコメントを参照）。
            var until = window.__ttCarouselProgrammaticScrollUntil || 0;
            if (Date.now() < until) {
                return;
            }
            var rowRect = row.getBoundingClientRect();
            var rowCenter = rowRect.left + rowRect.width / 2;
            var cards = row.querySelectorAll('[class*="st-key-carousel_card_"]');
            var bestPid = null;
            var bestDist = Infinity;
            cards.forEach(function (card) {
                var rect = card.getBoundingClientRect();
                var cardCenter = rect.left + rect.width / 2;
                var dist = Math.abs(cardCenter - rowCenter);
                if (dist < bestDist) {
                    bestDist = dist;
                    bestPid = pageIdFromCard(card);
                }
            });
            reportSelectedPageId(bestPid);
        }

        document.addEventListener('scroll', function (e) {
            var row = e.target;
            if (!row || !row.classList || !row.classList.contains('st-key-carousel_thumb_row')) {
                return;
            }
            if (scrollDebounceTimer) {
                clearTimeout(scrollDebounceTimer);
            }
            scrollDebounceTimer = setTimeout(function () {
                reportCenterCard(row);
            }, 180);
        }, true);
    })();
    """
    inner_js = inner_js.replace("__UNDO_LABEL__", json.dumps(UNDO_BUTTON_LABEL))
    inner_js = inner_js.replace("__REDO_LABEL__", json.dumps(REDO_BUTTON_LABEL))
    # scroll_sync_keyが渡されなかった場合（呼び出し側が更新される前の一時的な
    # 不整合など）でも、存在しない要素を安全に指すセレクタにしておく。
    # querySelectorはnullを返すだけなので、クリック／スクロール同期機能だけが
    # 静かに無効化され、Ctrl+Z/Ctrl+Yのショートカット機能はこの後も維持される。
    sync_selector = f".st-key-{scroll_sync_key} input" if scroll_sync_key else "[data-tt-carousel-sync-unavailable]"
    inner_js = inner_js.replace(
        "__SYNC_INPUT_SELECTOR__",
        json.dumps(sync_selector),
    )

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
