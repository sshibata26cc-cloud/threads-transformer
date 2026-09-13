"""
Threadsから取得した本文・本人返信を、Instagramカルーセル投稿用に
複数ページへ自動分割するモジュール。

分割の方針:
- 文章そのもの（文字・改行・順序）は一切変更しない。
- 「。」「！」「？」で終わる文単位、および改行・段落を
  分割の優先位置として扱い、文の途中・単語の途中では分割しない。
- ページ数は7ページ前後を目標にするが、文章量に応じて前後する。

Threads APIの呼び出しはこのモジュールには含まれない
（呼び出し側が取得済みのテキストを渡す）。
"""

import re

TARGET_PAGE_COUNT = 7

# 最終ページが極端に短くなった場合、直前のページへ統合する際のしきい値。
# 「目標文字数」に対するこの割合を下回る最終ページは、前のページへ結合する。
MIN_LAST_PAGE_RATIO = 0.25

# 文末の句点・感嘆符・疑問符（全角）。この直後で文を区切る。
_SENTENCE_END_RE = re.compile(r"(?<=[。！？])")
# 改行の連続（段落の区切りも含む）をひとかたまりのトークンとして扱う。
_NEWLINE_RUN_RE = re.compile(r"(\n+)")


def _split_units(text: str):
    """
    テキストを「文単位」または「改行の連続」のトークン列に分割する。

    すべてのトークンを順番通りに連結すると、元のテキストと完全に一致する。
    つまりこの関数自体は文章の内容を一切変更しない。
    """
    units = []
    for part in _NEWLINE_RUN_RE.split(text):
        if part == "":
            continue
        if part[0] == "\n":
            # 改行の連続はそのまま1トークンとして扱う（段落の区切りの手がかり）
            units.append(part)
            continue
        units.extend(s for s in _SENTENCE_END_RE.split(part) if s)
    return units


def _join_blocks(original_text: str, own_replies) -> str:
    """
    元投稿と本人返信を、取得時の順番のまま1つのテキストへ結合する。

    note投稿用テキストの結合方法（"\\n\\n".join(...)）と同じ考え方を使い、
    各投稿・返信の区切りを段落の区切りとして扱えるようにする。
    """
    blocks = [original_text or ""] + [reply or "" for reply in own_replies]
    return "\n\n".join(blocks)


def split_into_pages(original_text: str, own_replies, target_pages: int = TARGET_PAGE_COUNT):
    """
    元投稿本文と本人返信をあわせて、カルーセル用に約target_pagesページへ分割する。

    文章の途中・単語の途中では区切らず、「。」「！」「？」・改行・段落を
    優先的な区切り位置として使う。文章量が少ない・多い場合は、
    ページ数が目標値より前後することがある。

    戻り値: ページごとの文字列のリスト（1ページ以上）。
    """
    full_text = _join_blocks(original_text, own_replies)

    units = _split_units(full_text)
    if not units:
        return [""]

    total_len = sum(len(u) for u in units)
    if total_len == 0:
        return [""]

    target_pages = max(1, target_pages)
    target_len = max(1, round(total_len / target_pages))

    pages = []
    current = ""
    for unit in units:
        has_current_content = bool(current.strip("\n"))
        has_unit_content = bool(unit.strip("\n"))
        if has_current_content and len(current.strip("\n")) >= target_len and has_unit_content:
            pages.append(current)
            current = unit
        else:
            current += unit

    if current.strip("\n"):
        pages.append(current)
    elif not pages:
        pages.append(current)

    # 見た目を整えるため、各ページの先頭・末尾にある余分な改行だけを取り除く
    # （行の途中や文の内容には触れない）。
    pages = [p.strip("\n") for p in pages]
    pages = [p for p in pages if p != ""] or [""]

    # 最後のページが極端に短い場合は、不自然な1ページだけにならないよう
    # 直前のページへ統合する。
    if len(pages) > 1 and len(pages[-1]) < target_len * MIN_LAST_PAGE_RATIO:
        last = pages.pop()
        pages[-1] = pages[-1] + "\n\n" + last

    return pages
