"""
Instagram API（Instagram Login方式）を使って、
Story画像を投稿するための処理をまとめたモジュール。

Instagramのアクセストークン・ユーザーIDは、
既存の [threads_accounts] とは別に、Streamlit Secretsの
[instagram_accounts.<アカウントキー>] から読み込む。
[threads_accounts] の構造は変更していない。

例:
    [instagram_accounts.shin_coaching]
    access_token = "..."
    ig_user_id = "..."

APIバージョンはこのファイル内の GRAPH_API_VERSION 定数だけで管理し、
他の場所には直接書かない。
"""

import time

import requests
import streamlit as st

from threads_api import username_to_secret_key

GRAPH_API_VERSION = "v21.0"
API_BASE = f"https://graph.instagram.com/{GRAPH_API_VERSION}"

# メディアコンテナの処理完了を待つ設定（無限ループにしないための上限つき）
STATUS_POLL_INTERVAL_SECONDS = 3
STATUS_MAX_WAIT_SECONDS = 60

REQUEST_TIMEOUT_SECONDS = 30


class InstagramAPIError(Exception):
    """Instagram API呼び出し中に発生したエラー。"""

    def __init__(self, friendly_message, detail=None):
        super().__init__(friendly_message)
        self.friendly_message = friendly_message
        self.detail = detail


def get_instagram_credentials(username: str):
    """
    Threadsアカウント名（画面で選択した表示名）に対応する
    Instagramのアクセストークン・ユーザーIDをSecretsから取得する。

    設定が無い、または不完全な場合はNoneを返す
    （access_token・api_secretなどの値そのものはログや例外に含めない）。
    """
    key = username_to_secret_key(username)
    accounts = st.secrets.get("instagram_accounts", {})
    account = accounts.get(key)
    if not account:
        return None

    access_token = account.get("access_token")
    ig_user_id = account.get("ig_user_id")
    if not access_token or not ig_user_id:
        return None

    return {"access_token": access_token, "ig_user_id": ig_user_id}


def _status_to_friendly_message(status_code: int) -> str:
    if status_code in (401, 403):
        return "Instagramとの認証に失敗しました。管理者にお問い合わせください。"
    return "Instagramへの投稿に失敗しました。時間をおいて再度お試しください。"


def _record_trace(trace, step, response):
    """
    デバッグ用に、Instagram APIへのリクエスト結果（ステップ名・HTTPステータス・
    応答本文）を記録する。traceがNoneの場合は何もしない。

    アクセストークンなどのリクエストパラメータ自体は記録しない。
    """
    if trace is None:
        return
    trace.append(
        {
            "step": step,
            "status_code": response.status_code,
            "response_text": response.text,
        }
    )


def _parse_response(response):
    """
    レスポンスをJSONとして解釈する。エラー時はInstagramAPIErrorに変換する。

    デバッグ情報にはHTTPステータスコードと応答本文だけを含め、
    リクエストに使ったアクセストークンなどの機密情報は含めない。
    """
    if response.status_code >= 400:
        raise InstagramAPIError(
            _status_to_friendly_message(response.status_code),
            detail=f"HTTPステータスコード: {response.status_code} / 応答: {response.text}",
        )
    try:
        return response.json()
    except ValueError:
        raise InstagramAPIError(
            "Instagramへの投稿に失敗しました。時間をおいて再度お試しください。",
            detail=f"応答を解析できませんでした。応答本文: {response.text}",
        )


def _request(method, url, params, trace=None, step=""):
    try:
        response = requests.request(method, url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.exceptions.RequestException as e:
        raise InstagramAPIError(
            "Instagramへの投稿に失敗しました。時間をおいて再度お試しください。",
            detail=str(e),
        )
    _record_trace(trace, step, response)
    return _parse_response(response)


def create_story_container(
    ig_user_id: str, access_token: str, image_url: str, trace=None
) -> str:
    """
    公開URLの画像から、Story投稿用のメディアコンテナを作成し、
    コンテナID（creation_id）を返す。

    image_urlは前後の空白を取り除いてから使用する
    （余計な文字が連結されたまま送られることを防ぐため）。
    """
    image_url = (image_url or "").strip()

    url = f"{API_BASE}/{ig_user_id}/media"
    params = {
        "image_url": image_url,
        "media_type": "STORIES",
        "access_token": access_token,
    }
    data = _request("POST", url, params, trace=trace, step="メディアコンテナ作成 (media)")

    container_id = data.get("id")
    if not container_id:
        raise InstagramAPIError(
            "Instagramへの投稿に失敗しました。時間をおいて再度お試しください。",
            detail=f"コンテナ作成の応答にidが含まれていません: {data}",
        )
    return container_id


def wait_until_container_ready(container_id: str, access_token: str, trace=None) -> None:
    """
    メディアコンテナの処理状況（status_code）を確認し、
    FINISHEDになるまで待つ。

    IN_PROGRESSの間は数秒おきに再確認し、最大待機時間を超えたら
    タイムアウトとしてInstagramAPIErrorを送出する（無限ループにしない）。
    ERRORになった場合はその時点で送出する。
    """
    url = f"{API_BASE}/{container_id}"
    params = {"fields": "status_code", "access_token": access_token}

    waited_seconds = 0
    while True:
        data = _request("GET", url, params, trace=trace, step="コンテナ状態確認 (status_code)")
        status_code = data.get("status_code")

        if status_code == "FINISHED":
            return
        if status_code == "ERROR":
            raise InstagramAPIError(
                "Instagramへの投稿に失敗しました。時間をおいて再度お試しください。",
                detail=f"メディアコンテナの処理でエラーが発生しました: {data}",
            )

        if waited_seconds >= STATUS_MAX_WAIT_SECONDS:
            raise InstagramAPIError(
                "Instagramへの投稿に失敗しました。時間をおいて再度お試しください。",
                detail=(
                    f"メディアコンテナの処理が{STATUS_MAX_WAIT_SECONDS}秒以内に"
                    f"完了しませんでした（最終状態: {status_code}）。"
                ),
            )

        time.sleep(STATUS_POLL_INTERVAL_SECONDS)
        waited_seconds += STATUS_POLL_INTERVAL_SECONDS


def publish_story(ig_user_id: str, access_token: str, container_id: str, trace=None) -> str:
    """
    処理が完了したメディアコンテナをStoryとして公開し、公開後のメディアIDを返す。
    """
    url = f"{API_BASE}/{ig_user_id}/media_publish"
    params = {
        "creation_id": container_id,
        "access_token": access_token,
    }
    data = _request("POST", url, params, trace=trace, step="公開 (media_publish)")
    return data.get("id")


def post_story(ig_user_id: str, access_token: str, image_url: str, trace=None) -> str:
    """
    画像の公開URLから、Instagramストーリーズへの投稿を最後まで行う。

    1. メディアコンテナを作成
    2. 処理が完了する（FINISHED）まで待つ
    3. Storyとして公開する

    traceにリストを渡すと、各ステップのHTTPステータスコードと応答本文を
    （アクセストークンなどを含めずに）追記していく。デバッグ表示用。

    戻り値: 公開されたメディアのID
    """
    container_id = create_story_container(ig_user_id, access_token, image_url, trace=trace)
    wait_until_container_ready(container_id, access_token, trace=trace)
    return publish_story(ig_user_id, access_token, container_id, trace=trace)
