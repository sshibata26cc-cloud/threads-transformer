"""
Story画像をInstagram APIから取得できる公開URLにするため、
Cloudinaryへ一時的にアップロード・削除する処理をまとめたモジュール。

Cloudinaryの認証情報はStreamlit Secretsの [cloudinary] から読み込み、
コード内には直接書かない。API Secretは画面やデバッグ情報にも表示しない。

例:
    [cloudinary]
    cloud_name = "..."
    api_key = "..."
    api_secret = "..."
"""

import io
import time
import uuid

import cloudinary
import cloudinary.uploader
import requests
import streamlit as st
from PIL import Image

UPLOAD_FOLDER = "threads-transformer/stories"

# アップロード直後はCloudinaryのCDNへの反映が一瞬遅れることがあるため、
# Instagram APIへ渡す前に、公開URLが実際に取得できるかを短時間だけリトライ確認する。
# 無限ループにはしない。
URL_VERIFY_MAX_ATTEMPTS = 5
URL_VERIFY_RETRY_DELAY_SECONDS = 2
URL_VERIFY_TIMEOUT_SECONDS = 15


class CloudinaryError(Exception):
    """Cloudinaryへのアップロード・削除・検証中に発生したエラー。"""

    def __init__(self, friendly_message, detail=None):
        super().__init__(friendly_message)
        self.friendly_message = friendly_message
        self.detail = detail


def _configure():
    config = st.secrets.get("cloudinary", {})
    cloud_name = config.get("cloud_name")
    api_key = config.get("api_key")
    api_secret = config.get("api_secret")

    if not cloud_name or not api_key or not api_secret:
        raise CloudinaryError(
            "投稿用画像の準備に失敗しました。もう一度お試しください。",
            detail=(
                "Streamlit Secretsに[cloudinary]の設定"
                "（cloud_name/api_key/api_secret）が見つかりません。"
            ),
        )

    cloudinary.config(
        cloud_name=cloud_name,
        api_key=api_key,
        api_secret=api_secret,
        secure=True,
    )


def upload_story_image(jpeg_bytes: bytes):
    """
    JPEGのバイト列をCloudinaryへアップロードし、(公開URL, public_id) を返す。

    公開URLには、Cloudinaryの応答に含まれるsecure_urlだけを使う
    （urlフィールドや、自前で文字列を組み立てたURLは使わない）。
    余計な空白・改行などが混ざっていないかもここで確認する。

    public_idは、投稿完了後にdelete_story_image()で
    一時画像を削除する際に使用する。
    """
    _configure()

    public_id = f"{UPLOAD_FOLDER}/{uuid.uuid4().hex}"
    try:
        result = cloudinary.uploader.upload(
            jpeg_bytes,
            public_id=public_id,
            resource_type="image",
            format="jpg",
            overwrite=False,
        )
    except Exception as e:
        raise CloudinaryError(
            "投稿用画像の準備に失敗しました。もう一度お試しください。",
            detail=str(e),
        )

    url = (result.get("secure_url") or "").strip()
    if not url or any(ch.isspace() for ch in url):
        raise CloudinaryError(
            "投稿用画像の準備に失敗しました。もう一度お試しください。",
            detail=f"Cloudinaryのsecure_urlが不正です（値: {url!r}）。",
        )

    return url, result.get("public_id", public_id)


def verify_image_url(url: str) -> dict:
    """
    Cloudinaryへアップロードした画像が、外部から実際に取得できる
    正常なJPEGになっているかを確認する。Instagram APIへ渡す直前に呼ぶ。

    以下をすべて満たすまで、短い間隔で有限回数だけリトライする
    （CDNへの反映タイミングのずれを吸収するためで、無限ループにはしない）。
    - HTTP 200であること
    - Content-Typeがimage/jpegであること
    - レスポンス本文が空でないこと
    - 実際にPillowでJPEGとして開けること

    戻り値: {"status_code", "content_type", "byte_count"} を含むdict
    （成功時のデバッグ表示用）。

    確認に失敗した場合はCloudinaryErrorを送出する。
    """
    last_status_code = None
    last_content_type = None
    last_byte_count = 0
    last_detail = "不明なエラー"

    for attempt in range(1, URL_VERIFY_MAX_ATTEMPTS + 1):
        try:
            response = requests.get(url, timeout=URL_VERIFY_TIMEOUT_SECONDS)
        except requests.exceptions.RequestException as e:
            last_detail = f"通信エラー: {e}"
        else:
            last_status_code = response.status_code
            last_content_type = response.headers.get("Content-Type", "")
            content = response.content
            last_byte_count = len(content)

            if (
                response.status_code == 200
                and last_content_type.startswith("image/jpeg")
                and content
            ):
                try:
                    Image.open(io.BytesIO(content)).verify()
                except Exception as e:
                    last_detail = f"JPEGとして開けませんでした: {e}"
                else:
                    return {
                        "status_code": last_status_code,
                        "content_type": last_content_type,
                        "byte_count": last_byte_count,
                    }
            else:
                last_detail = (
                    f"ステータスコード: {last_status_code} / "
                    f"Content-Type: {last_content_type or '(なし)'} / "
                    f"バイト数: {last_byte_count}"
                )

        if attempt < URL_VERIFY_MAX_ATTEMPTS:
            time.sleep(URL_VERIFY_RETRY_DELAY_SECONDS)

    raise CloudinaryError(
        "投稿用画像の準備に失敗しました。もう一度お試しください。",
        detail=(
            f"公開URLの検証に{URL_VERIFY_MAX_ATTEMPTS}回試行しても成功しませんでした。"
            f"最終結果: {last_detail}"
        ),
    )


def delete_story_image(public_id: str) -> bool:
    """
    アップロード済みの一時画像をCloudinaryから削除する。

    Instagram側でのメディアコンテナ作成・公開処理がすべて成功した後の
    後片付けとしてのみ呼ぶこと。ここで失敗しても例外は送出せず、
    成功したかどうかをbool値で返すだけにする。
    """
    try:
        _configure()
        cloudinary.uploader.destroy(public_id, resource_type="image")
        return True
    except Exception:
        return False
