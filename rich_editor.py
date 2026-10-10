"""
ストーリーズ・カルーセルの文章を編集する、文書編集欄（書式つき）。

Streamlit標準のテキスト欄では、行ごとの寄せや一部の文字だけのサイズ変更が
できないため、rich_editor_component/index.html を双方向コンポーネントとして
読み込む。中身はブラウザ標準の編集欄（contenteditable）なので、文字の追加・
削除・スペースの挿入はそのまま入力でき、スマホではキーボードが開く。

編集内容は rich_text.py の文書形式でPython側へ返り、画像はこれまで通り
Python側（Pillow）で描画する。編集欄の見た目は編集しやすい大きさに
してあり、画像上の実際の折り返し位置はプレビューで確認する。
"""

import os

import streamlit as st
import streamlit.components.v1 as components

from rich_text import normalize_doc

_component = components.declare_component(
    "rich_editor",
    path=os.path.join(os.path.dirname(__file__), "rich_editor_component"),
)


def accept_editor_value(value, rev, allow_pagebreak=False):
    """
    編集欄から返ってきた値を文書として受け取る。使えない場合はNoneを返す。

    コンポーネントの値は再実行をまたいで残るため、Python側で文書を作り直した
    （revを進めた）後に残っている古い値は、revが違うことで見分けて捨てる。
    """
    if not isinstance(value, dict) or value.get("rev") != rev:
        return None
    return normalize_doc(value.get("doc"), allow_pagebreak=allow_pagebreak)


def editor_value(key, rev, allow_pagebreak=False):
    """
    編集欄を描画するより前に、その最新の編集内容を読み取る
    （編集欄を画面の下の方に置きつつ、上の処理で内容を使いたい場合用）。
    """
    return accept_editor_value(st.session_state.get(key), rev, allow_pagebreak)


def rich_editor(doc, rev, base_size, key, allow_pagebreak=False):
    """
    編集欄を表示し、現在の文書を返す。

    引数:
        doc: 表示する文書。編集欄は初回と、revが変わったときだけこれを読み込む
             （入力中の内容を、再実行のたびに上書きしてしまわないため）。
        rev: Python側で文書を作り直すたびに進める番号。
        base_size: 標準の文字サイズ（部分指定のサイズを相対的な大きさで表示するため）。
        allow_pagebreak: 「改ページ」ボタンを出すかどうか。
    """
    value = _component(
        doc=doc,
        rev=rev,
        base_size=base_size,
        allow_pagebreak=allow_pagebreak,
        key=key,
        default=None,
    )
    accepted = accept_editor_value(value, rev, allow_pagebreak)
    return doc if accepted is None else accepted
