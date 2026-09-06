import streamlit as st

from story_image import StoryImageError, generate_story_image
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
    page_title="Threads Transformer",
    page_icon="🧵",
    layout="centered",
)

inject_custom_css()

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
                    st.error(
                        "選択したアカウントの投稿として確認できませんでした。"
                        "アカウントの選択とThreadsリンクをご確認ください。"
                    )
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
                                    account_name=post.get("username", selected_account),
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
                            st.markdown(
                                '<div class="tt-step-title">生成されたストーリーズ画像</div>',
                                unsafe_allow_html=True,
                            )
                            st.image(image_bytes, use_container_width=True)
                            st.download_button(
                                "PNGをダウンロード",
                                data=image_bytes,
                                file_name="threads_story.png",
                                mime="image/png",
                                use_container_width=True,
                            )

                    else:  # MODE_NOTE
                        note_text = "\n\n".join([post.get("text", "")] + reply_texts)
                        st.markdown(
                            '<div class="tt-step-title">note投稿用テキスト</div>',
                            unsafe_allow_html=True,
                        )
                        st.text_area(
                            "コピーしてお使いください（自由に編集できます）",
                            value=note_text,
                            height=600,
                            label_visibility="collapsed",
                        )

            except ThreadsAPIError as e:
                st.error(e.friendly_message)
                with st.expander("デバッグ情報（エラー詳細）"):
                    st.write(f"HTTPステータスコード: {e.status_code}")
                    st.code(e.response_text or "（応答本文なし）")
