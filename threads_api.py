"""
Threads API とのやり取りをまとめたモジュール。

このファイルには、
- ThreadsのURLからアカウント名と投稿IDを取り出す処理
- Streamlit Secretsからアクセストークンを取り出す処理
- Threads APIを呼び出して投稿情報・返信一覧を取得する処理
をまとめています。

アクセストークンはコード内に書かず、必ず Streamlit Secrets
（st.secrets）経由で読み込みます。
"""

import re
from typing import Optional

import requests
import streamlit as st

API_BASE = "https://graph.threads.net/v1.0"

# Threads APIから取得するフィールド一覧
POST_LIST_FIELDS = "id,text,timestamp,permalink,username,shortcode"
POST_DETAIL_FIELDS = "id,text,timestamp,permalink,username,shortcode"
REPLY_FIELDS = "id,text,timestamp,permalink,username,is_reply"
PROFILE_FIELDS = "id,username,threads_profile_picture_url"

# Threads投稿一覧を何ページまで遡って探すか（投稿が多いアカウント向けの上限）
MAX_SEARCH_PAGES = 10


class ThreadsAPIError(Exception):
    """Threads API呼び出し中に発生したエラーをまとめて扱うための例外。"""

    def __init__(self, friendly_message, status_code=None, response_text=None):
        super().__init__(friendly_message)
        self.friendly_message = friendly_message
        self.status_code = status_code
        self.response_text = response_text


def extract_username_and_shortcode(url: str):
    """
    ThreadsのURLから (アカウント名, 投稿ID) を取り出す。

    例:
        https://www.threads.net/@shin.coaching/post/Cxxxxxxxx
        -> ("shin.coaching", "Cxxxxxxxx")

    URLの形式が一致しない場合は (None, None) を返す。
    """
    if not url:
        return None, None

    pattern = r"threads\.(?:net|com)/@([^/?#]+)/post/([^/?#]+)"
    match = re.search(pattern, url.strip())
    if not match:
        return None, None

    username, shortcode = match.group(1), match.group(2)
    return username, shortcode


def username_to_secret_key(username: str) -> str:
    """アカウント名をSecretsのキー形式に変換する（ドット→アンダースコア）。"""
    return username.replace(".", "_")


def get_access_token(username: str):
    """
    アカウント名に対応するアクセストークンをStreamlit Secretsから取得する。
    登録されていない場合はNoneを返す。
    """
    key = username_to_secret_key(username)
    accounts = st.secrets.get("threads_accounts", {})
    return accounts.get(key)


def _status_to_friendly_message(status_code: int) -> str:
    """HTTPステータスコードを、初心者にも分かる日本語メッセージに変換する。"""
    if status_code in (401, 403):
        return (
            "アクセストークンが無効か、権限が不足しています。"
            "Streamlit Secretsに設定したトークンを確認してください。"
        )
    if status_code == 404:
        return "指定された投稿またはアカウントが見つかりませんでした。"
    if status_code == 429:
        return "Threads APIの利用制限に達しました。しばらく時間をおいてから再度お試しください。"
    if status_code and status_code >= 500:
        return "Threads側で一時的なエラーが発生しています。しばらくしてから再度お試しください。"
    return "Threads APIの呼び出し中にエラーが発生しました。"


def _request(url: str, params: Optional[dict] = None) -> dict:
    """GETリクエストを送り、エラー時はThreadsAPIErrorに変換する。"""
    try:
        response = requests.get(url, params=params, timeout=15)
    except requests.exceptions.RequestException as e:
        raise ThreadsAPIError(
            "Threads APIとの通信に失敗しました。インターネット接続を確認してください。",
            status_code=None,
            response_text=str(e),
        )

    if response.status_code >= 400:
        raise ThreadsAPIError(
            _status_to_friendly_message(response.status_code),
            status_code=response.status_code,
            response_text=response.text,
        )

    try:
        return response.json()
    except ValueError:
        raise ThreadsAPIError(
            "Threads APIからの応答を正しく読み取れませんでした。",
            status_code=response.status_code,
            response_text=response.text,
        )


def get_profile(access_token: str) -> dict:
    """アクセストークンに紐づくアカウントのプロフィール情報を取得する。"""
    url = f"{API_BASE}/me"
    params = {"fields": PROFILE_FIELDS, "access_token": access_token}
    return _request(url, params=params)


def _iter_own_posts(access_token: str):
    """アクセストークンに紐づくアカウントの投稿一覧を、ページを辿りながら順に返す。"""
    url = f"{API_BASE}/me/threads"
    params = {"fields": POST_LIST_FIELDS, "limit": 25, "access_token": access_token}

    pages_fetched = 0
    while url and pages_fetched < MAX_SEARCH_PAGES:
        data = _request(url, params=params)
        for post in data.get("data", []):
            yield post

        url = data.get("paging", {}).get("next")
        params = None  # next のURLには必要なパラメータが既に含まれている
        pages_fetched += 1


def find_post_by_shortcode(access_token: str, shortcode: str):
    """
    投稿一覧の中から、URLに含まれる投稿IDに一致する投稿を探す。
    見つからない場合はNoneを返す。
    """
    if not shortcode:
        return None

    for post in _iter_own_posts(access_token):
        if post.get("shortcode") == shortcode or shortcode in (post.get("permalink") or ""):
            return post
    return None


def get_post_detail(post_id: str, access_token: str) -> dict:
    """投稿IDから、投稿の詳細情報を取得する。"""
    url = f"{API_BASE}/{post_id}"
    params = {"fields": POST_DETAIL_FIELDS, "access_token": access_token}
    return _request(url, params=params)


def get_post_replies(post_id: str, access_token: str) -> list:
    """投稿IDから、その投稿に対する返信一覧を取得する。"""
    url = f"{API_BASE}/{post_id}/replies"
    params = {"fields": REPLY_FIELDS, "access_token": access_token}
    data = _request(url, params=params)
    return data.get("data", [])


def get_own_replies_in_order(replies: list, original_username: str) -> list:
    """
    返信一覧の中から、元投稿と同じユーザー名（本人）の返信だけを抽出し、
    投稿日時の昇順（時系列順）に並べ替えて返す。
    """
    own_replies = [r for r in replies if r.get("username") == original_username]
    own_replies.sort(key=lambda r: r.get("timestamp") or "")
    return own_replies
