import streamlit as st

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

st.set_page_config(
    page_title="Threads Transformer",
    page_icon="🧵"
)

st.title("Threads Transformer")

st.write(
    "Threadsの投稿を、Instagramストーリーズ用画像または"
    "note投稿用文章に変換します。"
)

st.header("Instagram ストーリーズ 投稿用")
st.info("この機能は準備中です。")

st.header("note 投稿用")
st.info("この機能は準備中です。")

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
                        st.success("投稿情報を取得しました。")

                        st.subheader("投稿内容（確認用）")

                        profile_col, text_col = st.columns([1, 4])
                        with profile_col:
                            picture_url = (profile or {}).get("threads_profile_picture_url")
                            if picture_url:
                                st.image(picture_url, width=64)
                        with text_col:
                            st.write(f"**{post.get('username', username)}**")
                            st.caption(post.get("timestamp", ""))

                        st.write(post.get("text", ""))

                        st.markdown("### 本人による返信")
                        if own_replies:
                            for reply in own_replies:
                                st.write(reply.get("text", ""))
                                st.caption(reply.get("timestamp", ""))
                        else:
                            st.write("本人による返信はありませんでした。")

                except ThreadsAPIError as e:
                    st.error(e.friendly_message)
                    with st.expander("デバッグ情報（エラー詳細）"):
                        st.write(f"HTTPステータスコード: {e.status_code}")
                        st.code(e.response_text or "（応答本文なし）")
