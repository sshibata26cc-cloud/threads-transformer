import hashlib
import json
import uuid

import streamlit as st

from app_fonts import DEFAULT_FONT_KEY, FONT_OPTIONS
from app_meta import inject_mobile_meta_tags, resolve_page_icon
from carousel_generator import generate_carousel_page_image
from carousel_splitter import split_into_pages
from carousel_ui import (
    REDO_BUTTON_LABEL,
    UNDO_BUTTON_LABEL,
    inject_carousel_card_css,
    inject_carousel_scroll_restore_js,
    inject_carousel_shortcuts_js,
)
from cloudinary_storage import (
    CloudinaryError,
    delete_story_image,
    upload_story_image,
    verify_image_url,
)
from instagram_api import InstagramAPIError, get_instagram_credentials, post_story
from reply_filters import filter_own_replies
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


# --------------------------------------------------------------------
# 画像生成のキャッシュ（Story / Carousel共通）
# --------------------------------------------------------------------
# Story・Carouselどちらも「デザイン設定を変更するたびに、現在の入力値から
# その場で画像を再生成する」仕組みになっている。裏を返すと、選択中ページを
# 切り替えただけ・他のページの本文を編集しただけ・ページを並び替えただけ、
# といった「この画像の見た目には影響しない操作」でも再実行が走るたびに、
# 同じ内容のPillow処理（背景画像のEXIF回転・RGB変換・cover/crop・文字の
# 折り返し・描画）を何度もやり直してしまう。
#
# 生成結果は「入力値だけで一意に決まる」純粋な処理なので、
# st.cache_dataで安全にキャッシュし、入力値が実際に変わったときだけ
# 再生成する。背景画像（PIL Imageオブジェクト）はそのままでは
# st.cache_dataのハッシュ対象にすると重く・不安定なため、アップロード
# ファイルの中身のハッシュ値を別途キーとして渡し、画像オブジェクト自体は
# 引数名の先頭に "_" を付けてハッシュ対象から除外する
# （Streamlitの標準的な回避策）。
def _background_cache_key(file_bytes):
    """アップロードされた背景画像ファイルの内容から、キャッシュキー用の
    短いハッシュ値を作る。背景画像が指定されていない場合はNoneを返す。"""
    if not file_bytes:
        return None
    return hashlib.sha256(file_bytes).hexdigest()


@st.cache_data(show_spinner=False, max_entries=5)
def _cached_load_background_image(file_bytes):
    """
    背景画像の読み込み（EXIF回転の反映・RGB変換）をキャッシュする。

    デザイン設定（文字サイズ・フォント・文字色など）を変更するたびに
    同じアップロード済みファイルを毎回読み込み直していたのを防ぐ。
    キャッシュキーはfile_bytes自体（アップロードされたファイルの中身）
    なので、別の画像に差し替えれば正しく再読み込みされる。
    """
    return load_background_image(file_bytes)


@st.cache_data(show_spinner=False, max_entries=20)
def _cached_story_image(
    profile_image_url,
    account_name,
    original_text,
    own_replies_tuple,
    bg_cache_key,
    text_color,
    text_bg_color,
    max_font_size,
    overlay_opacity,
    font_key,
    _background_image,
):
    """
    generate_story_image()の結果をキャッシュする。

    Story画面のデザイン設定を1つ変えるたびに、それ以外の設定が同じ
    組み合わせであれば以前と同じ画像になる。すべての入力値が一致する
    場合は再生成をスキップする。プレビュー・PNGダウンロード・
    Instagram投稿は、この関数が返す同じ結果をそのまま使い続けるため、
    表示内容がずれることはない。
    """
    return generate_story_image(
        profile_image_url=profile_image_url,
        account_name=account_name,
        original_text=original_text,
        own_replies=list(own_replies_tuple),
        background_image=_background_image,
        text_color=text_color,
        text_bg_color=text_bg_color,
        max_font_size=max_font_size,
        overlay_opacity=overlay_opacity,
        font_key=font_key,
    )


