import hashlib

import streamlit as st

from app_meta import inject_mobile_meta_tags, resolve_page_icon
from cloudinary_storage import (
    CloudinaryError,
    delete_story_image,
    upload_story_image,
    verify_image_url,
)
from instagram_api import InstagramAPIError, get_instagram_credentials, post_story
from story_image import (
    DEFAULT_MAX_FONT_SIZE,
    DEFAULT_TEXT_COLOR,
    StoryImageError,
    convert_png_to_jpeg,
    generate_story_image,
    load_background_image,
)
from styles import inject_custom_css
from threads_api import (
    ThreadsAPIError,
    find_post_by_permalink,
    get_access_token,
    get_own_replies_in_order,
    get_post_detail,
    get_post_replies,
    get_profile,
    is_threads_url,
    resolve_target_url,
)

MODE_INSTAGRAM = "Instagram ストーリーズ 投稿用"
MODE_NOTE = "note 投稿用"

ACCOUNT_CHOICES = ["shin.coaching", "takuma_o369", "masa_life128"]

st.set_page_config(
    page_title="Threads XC",
    page_icon=resolve_page_icon(),
    layout="centered",
)

inject_custom_css()
inject_mobile_meta_tags()

st.markdown(
    """
    <div class="tt-header">
        <div class="tt-brand">Threads Transformer</div>
        <div class="tt-tagline">Threadsの言葉を、次の場所へ。</div>
        <div class="tt-description">
            Threadsの投稿を、Instagramストーリーズ用画像または<br>
            note投稿用テキストに変換します。
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

if "result" not in st.session_state:
    st.session_state.result = None
if "story_image_bytes" not in st.session_state:
    st.session_state.story_image_bytes = None
if "story_warning" not in st.session_state:
    st.session_state.story_warning = None
if "design_reset_id" not in st.session_state:
    st.session_state.design_reset_id = 0
if "ig_post_confirm_pending" not in st.session_state:
    st.session_state.ig_post_confirm_pending = False
if "last_posted_image_hash" not in st.session_state:
    st.session_state.last_posted_image_hash = None
if "ig_post_success_message" not in st.session_state:
    st.session_state.ig_post_success_message = None
if "ig_post_error" not in st.session_state:
    st.session_state.ig_post_error = None
if "ig_post_debug_info" not in st.session_state:
    st.session_state.ig_post_debug_info = None

st.markdown('<div class="tt-step-title">1. Threadsアカウントを選択</div>', unsafe_allow_html=True)
with st.container(border=True):
    selected_account = st.radio(
        "変換に使うThreadsアカウントを選んでください",
        ACCOUNT_CHOICES,
        label_visibility="collapsed",
    )

st.markdown('<div class="tt-step-title">2. 変換先を選択</div>', unsafe_allow_html=True)
with st.container(border=True):
    mode = st.radio(
        "変換したい形式を選んでください",
        (MODE_INSTAGRAM, MODE_NOTE),
        label_visibility="collapsed",
    )

st.markdown('<div class="tt-step-title">3. Threads投稿の読み込み</div>', unsafe_allow_html=True)
with st.container(border=True):
    threads_url = st.text_input(
        "Threads投稿のURLを入力してください",
        placeholder="https://www.threads.com/@shin.coaching/post/xxxxx",
        label_visibility="collapsed",
    )

st.write("")
convert_clicked = st.button("変換する", use_container_width=True)

if convert_clicked:
    if not threads_url:
        st.warning("Threads投稿のURLを入力してください。")
    elif not is_threads_url(threads_url):
        st.error(
            "URLの形式が正しくありません。"
            "Threadsの投稿URL、または共有リンクを入力してください。"
        )
    else:
        access_token = get_access_token(selected_account)

        if not access_token:
            st.error("このアカウントは現在変換対象に登録されていません")
        else:
            try:
                with st.spinner("Threadsから投稿情報を取得しています..."):
                    resolved_url = resolve_target_url(threads_url)
                    post_summary = find_post_by_permalink(access_token, resolved_url)

                    post = None
                    profile = None
                    own_replies = []

                    if post_summary:
                        post = get_post_detail(post_summary["id"], access_token)
                        profile = get_profile(access_token)
                        replies = get_post_replies(post["id"], access_token)
                        own_replies = get_own_replies_in_order(
                            replies, post.get("username", selected_account)
                        )

                if not post_summary:
                    st.session_state.result = None
                    st.error(
                        "選択したアカウントの投稿として確認できませんでした。"
                        "アカウントの選択とThreadsリンクをご確認ください。"
                    )
                else:
                    st.success("Threadsから投稿情報を取得しました。")

                    account_name = post.get("username", selected_account)
                    profile_image_url = (profile or {}).get("threads_profile_picture_url")
                    original_text = post.get("text", "")
                    reply_texts = [r.get("text", "") for r in own_replies]

                    st.session_state.result = {
                        "mode": mode,
                        "account_label": selected_account,
                        "account_name": account_name,
                        "profile_image_url": profile_image_url,
                        "original_text": original_text,
                        "reply_texts": reply_texts,
                    }
                    st.session_state.design_reset_id += 1
                    st.session_state.ig_post_confirm_pending = False

                    if mode == MODE_INSTAGRAM:
                        try:
                            image_bytes, warning = generate_story_image(
                                profile_image_url=profile_image_url,
                                account_name=account_name,
                                original_text=original_text,
                                own_replies=reply_texts,
                            )
                        except StoryImageError as e:
                            st.session_state.story_image_bytes = None
                            st.session_state.story_warning = None
                            st.error(e.friendly_message)
                            with st.expander("デバッグ情報（エラー詳細）"):
                                st.write(e.detail or "詳細情報はありません。")
                        else:
                            st.session_state.story_image_bytes = image_bytes
                            st.session_state.story_warning = warning
                    else:
                        st.session_state.note_text = "\n\n".join(
                            [original_text] + reply_texts
                        )

            except ThreadsAPIError as e:
                st.session_state.result = None
                st.error(e.friendly_message)
                with st.expander("デバッグ情報（エラー詳細）"):
                    st.write(f"HTTPステータスコード: {e.status_code}")
                    st.code(e.response_text or "（応答本文なし）")

# ここから下は、変換ボタンを押したときだけでなく、
# デザイン設定を変更したときの再実行でも表示され続けるようにする。
result = st.session_state.result

if result and result["mode"] == MODE_INSTAGRAM:
    st.markdown('<div class="tt-step-title">ストーリーズデザイン</div>', unsafe_allow_html=True)
    with st.container(border=True):
        reset_id = st.session_state.design_reset_id

        bg_file = st.file_uploader(
            "背景画像を選択（未指定の場合は白背景を使用します）",
            type=["png", "jpg", "jpeg", "webp"],
            key=f"story_bg_{reset_id}",
        )
        overlay_percent = st.slider(
            "背景の暗さ",
            min_value=0,
            max_value=80,
            value=20,
            step=5,
            format="%d%%",
            key=f"story_overlay_{reset_id}",
        )
        text_color = st.color_picker(
            "文字色",
            value=DEFAULT_TEXT_COLOR,
            key=f"story_color_{reset_id}",
        )
        font_size = st.slider(
            "文字サイズ",
            min_value=20,
            max_value=64,
            value=DEFAULT_MAX_FONT_SIZE,
            key=f"story_font_{reset_id}",
        )
        preview_clicked = st.button(
            "プレビューを更新",
            key=f"story_preview_btn_{reset_id}",
            use_container_width=True,
        )

    if preview_clicked:
        background_image = None
        bg_load_failed = False

        if bg_file is not None:
            try:
                background_image = load_background_image(bg_file.getvalue())
            except StoryImageError as e:
                bg_load_failed = True
                st.error(e.friendly_message)
                with st.expander("デバッグ情報（エラー詳細）"):
                    st.write(e.detail or "詳細情報はありません。")

        if not bg_load_failed:
            try:
                with st.spinner("プレビューを生成しています..."):
                    image_bytes, warning = generate_story_image(
                        profile_image_url=result["profile_image_url"],
                        account_name=result["account_name"],
                        original_text=result["original_text"],
                        own_replies=result["reply_texts"],
                        background_image=background_image,
                        text_color=text_color,
                        max_font_size=font_size,
                        overlay_opacity=overlay_percent / 100,
                    )
            except StoryImageError as e:
                st.error(e.friendly_message)
                with st.expander("デバッグ情報（エラー詳細）"):
                    st.write(e.detail or "詳細情報はありません。")
            else:
                st.session_state.story_image_bytes = image_bytes
                st.session_state.story_warning = warning

    if st.session_state.story_image_bytes:
        if st.session_state.story_warning:
            st.warning(st.session_state.story_warning)
        st.markdown('<div class="tt-step-title">プレビュー</div>', unsafe_allow_html=True)
        st.image(st.session_state.story_image_bytes, use_container_width=True)
        st.download_button(
            "PNGをダウンロード",
            data=st.session_state.story_image_bytes,
            file_name="threads_story.png",
            mime="image/png",
            use_container_width=True,
        )

        current_image_bytes = st.session_state.story_image_bytes
        current_image_hash = hashlib.sha256(current_image_bytes).hexdigest()
        already_posted = st.session_state.last_posted_image_hash == current_image_hash

        if st.session_state.ig_post_success_message:
            st.success(st.session_state.ig_post_success_message)
            st.session_state.ig_post_success_message = None

        if st.session_state.ig_post_error:
            ig_error = st.session_state.ig_post_error
            st.error(ig_error["message"])
            with st.expander("デバッグ情報（エラー詳細）"):
                st.write(ig_error["detail"] or "詳細情報はありません。")
            st.session_state.ig_post_error = None

        if st.session_state.ig_post_debug_info:
            debug_info = st.session_state.ig_post_debug_info
            with st.expander("デバッグ情報（投稿処理の詳細）"):
                st.write("Cloudinaryの公開URL:", debug_info.get("cloudinary_url"))
                verify_result = debug_info.get("verify_result")
                if verify_result:
                    st.write(
                        "画像取得テスト:",
                        f"HTTPステータス {verify_result['status_code']} / "
                        f"Content-Type {verify_result['content_type']} / "
                        f"{verify_result['byte_count']}バイト",
                    )
                for entry in debug_info.get("instagram_trace", []):
                    st.write(f"Instagram API - {entry['step']}:")
                    st.write(f"HTTPステータス: {entry['status_code']}")
                    st.code(entry["response_text"] or "（応答本文なし）")
            st.session_state.ig_post_debug_info = None

        post_clicked = st.button(
            "ストーリーズ投稿",
            key="ig_post_button",
            use_container_width=True,
        )
        if post_clicked:
            st.session_state.ig_post_confirm_pending = True

        if st.session_state.ig_post_confirm_pending:
            ig_credentials = get_instagram_credentials(result["account_label"])

            if not ig_credentials:
                st.error("このアカウントにはInstagram投稿設定がありません。")
                st.session_state.ig_post_confirm_pending = False
            else:
                if already_posted:
                    st.warning(
                        "この画像は既にInstagramへ投稿済みです。"
                        "同じ画像を再投稿する場合のみ「投稿する」を押してください。"
                    )
                st.warning(
                    f"この画像を「{result['account_label']}」の"
                    "Instagramストーリーズへ投稿しますか？"
                )
                confirm_col, cancel_col = st.columns(2)
                with confirm_col:
                    ig_confirm_clicked = st.button(
                        "投稿する", key="ig_post_confirm", use_container_width=True
                    )
                with cancel_col:
                    ig_cancel_clicked = st.button(
                        "キャンセル", key="ig_post_cancel", use_container_width=True
                    )

                if ig_cancel_clicked:
                    st.session_state.ig_post_confirm_pending = False

                if ig_confirm_clicked:
                    st.session_state.ig_post_confirm_pending = False
                    debug_info = {
                        "cloudinary_url": None,
                        "verify_result": None,
                        "instagram_trace": [],
                    }
                    ig_trace = debug_info["instagram_trace"]
                    try:
                        with st.spinner("Instagramへ投稿しています..."):
                            jpeg_bytes = convert_png_to_jpeg(current_image_bytes)
                            image_url, public_id = upload_story_image(jpeg_bytes)
                            debug_info["cloudinary_url"] = image_url

                            # Instagramに渡す前に、公開URLが実際に取得できる
                            # 正常なJPEGになっているかを確認する（CDN反映待ちを考慮）。
                            verify_result = verify_image_url(image_url)
                            debug_info["verify_result"] = verify_result

                            post_story(
                                ig_credentials["ig_user_id"],
                                ig_credentials["access_token"],
                                image_url,
                                trace=ig_trace,
                            )
                    except CloudinaryError as e:
                        st.session_state.ig_post_error = {
                            "message": e.friendly_message,
                            "detail": e.detail,
                        }
                    except InstagramAPIError as e:
                        st.session_state.ig_post_error = {
                            "message": e.friendly_message,
                            "detail": e.detail,
                        }
                    except Exception as e:
                        st.session_state.ig_post_error = {
                            "message": "投稿用画像の準備に失敗しました。もう一度お試しください。",
                            "detail": str(e),
                        }
                    else:
                        # Instagram側でのメディアコンテナ作成・公開がすべて成功した後にのみ、
                        # Cloudinaryの一時画像を削除する。削除に失敗しても投稿自体は成功として扱う。
                        delete_story_image(public_id)
                        st.session_state.last_posted_image_hash = current_image_hash
                        st.session_state.ig_post_success_message = (
                            "Instagramストーリーズへの投稿が完了しました。"
                        )
                    finally:
                        st.session_state.ig_post_debug_info = debug_info

                    # 確認ダイアログを画面から消し、結果メッセージだけを
                    # きれいに表示し直すために再実行する。
                    st.rerun()

elif result and result["mode"] == MODE_NOTE:
    st.markdown('<div class="tt-step-title">note投稿用テキスト</div>', unsafe_allow_html=True)
    st.text_area(
        "コピーしてお使いください（自由に編集できます）",
        height=600,
        key="note_text",
        label_visibility="collapsed",
    )
