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

import uuid

import cloudinary
import cloudinary.uploader
import streamlit as st

UPLOAD_FOLDER = "threads-transformer/stories"


class CloudinaryError(Exception):
    """Cloudinaryへのアップロード・削除中に発生したエラー。"""

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
            overwrite=False,
        )
    except Exception as e:
        raise CloudinaryError(
            "投稿用画像の準備に失敗しました。もう一度お試しください。",
            detail=str(e),
        )

    url = result.get("secure_url")
    if not url:
        raise CloudinaryError(
            "投稿用画像の準備に失敗しました。もう一度お試しください。",
            detail="Cloudinaryの応答にsecure_urlが含まれていません。",
        )

    return url, result.get("public_id", public_id)


def delete_story_image(public_id: str) -> bool:
    """
    アップロード済みの一時画像をCloudinaryから削除する。

    Instagramへの投稿が既に成功した後の後片付けとして呼ぶことを想定しており、
    ここで失敗しても例外は送出せず、成功したかどうかをbool値で返すだけにする。
    """
    try:
        _configure()
        cloudinary.uploader.destroy(public_id, resource_type="image")
        return True
    except Exception:
        return False