@st.cache_data(show_spinner=False, max_entries=80)
def _cached_carousel_page_image(
    page_text,
    bg_cache_key,
    text_color,
    text_bg_color,
    max_font_size,
    overlay_opacity,
    font_key,
    _background_image,
):
    """
    generate_carousel_page_image()の結果をキャッシュする。

    これにより、ページを選択しただけ・他のページを編集しただけ・
    ページを並び替えただけ・前後移動しただけ・ページを削除しただけ、
    といった「そのページ自身の見た目には影響しない操作」では、
    各ページの画像を再生成せずに済む（キャッシュキーが変わらないため）。
    本文・背景・デザイン設定のいずれかが変わったページ、または
    新しく追加されたページだけが実際に再生成される。
    """
    return generate_carousel_page_image(
        page_text,
        background_image=_background_image,
        text_color=text_color,
        text_bg_color=text_bg_color,
        max_font_size=max_font_size,
        overlay_opacity=overlay_opacity,
        font_key=font_key,
    )


def _sanitize_font_choice_session_value(key: str) -> None:
    """
    st.selectbox()は、session_stateに既にkeyの値が入っている場合、それが
    optionsに含まれていないとエラーになる。フォントの選択肢を変更・削除
    したときに、過去のsession_state（例: 削除済みの「教科書体」や、
    以前のデフォルトだった「ゴシック体」）が残っていてもエラーにならない
    よう、該当キーの値が現在のFONT_OPTIONSに含まれているかを描画前に
    確認し、含まれていなければ現在のデフォルトへ差し替える。
    """
    if key in st.session_state and st.session_state[key] not in FONT_OPTIONS:
        st.session_state[key] = DEFAULT_FONT_KEY


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
        "font_choice": DEFAULT_FONT_KEY,
        "font_size": DEFAULT_MAX_FONT_SIZE,
        "text_color": DEFAULT_TEXT_COLOR,
        "overlay_percent": 20,
        "text_bg_mode": TEXT_BG_MODE_NONE,
        "text_bg_color": DEFAULT_TEXT_BG_COLOR,
    }


