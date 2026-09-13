"""
Threadsの「同一アカウント本人の返信コメント」に対する、共通の除外ルールを
まとめたモジュール。

Instagram ストーリーズ投稿用・Instagram カルーセル投稿用・note投稿用の
3機能すべてが、本人返信を取り込む前にこのモジュールでのフィルタリングを
1回だけ通す（呼び出し側はstreamlit_app.pyの1箇所のみ）。

判定の対象は元投稿本文ではなく、本人返信コメント1件ごとの本文である。
元投稿本文や、「本人かどうか」の判定（threads_api.get_own_replies_in_order）
には一切手を加えない。

将来、除外条件を追加したい場合は、判定関数を1つ書いて
_EXCLUSION_RULES に追加するだけでよい。
"""

# 除外条件1: この文字列が本文のどこかに含まれていたら除外する。
_ANNOUNCEMENT_MARKER = "【ここからは告知です】"

# 除外条件2: Google Formへのリンクとみなす文字列（小文字で判定）。
_GOOGLE_FORM_MARKERS = (
    "google.com/form",
    "docs.google.com/forms",
    "forms.gle",
)

# 除外条件3: YouTubeへのリンクとみなす文字列（小文字で判定）。
_YOUTUBE_MARKERS = (
    "youtube.com",
    "youtu.be",
)


def _contains_marker(text: str, markers) -> bool:
    """textの中に、markersのいずれかが（大文字・小文字を区別せず）含まれるか。"""
    lowered = text.lower()
    return any(marker in lowered for marker in markers)


def _has_announcement_marker(text: str) -> bool:
    """除外条件1: 「【ここからは告知です】」が文章中のどこかに含まれる。"""
    return _ANNOUNCEMENT_MARKER in text


def _has_google_form_link(text: str) -> bool:
    """除外条件2: Google Formへのリンクが含まれる。"""
    return _contains_marker(text, _GOOGLE_FORM_MARKERS)


def _has_youtube_link(text: str) -> bool:
    """除外条件3: YouTubeへのリンクが含まれる。"""
    return _contains_marker(text, _YOUTUBE_MARKERS)


# 除外ルール一覧。1つでも該当すればそのコメント全体を除外する。
# 新しい除外条件を追加する場合は、判定関数を書いてここに加えるだけでよい。
_EXCLUSION_RULES = (
    _has_announcement_marker,
    _has_google_form_link,
    _has_youtube_link,
)


def should_exclude_reply(text: str) -> bool:
    """
    本人返信コメント1件の本文を受け取り、除外ルールのいずれかに
    該当するかどうかを判定する。

    該当部分だけを取り除くのではなく、コメント全体を「採用」か「除外」かの
    どちらかに判定するための関数。該当すればTrueを返す。
    """
    text = text or ""
    return any(rule(text) for rule in _EXCLUSION_RULES)


def filter_own_replies(own_replies: list) -> list:
    """
    threads_api.get_own_replies_in_order()が返す、時系列順の本人返信一覧から、
    除外ルールに該当するコメントを取り除く。

    リストをそのままフィルタするだけなので、除外後も残ったコメントの
    時系列順（元投稿 → 本人返信1 → 本人返信2 → ...）は変化しない。
    """
    return [reply for reply in own_replies if not should_exclude_reply(reply.get("text", ""))]
