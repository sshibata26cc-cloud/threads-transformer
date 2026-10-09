"""
ストーリーズの文章を、改行・改ページで複数枚に分けるための編集処理。

ページは {"id": 内部ID, "text": 本文} のリストで持つ。本文中の "\n" が改行。
プレビュー上のカーソル位置（本文の何文字目の前か）を受け取り、そこへ改行を
入れる・そこでページを2つに分ける、という操作だけを行う（文章そのものは
書き換えない）。Streamlitには依存しない純粋な処理にしてある。
"""

import uuid

ACTION_NEWLINE = "newline"
ACTION_PAGEBREAK = "pagebreak"


def _new_page(text: str) -> dict:
    return {"id": uuid.uuid4().hex, "text": text}


def initial_pages(original_text, reply_texts):
    """変換直後の状態（本文と返信を空行でつないだ1ページ）を作る。"""
    blocks = [original_text or ""] + [r or "" for r in reply_texts]
    return [_new_page("\n\n".join(blocks))]


def apply_text_action(pages, page_index, action_type, char_index):
    """
    page_index番目のページの、char_index文字目の前に対して操作を行う。

    戻り値: (新しいページのリスト, 操作後に表示するページ番号)。
    何も変わらない操作（ページの先頭・末尾での改ページなど）の場合はNone。
    渡されたpagesは変更しない（元に戻す用にそのまま履歴へ残せるようにするため）。
    """
    if not 0 <= page_index < len(pages):
        return None
    page = pages[page_index]
    text = page["text"]
    if not isinstance(char_index, int) or not 0 <= char_index <= len(text):
        return None

    if action_type == ACTION_NEWLINE:
        updated = {"id": page["id"], "text": text[:char_index] + "\n" + text[char_index:]}
        return pages[:page_index] + [updated] + pages[page_index + 1 :], page_index

    if action_type == ACTION_PAGEBREAK:
        # 分けた境目に残る改行は、前のページの末尾・次のページの先頭の
        # 余分な空行になるだけなので取り除く。
        before = text[:char_index].rstrip("\n")
        after = text[char_index:].lstrip("\n")
        if not before.strip() or not after.strip():
            return None
        return (
            pages[:page_index]
            + [{"id": page["id"], "text": before}, _new_page(after)]
            + pages[page_index + 1 :],
            page_index + 1,
        )

    return None
