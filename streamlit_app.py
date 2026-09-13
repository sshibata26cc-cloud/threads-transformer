import hashlib
import uuid

import streamlit as st
from streamlit_sortables import sort_items

from app_meta import inject_mobile_meta_tags, resolve_page_icon
from carousel_generator import generate_carousel_page_image
from carousel_splitter import split_into_pages
from carousel_ui import (
    CAROUSEL_CARD_WIDTH,
    REDO_BUTTON_LABEL,
    UNDO_BUTTON_LABEL,
    inject_carousel_card_css,
    inject_carousel_shortcuts_js,
)
from cloudinary_storage import (
    CloudinaryError,
    delete_story_image,
    upload_story_image,
    verify_image_url,
)
from instagram_api import InstagramAPIError, get_instagram_credentials, post_story
from story_image import (
    DEFAULT_MAX_FONT_SIZE,
    DEFAULT_TEXT_BG_COLOR,
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
MODE_CAROUSEL = "Instagram カルーセル投稿用"
MODE_NOTE = "note 投稿用"

TEXT_BG_MODE_NONE = "透明"
TEXT_BG_MODE_COLOR = "色を設定"

ACCOUNT_CHOICES = ["shin.coaching", "takuma_o369", "masa_life128"]

# カルーセルのUndo/Redo履歴として保持する最大件数。
# これを超えたら、最も古い履歴から削除する。
CAROUSEL_HISTORY_LIMIT = 20


def _new_carousel_page_id() -> str:
    """
    カルーセルページの内部ID（UUID）を新しく発行する。

    表示番号ではなくこのIDでページを管理することで、並び替え・追加・削除を
    行っても、本文・選択状態・画像との対応関係が崩れないようにする。
    Streamlitのwidget keyにもこのIDをそのまま利用する。
    """
    return uuid.uuid4().hex


def _default_carousel_design():
    """カルーセルデザイン設定の初期値（＝これまでの既定値と同じ）。"""
    return {
        "font_size": DEFAULT_MAX_FONT_SIZE,
        "text_color": DEFAULT_TEXT_COLOR,
        "overlay_percent": 20,
        "text_bg_mode": TEXT_BG_MODE_NONE,
        "text_bg_color": DEFAULT_TEXT_BG_COLOR,
    }


def _carousel_design_keys(reset_id):
    """カルーセルのデザイン設定項目名と、対応するwidget keyの対応表。"""
    return {
        "font_size": f"carousel_font_{reset_id}",
        "text_color": f"carousel_color_{reset_id}",
        "overlay_percent": f"carousel_overlay_{reset_id}",
        "text_bg_mode": f"carousel_text_bg_mode_{reset_id}",
        "text_bg_color": f"carousel_text_bg_color_{reset_id}",
    }


def _current_carousel_design(reset_id):
    """現在の各デザインwidgetの値を、Undo/Redo用のスナップショット形式で読み取る。"""
    defaults = _default_carousel_design()
    return {
        name: st.session_state.get(key, defaults[name])
        for name, key in _carousel_design_keys(reset_id).items()
    }


def _current_carousel_state(reset_id):
    """
    現在の編集状態（ページ構成・本文・選択中ページ・デザイン設定）を、
    Undo/Redo履歴に保存できる軽量なスナップショットとして取り出す。

    Threads APIの取得結果そのものや画像バイナリは含めない。
    """
    page_ids = list(st.session_state.carousel_page_ids)
    return {
        "page_ids": page_ids,
        "texts": {pid: st.session_state.carousel_store.get(pid, "") for pid in page_ids},
        "selected_id": st.session_state.carousel_selected_id,
        "design": _current_carousel_design(reset_id),
    }


def _apply_carousel_state(state, reset_id):
    """
    Undo/Redoで選んだスナップショットの内容を、実際のセッション状態へ書き戻す。

    carousel_active_editor（共有テキスト編集欄）は、この時点ではまだ
    今回の実行で描画されていないため、直接書き込んでも問題ない。
    ただし「選択中ページは変わらないが本文だけ戻したい」というUndoもあり得るため、
    次の描画時に必ずcarousel_active_editorへ再同期させるフラグを立てておく。
    """
    st.session_state.carousel_page_ids = list(state["page_ids"])
    st.session_state.carousel_store = dict(state["texts"])
    st.session_state.carousel_selected_id = state["selected_id"]
    st.session_state.carousel_force_editor_resync = True

    design_keys = _carousel_design_keys(reset_id)
    for name, value in state.get("design", {}).items():
        if name in design_keys:
            st.session_state[design_keys[name]] = value


def _carousel_content_signature(state):
    """
    Undo/Redo履歴に「新しい1手」として記録すべきかどうかの判定に使う、
    内容だけを見た比較用の値（選択中ページは含めない）。

    ページをクリックして選んだだけでは、文章・並び順・デザインといった
    「内容」は何も変わっていないため、履歴を1件消費させたくない。
    """
    return (state["page_ids"], state["texts"], state["design"])


def _push_carousel_history(reset_id):
    """
    現在の編集状態を履歴に記録する。直前に記録した内容と変わっていなければ、
    新しい履歴は積まず、選択中ページの情報だけ最新に更新する
    （ページをクリックして選ぶだけの操作でUndo履歴を消費しないため）。

    Undoで過去の状態へ戻った後に新しい操作を行った場合は、それより先の
    Redo履歴を破棄する（一般的なエディタと同じ挙動）。履歴は最大
    CAROUSEL_HISTORY_LIMIT件までとし、超えた分は最も古いものから削除する。
    """
    history = st.session_state.carousel_history
    index = st.session_state.carousel_history_index
    current = _current_carousel_state(reset_id)

    if history and _carousel_content_signature(current) == _carousel_content_signature(
        history[index]
    ):
        if history[index]["selected_id"] != current["selected_id"]:
            history[index]["selected_id"] = current["selected_id"]
            st.session_state.carousel_history = history
        return

    history = history[: index + 1]
    history.append(current)
    if len(history) > CAROUSEL_HISTORY_LIMIT:
        history = history[-CAROUSEL_HISTORY_LIMIT:]

    st.session_state.carousel_history = history
    st.session_state.carousel_history_index = len(history) - 1


def _reset_carousel_pages(pages_text):
    """
    Threadsから新しく取得した内容をもとに、カルーセルのページ構成を作り直す。

    各ページにUUIDの内部IDを割り当て、その本文を carousel_store（プレーンな
    dict）へ保存する。あわせて、Undo/Redo履歴もこの状態を起点として作り直す
    （別のThreads投稿を新しく変換したときだけ、ここが呼ばれる）。
    """
    new_ids = [_new_carousel_page_id() for _ in pages_text]
    st.session_state.carousel_page_ids = new_ids
    st.session_state.carousel_store = dict(zip(new_ids, pages_text))
    st.session_state.carousel_selected_id = new_ids[0] if new_ids else None

    # 共有テキスト編集欄（carousel_active_editor）も、選択中の先頭ページの
    # 本文へ合わせておく。まだ今回の実行でこのウィジェットは描画されていないため、
    # ここでsession_stateへ直接書き込んでも問題ない。
    st.session_state.carousel_active_editor = pages_text[0] if pages_text else ""
    st.session_state.carousel_editor_owner_id = st.session_state.carousel_selected_id

    initial_state = {
        "page_ids": list(new_ids),
        "texts": dict(zip(new_ids, pages_text)),
        "selected_id": st.session_state.carousel_selected_id,
        "design": _default_carousel_design(),
    }
    st.session_state.carousel_history = [initial_state]
    st.session_state.carousel_history_index = 0


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
            Threadsの投稿を、Instagramストーリーズ用画像・<br>
            カルーセル用画像・note投稿用テキストに変換します。
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
if "carousel_page_ids" not in st.session_state:
    st.session_state.carousel_page_ids = []
if "carousel_store" not in st.session_state:
    # ページID -> 本文 のプレーンなdict。
    # st.text_areaのwidget keyとしては使わない専用の保存領域にすることで、
    # 選択中でなくなったページの本文がStreamlitのウィジェット破棄処理で
    # 消えてしまわないようにしている（詳細は_apply_carousel_state等を参照）。
    st.session_state.carousel_store = {}
if "carousel_selected_id" not in st.session_state:
    st.session_state.carousel_selected_id = None
if "carousel_history" not in st.session_state:
    st.session_state.carousel_history = []
if "carousel_history_index" not in st.session_state:
    st.session_state.carousel_history_index = 0
if "carousel_force_editor_resync" not in st.session_state:
    st.session_state.carousel_force_editor_resync = False

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
        (MODE_INSTAGRAM, MODE_CAROUSEL, MODE_NOTE),
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
                    elif mode == MODE_CAROUSEL:
                        pages_text = split_into_pages(original_text, reply_texts)
                        _reset_carousel_pages(pages_text)
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

        # 文字の背景色。初期状態は必ず「透明」（＝これまでと同じ見た目）。
        text_bg_mode = st.radio(
            "文字の背景",
            (TEXT_BG_MODE_NONE, TEXT_BG_MODE_COLOR),
            index=0,
            horizontal=True,
            key=f"story_text_bg_mode_{reset_id}",
        )
        if text_bg_mode == TEXT_BG_MODE_COLOR:
            text_bg_color = st.color_picker(
                "文字の背景色",
                value=DEFAULT_TEXT_BG_COLOR,
                key=f"story_text_bg_color_{reset_id}",
            )
        else:
            text_bg_color = None

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
                        text_bg_color=text_bg_color,
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

elif result and result["mode"] == MODE_CAROUSEL:
    reset_id = st.session_state.design_reset_id

    # 「カルーセルデザイン」の表示位置だけ先に確保しておき、実際のウィジェットは
    # このブロックの後半（Undo/Redoによる状態復元より後）でまとめて描画する。
    # Streamlitは、今回すでに描画済みのウィジェットのkeyへ後からsession_state経由で
    # 書き込むことを許さないため、Undo/Redoでの復元を必ず先に済ませる必要がある。
    st.markdown('<div class="tt-step-title">カルーセルデザイン</div>', unsafe_allow_html=True)
    design_container = st.container(border=True)

    st.markdown('<div class="tt-step-title">カルーセルページ</div>', unsafe_allow_html=True)

    # 選択中ページが何らかの理由でページ一覧から消えていたら、先頭ページへ戻す。
    page_ids = st.session_state.carousel_page_ids
    if page_ids and st.session_state.carousel_selected_id not in page_ids:
        st.session_state.carousel_selected_id = page_ids[0]
    selected_id = st.session_state.carousel_selected_id

    # --- 共有テキスト編集欄（carousel_active_editor）とcarousel_storeの同期 ---
    # 編集欄はページごとに別ウィジェットにせず、1つのウィジェットを使い回している。
    # 選択中ページが切り替わったとき（または直前にUndo/Redoで復元が行われたとき）は、
    # 編集欄の表示内容を保存領域（carousel_store）の値へ差し替える。
    # 切り替わっていない間は、編集欄の「今の値」こそが最新の編集内容なので上書きしない。
    force_resync = st.session_state.pop("carousel_force_editor_resync", False)
    if selected_id is not None and (
        force_resync or st.session_state.get("carousel_editor_owner_id") != selected_id
    ):
        st.session_state.carousel_active_editor = st.session_state.carousel_store.get(
            selected_id, ""
        )
        st.session_state.carousel_editor_owner_id = selected_id

    # 編集欄の最新の内容を、選択中ページの保存領域へ書き戻す。
    # 以降のサムネイル生成・並び替え・Undo履歴の判定は、すべてこの保存領域を参照する。
    if selected_id is not None:
        st.session_state.carousel_store[selected_id] = st.session_state.get(
            "carousel_active_editor", ""
        )

    # --- ドラッグ＆ドロップによる並び替え ---
    # 画像そのものをドラッグ対象にはできないため、「順番の入れ替え」だけを
    # 担当する軽量な操作バーを、画像カード列の前に置いている。
    # ボタン操作（追加・削除・Undo/Redo）より先にここで並び替えを確定させることで、
    # 直後に計算するUndo/Redoの押せる/押せない状態がこの回の操作をすぐ反映できる。
    page_texts = {pid: st.session_state.carousel_store.get(pid, "") for pid in page_ids}

    if page_ids:

        def _sort_label(index, pid):
            snippet = page_texts[pid].replace("\n", " ").strip()[:12]
            return f"{index + 1}. {snippet}" if snippet else f"{index + 1}. (空白)"

        sort_labels = [_sort_label(i, pid) for i, pid in enumerate(page_ids)]
        label_to_id = dict(zip(sort_labels, page_ids))

        st.caption("↕ ドラッグして並び替え")
        # keyに現在の並び順を含めることで、Undo/Redoなどドラッグ以外の理由で
        # ページ順が変わったときはウィジェットを作り直させ、内部に残った
        # 古い並びが表示され続けないようにする。
        sorted_labels = sort_items(
            sort_labels,
            direction="horizontal",
            key=f"carousel_sort_{reset_id}_{'|'.join(page_ids)}",
            custom_style="""
            .sortable-component { background-color: #FBF6EC; border-radius: 10px; padding: 6px; }
            .sortable-item { background-color: #FFFDF8; border: 1px solid #E7DAC5;
                             color: #3B2E27; border-radius: 8px; }
            .sortable-item.active { border-color: #A9673F; }
            """,
        )
        new_order = [label_to_id[label] for label in sorted_labels if label in label_to_id]
        if len(new_order) == len(page_ids) and new_order != page_ids:
            st.session_state.carousel_page_ids = new_order
            page_ids = new_order

    # ここまでの並び替えを反映したうえで、今回の実行で確定した状態を履歴に記録する。
    # （テキスト編集・デザイン変更・並び替えは、ここより前に session_state へ反映済み）
    _push_carousel_history(reset_id)

    history = st.session_state.carousel_history
    history_index = st.session_state.carousel_history_index
    can_undo = history_index > 0
    can_redo = history_index < len(history) - 1
    can_delete_page = len(page_ids) > 1

    # ページ操作（追加・削除・元に戻す・やり直す）をひとまとめに配置する。
    action_add_col, action_delete_col, action_undo_col, action_redo_col = st.columns(4)
    with action_add_col:
        add_page_clicked = st.button(
            "ページを追加", key="carousel_add_page", use_container_width=True
        )
    with action_delete_col:
        delete_page_clicked = st.button(
            "選択中のページを削除",
            key="carousel_delete_page",
            use_container_width=True,
            disabled=not can_delete_page,
        )
    with action_undo_col:
        undo_clicked = st.button(
            UNDO_BUTTON_LABEL,
            key="carousel_undo",
            use_container_width=True,
            disabled=not can_undo,
        )
    with action_redo_col:
        redo_clicked = st.button(
            REDO_BUTTON_LABEL,
            key="carousel_redo",
            use_container_width=True,
            disabled=not can_redo,
        )

    # Ctrl+Z/Ctrl+Y（Mac: Cmd+Z/Cmd+Shift+Z）でも、上と同じボタンを操作させる。
    inject_carousel_shortcuts_js()

    if add_page_clicked:
        # 「現在選択中のページの直後」へ新規ページを挿入する。
        new_id = _new_carousel_page_id()
        ids = list(st.session_state.carousel_page_ids)
        try:
            insert_at = ids.index(selected_id) + 1
        except ValueError:
            insert_at = len(ids)
        ids.insert(insert_at, new_id)
        st.session_state.carousel_page_ids = ids
        st.session_state.carousel_store[new_id] = ""
        st.session_state.carousel_selected_id = new_id
        st.rerun()

    if delete_page_clicked and can_delete_page:
        ids = list(st.session_state.carousel_page_ids)
        deleted_index = ids.index(selected_id)
        removed_id = ids.pop(deleted_index)
        st.session_state.carousel_page_ids = ids
        st.session_state.carousel_store.pop(removed_id, None)
        # 可能なら直前のページ、それが無ければ（先頭を削除した場合）次のページを選択する。
        new_selected_index = deleted_index - 1 if deleted_index > 0 else 0
        st.session_state.carousel_selected_id = ids[new_selected_index]
        st.rerun()

    if undo_clicked and can_undo:
        st.session_state.carousel_history_index = history_index - 1
        _apply_carousel_state(history[history_index - 1], reset_id)
        st.rerun()

    if redo_clicked and can_redo:
        st.session_state.carousel_history_index = history_index + 1
        _apply_carousel_state(history[history_index + 1], reset_id)
        st.rerun()

    # --- 「カルーセルデザイン」ウィジェットの描画 ---
    # Undo/Redoによる復元は必ずここより前に完了しているので、
    # ウィジェット描画後にsession_stateへ書き込もうとするエラーは起きない。
    #
    # 各ウィジェットは value= を渡さず、代わりに「まだキーが無ければ既定値を
    # session_stateへ入れておく」方式にしている。value= とsession_state経由の
    # 値を両方使うとStreamlitが警告を出すため、Undo/Redoで復元した値と
    # 初回表示時の既定値の両方を、同じ仕組みで自然に扱えるようにするため。
    with design_container:
        carousel_bg_file = st.file_uploader(
            "背景画像を選択（未指定の場合は白背景を使用します・全ページ共通）",
            type=["png", "jpg", "jpeg", "webp"],
            key=f"carousel_bg_{reset_id}",
        )

        overlay_key = f"carousel_overlay_{reset_id}"
        if overlay_key not in st.session_state:
            st.session_state[overlay_key] = 20
        carousel_overlay_percent = st.slider(
            "背景の暗さ",
            min_value=0,
            max_value=80,
            step=5,
            format="%d%%",
            key=overlay_key,
        )

        color_key = f"carousel_color_{reset_id}"
        if color_key not in st.session_state:
            st.session_state[color_key] = DEFAULT_TEXT_COLOR
        carousel_text_color = st.color_picker("文字色（全ページ共通）", key=color_key)

        # 文字の背景色。Storyズ機能と同じ仕様（初期状態は必ず「透明」）。
        text_bg_mode_key = f"carousel_text_bg_mode_{reset_id}"
        if text_bg_mode_key not in st.session_state:
            st.session_state[text_bg_mode_key] = TEXT_BG_MODE_NONE
        carousel_text_bg_mode = st.radio(
            "文字の背景",
            (TEXT_BG_MODE_NONE, TEXT_BG_MODE_COLOR),
            horizontal=True,
            key=text_bg_mode_key,
        )
        if carousel_text_bg_mode == TEXT_BG_MODE_COLOR:
            text_bg_color_key = f"carousel_text_bg_color_{reset_id}"
            if text_bg_color_key not in st.session_state:
                st.session_state[text_bg_color_key] = DEFAULT_TEXT_BG_COLOR
            carousel_text_bg_color = st.color_picker("文字の背景色", key=text_bg_color_key)
        else:
            carousel_text_bg_color = None

        font_key = f"carousel_font_{reset_id}"
        if font_key not in st.session_state:
            st.session_state[font_key] = DEFAULT_MAX_FONT_SIZE
        carousel_font_size = st.slider(
            "文字サイズ（最大値・全ページ共通）",
            min_value=20,
            max_value=64,
            key=font_key,
        )

    # 背景画像はページ数ぶん何度も読み込み直さないよう、ここで一度だけ読み込む。
    # 読み込みに失敗した場合はエラーを表示しつつ、背景なし（白背景）で
    # 各ページのプレビューは表示を続ける。
    carousel_background_image = None
    if carousel_bg_file is not None:
        try:
            carousel_background_image = load_background_image(carousel_bg_file.getvalue())
        except StoryImageError as e:
            st.error(e.friendly_message)
            with st.expander("デバッグ情報（エラー詳細）"):
                st.write(e.detail or "詳細情報はありません。")

    def _generate_carousel_preview(page_text):
        return generate_carousel_page_image(
            page_text,
            background_image=carousel_background_image,
            text_color=carousel_text_color,
            text_bg_color=carousel_text_bg_color,
            max_font_size=carousel_font_size,
            overlay_opacity=carousel_overlay_percent / 100,
        )

    # ここまでのボタン操作でページ構成が変わっていないことが確定した状態で描画する。
    page_ids = st.session_state.carousel_page_ids
    total_pages = len(page_ids)
    selected_id = st.session_state.carousel_selected_id

    if not page_ids:
        st.info("ページがありません。「ページを追加」から作成してください。")
    else:
        # page_textsは並び替えセクションで既に計算済み（reorderはpidの順番だけを
        # 変えるので、辞書の中身自体は作り直さなくても引き続き有効）。

        # --- 横スクロールのカードプレビュー一覧 ---
        inject_carousel_card_css(selected_id)

        with st.container(horizontal=True, wrap=False, key="carousel_thumb_row"):
            for index, pid in enumerate(page_ids):
                with st.container(
                    key=f"carousel_card_{pid}", border=True, width=CAROUSEL_CARD_WIDTH
                ):
                    try:
                        thumb_bytes, _ = _generate_carousel_preview(page_texts.get(pid, ""))
                    except StoryImageError:
                        thumb_bytes = None
                    if thumb_bytes:
                        st.image(thumb_bytes, width=CAROUSEL_CARD_WIDTH - 24)
                    if st.button(
                        str(index + 1),
                        key=f"carousel_select_{pid}",
                        use_container_width=True,
                    ):
                        st.session_state.carousel_selected_id = pid
                        st.rerun()

        # --- 選択中ページの大きめのプレビューとテキスト編集欄 ---
        selected_index = page_ids.index(selected_id)
        st.markdown(
            f'<div class="tt-step-title">ページ {selected_index + 1} / {total_pages} を編集中</div>',
            unsafe_allow_html=True,
        )

        selected_image_bytes = None
        selected_warning = None
        try:
            selected_image_bytes, selected_warning = _generate_carousel_preview(
                page_texts.get(selected_id, "")
            )
        except StoryImageError as e:
            st.error(e.friendly_message)
            with st.expander("デバッグ情報（エラー詳細）"):
                st.write(e.detail or "詳細情報はありません。")

        if selected_image_bytes:
            if selected_warning:
                st.warning(selected_warning)
            preview_col, _spacer_col = st.columns([1, 1])
            with preview_col:
                st.image(selected_image_bytes, use_container_width=True)
            st.download_button(
                f"ページ{selected_index + 1}のPNGをダウンロード",
                data=selected_image_bytes,
                file_name=f"threads_carousel_{selected_index + 1}.png",
                mime="image/png",
                use_container_width=True,
                key=f"carousel_download_{selected_id}",
            )

        # 全ページ共通で1つだけ使い回す編集欄。keyは選択中ページIDではなく
        # 固定文字列にし、実際の本文はcarousel_storeとの同期処理（このブロックの
        # 冒頭）で切り替える。ページごとに異なるkeyを使うと、選択が外れた
        # ページのウィジェットとしてStreamlitに扱われ、値が破棄されてしまうため。
        st.text_area(
            "本文",
            key="carousel_active_editor",
            height=220,
            label_visibility="collapsed",
        )

elif result and result["mode"] == MODE_NOTE:
    st.markdown('<div class="tt-step-title">note投稿用テキスト</div>', unsafe_allow_html=True)
    st.text_area(
        "コピーしてお使いください（自由に編集できます）",
        height=600,
        key="note_text",
        label_visibility="collapsed",
    )