def _carousel_design_keys(reset_id):
    """カルーセルのデザイン設定項目名と、対応するwidget keyの対応表。"""
    return {
        "font_choice": f"carousel_font_choice_{reset_id}",
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
            if name == "font_choice" and value not in FONT_OPTIONS:
                # 過去のUndo履歴に、削除済み・改名済みのフォント名
                # （例: 旧「教科書体」）が残っている場合でもエラーに
                # ならないよう、現在のデフォルトへ差し替える。
                value = DEFAULT_FONT_KEY
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
    current_sig = _carousel_content_signature(current)

    if history and current_sig == _carousel_content_signature(history[index]):
        if history[index]["selected_id"] != current["selected_id"]:
            history[index]["selected_id"] = current["selected_id"]
            st.session_state.carousel_history = history
        return

    # 新しい履歴として積む前に、末尾の実エントリ（history[-1]）ともう一度だけ
    # 比較する。ボタン操作による再実行と、横スクロール／画像クリックに由来する
    # 選択変更の再実行が極めて近いタイミングで重なると、双方が更新前の古い
    # carousel_history_indexを見たまま「内容が変わった」と判定してしまい、
    # まったく同じ内容の履歴が2件連続で積まれてしまうことがあった
    # （＝一見「元に戻す」が効かないように見える不具合の原因。実際には
    # 1回目のUndoが、直前と中身が同じ“重複した1手”へ戻っていただけだった）。
    # index基準の判定だけでなく、実際の末尾ともここで比較しておくことで、
    # このケースでは新しい履歴を積まずに済み、この重複を防げる。
    if history and current_sig == _carousel_content_signature(history[-1]):
        st.session_state.carousel_history_index = len(history) - 1
        if history[-1]["selected_id"] != current["selected_id"]:
            history[-1]["selected_id"] = current["selected_id"]
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
                        # Story・Carousel・note共通の除外ルール（告知文言・
                        # Google Form/YouTubeリンクを含む本人返信を除外する）。
                        # ここで1回フィルタするだけで、reply_textsを介して
                        # 3機能すべてに反映される。時系列順はそのまま維持される。
                        own_replies = filter_own_replies(own_replies)

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
                        # Story画像は下の「ストーリーズデザイン」欄で、現在の
                        # デザイン設定（初期値）から即座にリアルタイム生成される
                        # ため、ここで改めて生成する必要はない。
                        pass
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
        _sanitize_font_choice_session_value(f"story_font_choice_{reset_id}")
        story_font_choice = st.selectbox(
            "フォント",
            FONT_OPTIONS,
            index=FONT_OPTIONS.index(DEFAULT_FONT_KEY),
            key=f"story_font_choice_{reset_id}",
        )
        font_size = st.slider(
            "文字サイズ",
            min_value=20,
            max_value=64,
            value=DEFAULT_MAX_FONT_SIZE,
            key=f"story_font_{reset_id}",
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

    # 背景画像はここで一度だけ読み込む。EXIF回転・RGB変換自体は
    # _cached_load_background_image()がファイル内容ごとにキャッシュするため、
    # 他のデザイン設定（文字サイズ・フォント等）を変えるだけの再実行では
    # 同じファイルを読み込み直さない。
    background_image = None
    bg_file_bytes = bg_file.getvalue() if bg_file is not None else None
    if bg_file_bytes is not None:
        try:
            background_image = _cached_load_background_image(bg_file_bytes)
        except StoryImageError as e:
            st.error(e.friendly_message)
            with st.expander("デバッグ情報（エラー詳細）"):
                st.write(e.detail or "詳細情報はありません。")

    # 「プレビューを更新」ボタンは使わず、上のデザイン設定（背景画像・背景の
    # 暗さ・フォント・文字サイズ・文字色・文字の背景）を変更するたびに、
    # Streamlitのwidget再実行の仕組みを利用して、現在の入力値からその場で
    # Story画像を再生成する。Threads APIは呼ばず、ローカルのPillow処理のみ。
    # 実際の生成結果は_cached_story_image()が入力値ごとにキャッシュしており、
    # まったく同じ設定の組み合わせであれば再生成しない。
    current_image_bytes = None
    current_warning = None
    try:
        current_image_bytes, current_warning = _cached_story_image(
            profile_image_url=result["profile_image_url"],
            account_name=result["account_name"],
            original_text=result["original_text"],
            own_replies_tuple=tuple(result["reply_texts"]),
            bg_cache_key=_background_cache_key(bg_file_bytes),
            text_color=text_color,
            text_bg_color=text_bg_color,
            max_font_size=font_size,
            overlay_opacity=overlay_percent / 100,
            font_key=story_font_choice,
            _background_image=background_image,
        )
    except StoryImageError as e:
        st.error(e.friendly_message)
        with st.expander("デバッグ情報（エラー詳細）"):
            st.write(e.detail or "詳細情報はありません。")

    if current_image_bytes:
        if current_warning:
            st.warning(current_warning)
        st.markdown('<div class="tt-step-title">プレビュー</div>', unsafe_allow_html=True)
        st.image(current_image_bytes, use_container_width=True)
        st.download_button(
            "PNGをダウンロード",
            data=current_image_bytes,
            file_name="threads_story.png",
            mime="image/png",
            use_container_width=True,
        )

        # Instagramへの投稿にも、常にこの（現在の設定から今まさに生成した）
        # current_image_bytesを使う。古いsession_stateの画像を誤って
        # 投稿してしまうことがないようにするため。
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

    # --- 画像クリックによる選択変更を、JavaScriptからPython側へ伝えるための
    # 非表示ウィジェット。見た目には一切現れない（CSSで完全に非表示にしている。
    # carousel_ui.pyのinject_carousel_card_css参照）。
    # JS側は、この中のテキスト入力の値をページIDへ書き換えることで、
    # Streamlitの通常の再実行フローに乗せてPython側へ伝える。
    # 横スクロールだけでは、この値は変化しない（＝選択は変わらない）。
    scroll_sync_key = f"carousel_scroll_sync_{reset_id}"
    with st.container(key=scroll_sync_key):
        synced_page_id = st.text_input(
            "carousel_scroll_sync",
            key=f"{scroll_sync_key}_input",
            label_visibility="collapsed",
        )

    # --- ドラッグ＆ドロップによる並び替え結果を、JavaScriptからPython側へ
    # 伝えるための非表示ウィジェット（上のscroll_sync_keyと同じ仕組み）。
    # 値はページIDの配列をJSON文字列にしたもの。
    reorder_sync_key = f"carousel_reorder_sync_{reset_id}"
    with st.container(key=reorder_sync_key):
        synced_reorder_json = st.text_input(
            "carousel_reorder_sync",
            key=f"{reorder_sync_key}_input",
            label_visibility="collapsed",
        )

    page_ids = st.session_state.carousel_page_ids
    # 非表示のテキスト入力は、JS側から新しい値を書き込まない限りその値を
    # 保持し続ける（＝再実行のたびに同じ値を返し続ける）。そのため
    # 「現在の選択と違うら常に上書きする」実装では、ページ追加・削除・
    # Undo/Redoなどプログラム側で選択を変えた直後の再実行時に、この
    # 古い値で選択が巻き戻ってしまう。前回処理した値からの「変化」を
    # 検知したときだけ反映することで、この巻き戻りを防ぐ。
    last_synced_value = st.session_state.get("carousel_last_scroll_sync_value")
    if synced_page_id and synced_page_id != last_synced_value:
        st.session_state.carousel_last_scroll_sync_value = synced_page_id
        if synced_page_id in page_ids and synced_page_id != st.session_state.carousel_selected_id:
            # 画像クリックによる選択変更。
            # _carousel_content_signature()はselected_idを見ないため、この後の
            # _push_carousel_history()は新しいUndo履歴を作らない
            # （＝クリックによる選択変更だけではUndo履歴を消費しない）。
            st.session_state.carousel_selected_id = synced_page_id
            st.session_state.carousel_force_editor_resync = True

    # ドラッグ＆ドロップによる並び替え結果の反映。
    # scroll_sync_keyと同じ理由で「前回処理した値からの変化」だけを見る。
    last_reorder_value = st.session_state.get("carousel_last_reorder_sync_value")
    if synced_reorder_json and synced_reorder_json != last_reorder_value:
        st.session_state.carousel_last_reorder_sync_value = synced_reorder_json
        try:
            new_order = json.loads(synced_reorder_json)
        except (TypeError, ValueError):
            new_order = None
        # JS側からの値は信用しすぎず、「今のページ構成をそのまま並び替えた
        # ものである（＝要素の集合が完全に一致する）」ことを確認してから
        # 反映する。ページ追加・削除の直後にドラッグ結果が届くような
        # タイミングのずれがあっても、内容を壊さないための安全策。
        if (
            isinstance(new_order, list)
            and all(isinstance(pid, str) for pid in new_order)
            and sorted(new_order) == sorted(page_ids)
        ):
            # page_id・本文・選択状態はそのまま。並び順だけを変更する。
            # 選択中ページをドラッグした場合も、carousel_selected_id自体は
            # 変更しないため、そのページを選択したまま維持される
            # （編集欄もcarousel_selected_idが変わらない限り再同期しない）。
            st.session_state.carousel_page_ids = new_order
            page_ids = new_order

    # 選択中ページが何らかの理由でページ一覧から消えていたら、先頭ページへ戻す。
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
    # 以降のサムネイル生成・Undo履歴の判定は、すべてこの保存領域を参照する。
    if selected_id is not None:
        st.session_state.carousel_store[selected_id] = st.session_state.get(
            "carousel_active_editor", ""
        )

    # ここまでの内容（テキスト編集・デザイン変更）を反映したうえで、
    # 今回の実行で確定した状態を履歴に記録する。
    _push_carousel_history(reset_id)

    history = st.session_state.carousel_history
    history_index = st.session_state.carousel_history_index
    can_undo = history_index > 0
    can_redo = history_index < len(history) - 1
    can_delete_page = len(page_ids) > 1
    selected_index_now = page_ids.index(selected_id) if selected_id in page_ids else -1
    can_move_prev = selected_index_now > 0
    can_move_next = 0 <= selected_index_now < len(page_ids) - 1

    # ページ操作（追加・削除・元に戻す・やり直す・前後移動）を1か所の
    # 操作パネルにまとめる。horizontal=True, wrap=Trueなので、
    # PCでは横並び、画面幅が狭い場合は自然に折り返す。
    st.markdown(
        """
        <style>
        .st-key-carousel_action_panel {
            background-color: var(--tt-card-bg);
            border: 1px solid var(--tt-border);
            border-radius: 12px;
            padding: 0.8rem;
            row-gap: 0.5rem !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    with st.container(horizontal=True, wrap=True, key="carousel_action_panel"):
        add_page_clicked = st.button("ページを追加", key="carousel_add_page")
        delete_page_clicked = st.button(
            "ページを削除", key="carousel_delete_page", disabled=not can_delete_page
        )
        undo_clicked = st.button(UNDO_BUTTON_LABEL, key="carousel_undo", disabled=not can_undo)
        redo_clicked = st.button(REDO_BUTTON_LABEL, key="carousel_redo", disabled=not can_redo)
        move_prev_clicked = st.button(
            "前に移動", key="carousel_move_prev", disabled=not can_move_prev
        )
        move_next_clicked = st.button(
            "後ろに移動", key="carousel_move_next", disabled=not can_move_next
        )

    # Ctrl+Z/Ctrl+Y（Mac: Cmd+Z/Cmd+Shift+Z）でも、上と同じボタンを操作させる。
    # 同じJavaScriptの中で、画像クリックによる選択変更をscroll_sync_key経由、
    # カードのドラッグ＆ドロップによる並び替えをreorder_sync_key経由で、
    # それぞれPython側へ伝えている。
    inject_carousel_shortcuts_js(scroll_sync_key, reorder_sync_key)

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
        # このボタンの処理はcarousel_active_editor（共有テキスト編集欄）を
        # 描画するより前でst.rerun()するため、Streamlitが「今回は描画されな
        # かったウィジェット」としてその状態を破棄してしまうことがある。
        # 次の描画で必ずcarousel_storeから読み直させることで、これを防ぐ。
        st.session_state.carousel_force_editor_resync = True
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
        st.session_state.carousel_force_editor_resync = True
        st.rerun()

    if undo_clicked and can_undo:
        st.session_state.carousel_history_index = history_index - 1
        _apply_carousel_state(history[history_index - 1], reset_id)
        st.rerun()

    if redo_clicked and can_redo:
        st.session_state.carousel_history_index = history_index + 1
        _apply_carousel_state(history[history_index + 1], reset_id)
        st.rerun()

    if move_prev_clicked and can_move_prev:
        ids = list(st.session_state.carousel_page_ids)
        idx = ids.index(selected_id)
        ids[idx - 1], ids[idx] = ids[idx], ids[idx - 1]
        st.session_state.carousel_page_ids = ids
        # 選択中ページ自体（ID）は変えず、表示位置だけが1つ前へ動く。
        # carousel_active_editorを描画する前でrerunするため、次の描画で
        # 必ず（選択中ページ自身の）本文をcarousel_storeから読み直させる。
        st.session_state.carousel_force_editor_resync = True
        st.rerun()

    if move_next_clicked and can_move_next:
        ids = list(st.session_state.carousel_page_ids)
        idx = ids.index(selected_id)
        ids[idx + 1], ids[idx] = ids[idx], ids[idx + 1]
        st.session_state.carousel_page_ids = ids
        st.session_state.carousel_force_editor_resync = True
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

        font_choice_key = f"carousel_font_choice_{reset_id}"
        if font_choice_key not in st.session_state:
            st.session_state[font_choice_key] = DEFAULT_FONT_KEY
        else:
            _sanitize_font_choice_session_value(font_choice_key)
        carousel_font_choice = st.selectbox(
            "フォント（全ページ共通）", FONT_OPTIONS, key=font_choice_key
        )

        font_size_key = f"carousel_font_{reset_id}"
        if font_size_key not in st.session_state:
            st.session_state[font_size_key] = DEFAULT_MAX_FONT_SIZE
        carousel_font_size = st.slider(
            "文字サイズ（最大値・全ページ共通）",
            min_value=20,
            max_value=64,
            key=font_size_key,
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

    # 背景画像はページ数ぶん何度も読み込み直さないよう、ここで一度だけ読み込む。
    # 読み込みに失敗した場合はエラーを表示しつつ、背景なし（白背景）で
    # 各ページのプレビューは表示を続ける。EXIF回転・RGB変換自体は
    # _cached_load_background_image()がファイル内容ごとにキャッシュするため、
    # ページ選択・本文編集・並び替えなど画像に影響しない再実行では
    # 同じファイルを読み込み直さない。
    carousel_background_image = None
    carousel_bg_file_bytes = (
        carousel_bg_file.getvalue() if carousel_bg_file is not None else None
    )
    if carousel_bg_file_bytes is not None:
        try:
            carousel_background_image = _cached_load_background_image(carousel_bg_file_bytes)
        except StoryImageError as e:
            st.error(e.friendly_message)
            with st.expander("デバッグ情報（エラー詳細）"):
                st.write(e.detail or "詳細情報はありません。")

    carousel_bg_cache_key = _background_cache_key(carousel_bg_file_bytes)

    def _generate_carousel_preview(page_text):
        # 本文・背景・デザイン設定のいずれも変わっていないページは、
        # _cached_carousel_page_image()がキャッシュ済みの画像をそのまま
        # 返す（ページ選択・他ページの編集・並び替え・前後移動・削除の
        # いずれでも、影響を受けないページの画像は再生成されない）。
        return _cached_carousel_page_image(
            page_text,
            bg_cache_key=carousel_bg_cache_key,
            text_color=carousel_text_color,
            text_bg_color=carousel_text_bg_color,
            max_font_size=carousel_font_size,
            overlay_opacity=carousel_overlay_percent / 100,
            font_key=carousel_font_choice,
            _background_image=carousel_background_image,
        )

    # ここまでのボタン操作でページ構成が変わっていないことが確定した状態で描画する。
    page_ids = st.session_state.carousel_page_ids
    total_pages = len(page_ids)
    selected_id = st.session_state.carousel_selected_id

    if not page_ids:
        st.info("ページがありません。「ページを追加」から作成してください。")
    else:
        page_texts = {pid: st.session_state.carousel_store.get(pid, "") for pid in page_ids}
        selected_index = page_ids.index(selected_id)

        # --- メインプレビュー（横一列・全ページ表示） ---
        # すべてのページを同じ大きさで横一列に並べ、画面幅を超えた分は
        # 自由に横スクロールして閲覧できるようにする（選択中かどうかに
        # 関係なく、常に全ページを表示する）。
        inject_carousel_card_css(selected_id)

        selected_image_bytes = None
        selected_warning = None

        with st.container(horizontal=True, wrap=False, key="carousel_thumb_row"):
            for pid in page_ids:
                is_selected = pid == selected_id
                with st.container(key=f"carousel_card_{pid}", border=True, width="content"):
                    page_image_bytes = None
                    page_warning = None
                    try:
                        page_image_bytes, page_warning = _generate_carousel_preview(
                            page_texts.get(pid, "")
                        )
                    except StoryImageError as e:
                        if is_selected:
                            st.error(e.friendly_message)
                            with st.expander("デバッグ情報（エラー詳細）"):
                                st.write(e.detail or "詳細情報はありません。")

                    if is_selected:
                        selected_image_bytes = page_image_bytes
                        selected_warning = page_warning

                    if page_image_bytes:
                        # 画像そのものの生成サイズは1080x1350のまま。
                        # 表示サイズはCSS（carousel_ui.py）ですべてのカードで
                        # 統一する（選択位置による拡大縮小は行わない）。
                        # プレビュー画像自体をクリックすると、carousel_ui.pyの
                        # JavaScript（inject_carousel_shortcuts_js）がそのページを
                        # 選択状態にする。専用の選択ボタンは設けていない。
                        st.image(page_image_bytes, width=280)

        # 選択してもスクロール位置を勝手に変更しない（ユーザーが見ている
        # 位置をそのまま維持する）。Streamlitは再実行のたびにこの領域の
        # DOMを作り直すことがあり、そのままではブラウザ側の横スクロール
        # 位置が0へ戻ってしまうことがあるため、直前のスクロール位置を
        # 明示的に復元する（新しい位置へ移動させるものではない）。
        inject_carousel_scroll_restore_js()

        # --- 「ページ N / M を編集中」表示 ---
        # プレビュー画像そのものにはページ番号を描画していないため、
        # 現在位置を把握するためのUI表示としてここに残す。
        st.markdown(
            f'<div class="tt-step-title">ページ {selected_index + 1} / {total_pages} を編集中</div>',
            unsafe_allow_html=True,
        )

        if selected_warning:
            st.warning(selected_warning)

        if selected_image_bytes:
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
