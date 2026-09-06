import streamlit as st

from gemini_formatter import GeminiFormatError, format_for_note
from story_image import StoryImageError, generate_story_image
from threads_api import (
    ThreadsAPIError,
    extract_username_and_shortcode,
    find_post_by_shortcode,
    get_access_token,
    get_own_replies_in_order,
    get_post_detail,
    get_post_replies,
    get_profile,
)

MODE_INSTAGRAM = "Instagram ストーリーズ 投稿用"
MODE_NOTE = "note 投稿用"

st.set_page_config(
    page_title="Threads Transformer",
    page_icon="🧵"
)

st.title("Threads Transformer")

st.write(
    "Threadsの投稿を、Instagramストーリーズ用画像または"
    "note投稿用文章に変換します。"
)

st.header("変換先を選択")
mode = st.radio(
    "変換したい形式を選んでください",
    (MODE_INSTAGRAM, MODE_NOTE),
)

st.header("Threads投稿の読み込み")

threads_url = st.text_input(
    "Threads投稿のURLを入力してください",
    placeholder="https://www.threads.net/@shin.coaching/post/Cxxxxxxxx",
)

if st.button("変換する"):
    if not threads_url:
        st.warning("Threads投稿のURLを入力してください。")
    else:
        username, shortcode = extract_username_and_shortcode(threads_url)

        if not username:
            st.error(
                "URLの形式が正しくありません。"
                "「https://www.threads.net/@アカウント名/post/投稿ID」の形式で入力してください。"
            )
        else:
            access_token = get_access_token(username)

            if not access_token:
                st.error("このアカウントは現在変換対象に登録されていません")
            else:
                try:
                    with st.spinner("Threadsから投稿情報を取得しています..."):
                        post_summary = find_post_by_shortcode(access_token, shortcode)

                        post = None
                        profile = None
                        own_replies = []

                        if post_summary:
                            post = get_post_detail(post_summary["id"], access_token)
                            profile = get_profile(access_token)
                            replies = get_post_replies(post["id"], access_token)
                            own_replies = get_own_replies_in_order(
                                replies, post.get("username", username)
                            )

                    if not post_summary:
                        st.error("入力されたURLに対応する投稿が見つかりませんでした。")
                    else:
                        st.success("Threadsから投稿情報を取得しました。")
                        reply_texts = [r.get("text", "") for r in own_replies]

                        if mode == MODE_INSTAGRAM:
                            try:
                                with st.spinner("ストーリーズ画像を生成しています..."):
                                    image_bytes, warning = generate_story_image(
                                        profile_image_url=(profile or {}).get(
                                            "threads_profile_picture_url"
                                        ),
                                        account_name=post.get("username", username),
                                        original_text=post.get("text", ""),
                                        own_replies=reply_texts,
                                    )
                            except StoryImageError as e:
                                st.error(e.friendly_message)
                                with st.expander("デバッグ情報（エラー詳細）"):
                                    st.write(e.detail or "詳細情報はありません。")
                            else:
                                if warning:
                                    st.warning(warning)
                                st.subheader("生成されたストーリーズ画像")
                                st.image(image_bytes)
                                st.download_button(
                                    "PNGをダウンロード",
                                    data=image_bytes,
                                    file_name="threads_story.png",
                                    mime="image/png",
                                )

                        else:  # MODE_NOTE
                            try:
                                with st.spinner("Geminiで文章を整形しています..."):
                                    formatted_text = format_for_note(
                                        post.get("text", ""), reply_texts
                                    )
                            except GeminiFormatError as e:
                                st.error(e.friendly_message)
                                with st.expander("デバッグ情報（エラー詳細）"):
                                    st.write(e.detail or "詳細情報はありません。")
                            else:
                                st.subheader("整形されたnote投稿用の文章")
                                st.text_area(
                                    "コピーしてお使いください",
                                    value=formatted_text,
                                    height=400,
                                )

                except ThreadsAPIError as e:
                    st.error(e.friendly_message)
                    with st.expander("デバッグ情報（エラー詳細）"):
                        st.write(f"HTTPステータスコード: {e.status_code}")
                        st.code(e.response_text or "（応答本文なし）")
