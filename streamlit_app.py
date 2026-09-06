import streamlit as st

from app_meta import inject_mobile_meta_tags, resolve_page_icon
from story_image import (
    DEFAULT_MAX_FONT_SIZE,
    DEFAULT_TEXT_COLOR,
    StoryImageError,
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
                        "account_name": account_name,
                        "profile_image_url": profile_image_url,
                        "original_text": original_text,
                        "reply_texts": reply_texts,
                    }
                    st.session_state.design_reset_id += 1

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

elif result and result["mode"] == MODE_NOTE:
    st.markdown('<div class="tt-step-title">note投稿用テキスト</div>', unsafe_allow_html=True)
    st.text_area(
        "コピーしてお使いください（自由に編集できます）",
        height=600,
        key="note_text",
        label_visibility="collapsed",
    )
