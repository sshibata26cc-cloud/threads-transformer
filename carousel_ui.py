"""
カルーセル編集UI（横スクロールのページ一覧・キーボードショートカット）向けの、
ごく小さなCSS / JavaScriptをまとめたモジュール。

Streamlit標準の部品だけでは実現しにくい、以下の見た目・操作だけを
必要最小限のHTML / JavaScriptで補っている。

- すべてのカードを同じサイズ・同じ見た目で表示し、選択中のカードだけを
  枠線・box-shadowのみで控えめに示す（拡大縮小・位置移動・opacityの変化は
  一切行わない）
- プレビュー画像（カード）をクリックしたら、そのページを選択する操作（JS）
- Ctrl+Z / Ctrl+Y（Macは Cmd+Z / Cmd+Shift+Z）による元に戻す・やり直す（JS）

横スクロールそのものは、Streamlit標準の`st.container(horizontal=True,
wrap=False)`が提供するネイティブの挙動（マウスホイール・トラックパッド・
タッチスワイプ・スクロールバーのドラッグに対応）をそのまま利用しており、
JavaScriptでの再実装や、scroll-snapによる強制的な吸着、スクロール位置に
応じた自動選択は一切行っていない。選択はカードのクリックのみで変わり、
選択が変わってもスクロール位置は変更しない。

大規模なフロントエンドフレームワークや新しいReactアプリ、外部の並び替え
コンポーネントは使用していない。ページの並び替えは「前に移動」「後ろに移動」
ボタン（streamlit_app.py側）で行う。

--------------------------------------------------------------------
JavaScript側からPython側（st.session_state）への同期の仕組み
--------------------------------------------------------------------
Streamlitには「JavaScriptの計算結果をそのままPythonへ返す」ための軽量な
標準APIが無いため、非表示のst.text_input（scroll_sync_key）を1つ用意し、
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

# キーボードショートカットから探すボタンのラベル文字列。
# streamlit_app.py側のボタンラベルと必ず一致させること。
UNDO_BUTTON_LABEL = "↶ 元に戻す"
REDO_BUTTON_LABEL = "やり直す ↷"

# カード1枚あたりの表示幅（レスポンシブ）。すべてのカードで共通・固定。
# 実際の画像生成サイズ（1080x1350）は変えず、表示サイズだけをCSSで統一する。
_CARD_WIDTH = "clamp(180px, 26vw, 300px)"
_CARD_WIDTH_MOBILE = "62vw"


def inject_carousel_card_css(selected_page_id):
    """
    横スクロール行に並ぶ各カードを、すべて同じサイズ・同じ見た目で表示する。

    選択中のカード（selected_page_id）だけ、枠線をアクセントカラーにし、
    ごく薄いbox-shadowを付けて区別する。枠線の太さ自体はすべてのカードで
    共通（2px）にしており、色とbox-shadowだけを変えることで、選択の有無に
    よってカードの実サイズ（bounding box）が変わらないようにしている。
    拡大・縮小・位置移動・opacityの変化は行わない。

    st.container(key=...)が自動的に付与する `st-key-<key>` クラスを使って、
    選択中カードだけにピンポイントでスタイルを当てる。
    """
    selected_rule = ""
    if selected_page_id:
        selected_rule = f"""
        .st-key-carousel_card_{selected_page_id} {{
            border-color: var(--tt-accent) !important;
            box-shadow: 0 2px 8px rgba(59, 46, 39, 0.12) !important;
        }}
        """

    st.markdown(
        f"""
        <style>
        .st-key-carousel_thumb_row {{
            padding: 1rem;
            overflow-x: auto;
            align-items: flex-start !important;
            gap: 0.6rem !important;
        }}
        [class*="st-key-carousel_card_"] {{
            border: 2px solid var(--tt-border);
            border-radius: 14px !important;
            background-color: var(--tt-card-bg);
            cursor: pointer;
            flex-shrink: 0;
        }}
        [class*="st-key-carousel_card_"] img {{
            width: {_CARD_WIDTH} !important;
            height: auto !important;
        }}
        {selected_rule}
        @media (max-width: 600px) {{
            [class*="st-key-carousel_card_"] img {{ width: {_CARD_WIDTH_MOBILE} !important; }}
        }}
        /* クリックによる選択結果をPythonへ伝えるための、完全に非表示の
           同期ウィジェット。 */
        [class*="st-key-carousel_scroll_sync_"] {{
            display: none !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def inject_carousel_scroll_restore_js():
    """
    直前の横スクロール位置（inject_carousel_shortcuts_js側の
    captureScrollIntent()が、カードや操作パネルのボタンをクリックした
    “その瞬間”にsessionStorageへ記録しておいた値）を、今回の再描画に
    合わせて復元する。

    Streamlitは再実行のたびにカルーセル領域のDOMを作り直すことがあり、
    その際にブラウザ側の横スクロール位置が一時的に0へ戻ってしまうことが
    ある。ページ選択（クリック）・ページの追加／削除／前後移動／元に戻す／
    やり直すのいずれの操作の後でも、ユーザーが見ていた横スクロール位置を
    できる限りそのまま維持するため、毎回の描画の最後にこの関数を呼ぶ。

    記録された値は1回使ったら必ず消す。消さずに残しておくと、今回の
    操作と無関係な次回以降の再実行（例: 本文の編集や、他のデザイン設定の
    変更）でも、古いスクロール位置へ意図せず引き戻されてしまうため。

    「一度だけ登録するイベントリスナー」ではなく「今回の描画1回だけ行いたい
    動作」なので、iframe内から直接window.parent上で実行してよい。
    """
    html = """
    <script>
    (function () {
        try {
            var saved = null;
            try {
                saved = sessionStorage.getItem('ttCarouselScrollIntent');
                sessionStorage.removeItem('ttCarouselScrollIntent');
            } catch (err) {
                return;
            }
            if (saved === null) {
                return;
            }
            var doc = window.parent.document;
            var target = parseInt(saved, 10) || 0;

            // Streamlitは再実行後のDOM更新を複数回に分けて反映することがあり、
            // その過程で横スクロール領域のDOMノード自体が作り直され、
            // scrollLeftが一時的に0へ戻ることがある。そのため一度だけ
            // 復元するのではなく、短い時間だけ繰り返し復元し続けることで、
            // 最終的に正しい位置へ収束させる。
            var attempts = 0;
            var timer = setInterval(function () {
                attempts += 1;
                var row = doc.querySelector('.st-key-carousel_thumb_row');
                if (row && row.scrollLeft !== target) {
                    row.scrollLeft = target;
                }
                if (attempts >= 20) {
                    clearInterval(timer);
                }
            }, 100);
        } catch (err) {
            // 親ページへアクセスできない環境では、何もせず静かに諦める。
        }
    })();
    </script>
    """
    components.html(html, height=0)


def inject_carousel_shortcuts_js(scroll_sync_key: str = None):
    """
    以下2つの操作を実現する、ごく小さなJavaScriptを埋め込む。

    1. Ctrl+Z / Ctrl+Y（Mac: Cmd+Z / Cmd+Shift+Z）を検知し、
       画面上の「↶ 元に戻す」「やり直す ↷」ボタンをクリックする。
       テキスト入力欄にフォーカスがある間はブラウザ標準のUndoに任せ、
       二重にUndoが走らないようにする。
    2. カード（プレビュー画像）をクリックしたら、そのページを選択状態にする。
       横スクロールだけでは選択は変わらない。

    2で選択されたページIDは、非表示のst.text_input（scroll_sync_key）の
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

        // --- カードクリックによる選択結果をPython（非表示ウィジェット）へ伝える ---
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

        // --- 横スクロール位置を「操作の直前」に記録する ---
        // Streamlitは再実行のたびにカルーセル領域のDOMを作り直すことがあり、
        // その際にブラウザ側の横スクロール位置（scrollLeft）が一時的に0へ
        // 戻ってしまうことがある。この「作り直し」自体もscrollイベントを
        // 発生させるため、スクロールイベントを継続的に監視して常時保存する
        // 方式だと、0に戻った瞬間の値でうっかり上書き保存してしまい、
        // 直後にinject_carousel_scroll_restore_js側で復元しようとしても
        // 「復元先の値そのものが既に0になっている」という状態になってしまう。
        // そのため、常時監視ではなく、「カードクリック」や「操作パネルの
        // ボタンクリック」など、再実行のきっかけになる操作が起きた
        // “その瞬間”のスクロール位置だけを記録する。
        function captureScrollIntent() {
            try {
                var row = document.querySelector('.st-key-carousel_thumb_row');
                if (row) {
                    sessionStorage.setItem('ttCarouselScrollIntent', String(row.scrollLeft));
                }
            } catch (err) {
                // sessionStorageが使えない環境では、位置の維持だけ諦める。
            }
        }

        // --- カード（プレビュー画像）のクリックだけで選択する ---
        // 横スクロール自体はブラウザ標準の挙動（overflow-x: auto）に任せて
        // おり、ここでは独自のドラッグ処理・スクロール監視は一切行わない。
        // そのため、ここでの「クリック」はドラッグと衝突する心配がなく、
        // 単純なclickイベントだけで選択判定できる。
        document.addEventListener('click', function (e) {
            var card = e.target.closest('[class*="st-key-carousel_card_"]');
            if (card) {
                captureScrollIntent();
                reportSelectedPageId(pageIdFromCard(card));
                return;
            }
            // ページ追加・削除・元に戻す・やり直す・前後移動ボタンも、
            // 押した直後にカルーセルの見た目（枚数・並び順・選択中カード）が
            // 変わり再実行されるため、同様に直前のスクロール位置を記録する。
            var actionButton = e.target.closest('.st-key-carousel_action_panel button');
            if (actionButton) {
                captureScrollIntent();
            }
        });
    })();
    """
    inner_js = inner_js.replace("__UNDO_LABEL__", json.dumps(UNDO_BUTTON_LABEL))
    inner_js = inner_js.replace("__REDO_LABEL__", json.dumps(REDO_BUTTON_LABEL))
    # scroll_sync_keyが渡されなかった場合（呼び出し側が更新される前の一時的な
    # 不整合など）でも、存在しない要素を安全に指すセレクタにしておく。
    # querySelectorはnullを返すだけなので、クリック同期機能だけが静かに
    # 無効化され、Ctrl+Z/Ctrl+Yのショートカット機能はこの後も維持される。
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
