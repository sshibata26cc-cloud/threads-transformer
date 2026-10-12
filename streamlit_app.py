import hashlib
import io
import json
import math
import uuid

import streamlit as st
from PIL import Image

from app_fonts import (
    DEFAULT_FONT_KEY,
    FONT_OPTIONS,
    JAPANESE_FALLBACK_KEY,
    browser_fallback_font,
    browser_font,
)
from app_meta import inject_mobile_meta_tags, resolve_page_icon
from carousel_generator import (
    CAROUSEL_CANVAS,
    generate_carousel_page_image,
    render_carousel_text_layer,
)
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
    upload_story_video,
    verify_image_url,
    verify_video_url,
)
from instagram_api import InstagramAPIError, get_instagram_credentials, post_story
from reply_filters import filter_own_replies
from page_editor import (
    STRUCTURE_HOST,
    STRUCTURE_LOCAL,
    background_fill_data_url,
    background_preview_data_url,
    inject_mobile_media_picker_fix,
    page_editor,
    read_editor_value,
)
from rich_text import doc_to_text, render_lines_layer, text_to_doc
from story_image import (
    BG_FIT_HEIGHT,
    BG_FIT_WIDTH,
    DEFAULT_MAX_FONT_SIZE,
    DEFAULT_TEXT_BG_COLOR,
    DEFAULT_TEXT_COLOR,
    STORY_CANVAS,
    STORY_DEFAULT_TEXT_COLOR,
    StoryImageError,
    convert_png_to_jpeg,
    generate_story_image,
    image_to_png_bytes,
    load_background_image,
    render_story_text_layer,
)
from story_video import (
    MAX_VIDEO_SECONDS,
    VIDEO_EXTENSIONS,
    is_video_filename,
    load_video_poster,
    render_story_video,
)
from styles import inject_custom_css
from threads_api import (
    ThreadsAPIError,
    find_post_by_permalink,
    get_access_token,
    get_own_replies_in_order,
    get_post_detail,
    get_post_replies,
    is_threads_url,
    resolve_target_url,
)

MODE_INSTAGRAM = "Instagram ストーリーズ 投稿用"
MODE_CAROUSEL = "Instagram カルーセル投稿用"
MODE_NOTE = "note 投稿用"

TEXT_BG_MODE_NONE = "透明"
TEXT_BG_MODE_COLOR = "色を設定"

# 「背景の暗さ」の初期値（%）。ストーリーズ・カルーセル共通。
DEFAULT_OVERLAY_PERCENT = 65

# 完成画像（右クリック / 長押しで保存する用）の表示。
SAVE_IMAGE_HINT = "画像を右クリック（スマホは長押し）すると、そのまま画像として保存できます。"
SAVE_IMAGE_WIDTH = 320

OVERFLOW_WARNING = "文章が画像に収まりきっていません。文章を減らすか、ページを分けてください。"
EDIT_HINT = (
    "画像の上の文字を、そのままクリック（スマホはタップ）して編集できます。"
    "寄せは行ごと、文字サイズは選択した文字に適用されます。"
)


def _show_saveable_image(png_bytes, key, caption=None):
    """
    完成画像を、右クリック / 長押しで保存できる通常の画像として表示する。

    st.imageにwidthを数値で渡すと、Streamlitがその幅に縮小したJPEGを配信して
    しまい、保存した画像の解像度が落ちる。そのため画像は元の解像度のPNGの
    まま配信し、画面上の表示サイズだけをCSSで小さくする。
    """
    st.markdown(
        f"""
        <style>
        [class*="st-key-tt_save_image_"] img {{
            width: {SAVE_IMAGE_WIDTH}px !important;
            max-width: 100% !important;
            height: auto !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )
    with st.container(key=f"tt_save_image_{key}"):
        st.image(png_bytes, caption=caption, output_format="PNG")

# ストーリーズ背景の差し込み方（表示名 -> story_imageの定数）。先頭がデフォルト。
BG_FIT_CHOICES = {"横いっぱい": BG_FIT_WIDTH, "縦いっぱい": BG_FIT_HEIGHT}

ACCOUNT_CHOICES = ["takuma_o369", "shin.coaching", "masa_life128"]

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
def _cached_video_poster(bg_cache_key, _video_bytes, suffix, start=0):
    """
    背景動画から位置合わせ用の静止画と動画の長さを取り出す処理をキャッシュする。
    動画本体は大きいのでハッシュ対象にせず、bg_cache_keyで区別する。
    startは使用する区間の開始位置（秒）。
    """
    return load_video_poster(_video_bytes, suffix=suffix, start=start)


def _limit_video_range(range_key):
    """
    「使用する区間」スライダーのon_changeコールバック。区間がMAX_VIDEO_SECONDS秒を
    超えたら、いま動かしていない側のつまみを追従させて上限内に収める
    （開始を動かしたら終了を、終了を動かしたら開始を寄せる）。
    """
    start, end = st.session_state[range_key]
    if end - start <= MAX_VIDEO_SECONDS:
        return
    prev_start, _ = st.session_state.get(range_key + "_prev", (start, end))
    if start != prev_start:
        end = start + MAX_VIDEO_SECONDS
    else:
        start = end - MAX_VIDEO_SECONDS
    st.session_state[range_key] = (start, end)


def _format_seconds(seconds) -> str:
    """秒数を「分:秒」（例: 1:05）の表記にする。"""
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


@st.cache_data(show_spinner=False, max_entries=5)
def _cached_preview_background(bg_cache_key, _background_image):
    """
    プレビュー表示用の背景をキャッシュする。
    戻り値: (縮小した背景のdata URL, 余白を埋めるぼかし背景のdata URL)
    """
    return (
        background_preview_data_url(_background_image),
        background_fill_data_url(_background_image),
    )


def _doc_key(doc) -> str:
    """文書（rich_text.pyの形式）を、キャッシュのキーに使える文字列にする。"""
    return json.dumps(doc, ensure_ascii=False, sort_keys=True)


@st.cache_data(show_spinner=False, max_entries=200)
def _cached_text_layer(
    kind, doc_key, lines_key, use_fallback_font, text_color, text_bg_color, max_font_size, font_key
):
    """
    1ページぶんの、文字だけを描いた透明レイヤー（PNG）をキャッシュする。
    完成画像・動画の書き出しでは、これを背景の上に重ねる。

    kindは "story" または "carousel"。
    lines_keyがあれば、編集画面（ブラウザ）が組んだ配置どおりに描く
    （編集中の見た目と一致する。通常はこちら）。lines_keyがNoneのときは、
    doc_key（_doc_key()で文字列にした文書）をPython側で組んで描く
    （配置がまだ届いていない、表示直後・設定を変えた直後だけの代わり）。

    戻り値: (PNGのバイト列, 警告メッセージ または None)
    """
    if lines_key is not None:
        canvas = STORY_CANVAS if kind == "story" else CAROUSEL_CANVAS
        layer = render_lines_layer(
            json.loads(lines_key),
            (canvas["w"], canvas["h"]),
            text_color,
            text_bg_color,
            # "Arial"で日本語を含むページは、編集画面と同じく代替フォントで描く。
            font_key=JAPANESE_FALLBACK_KEY if use_fallback_font else font_key,
        )
        return image_to_png_bytes(layer), None

    render = render_story_text_layer if kind == "story" else render_carousel_text_layer
    layer, warning = render(
        json.loads(doc_key),
        text_color=text_color,
        text_bg_color=text_bg_color,
        max_font_size=max_font_size,
        font_key=font_key,
    )
    return image_to_png_bytes(layer), warning


def _page_text_layer(kind, page, text_color, text_bg_color, max_font_size, font_key):
    """
    read_editor_value()のページ情報（"doc"と、あれば"lines"）から文字レイヤーを作る。
    戻り値: (PNGのバイト列, 警告メッセージ または None)
    """
    if page.get("lines") is not None:
        layer_png, _ = _cached_text_layer(
            kind, None, _doc_key(page["lines"]), page.get("fallback", False),
            text_color, text_bg_color, None, font_key,
        )
        warning = OVERFLOW_WARNING if page.get("overflow") else None
        return layer_png, warning
    return _cached_text_layer(
        kind, _doc_key(page["doc"]), None, False, text_color, text_bg_color, max_font_size, font_key
    )


def _layer_key(layer_png) -> str:
    return hashlib.sha256(layer_png).hexdigest()


@st.cache_data(show_spinner=False, max_entries=60)
def _cached_story_image(
    layer_key, bg_cache_key, overlay_opacity, bg_fit, bg_offset, _layer_png, _background_image
):
    """
    ストーリーズ1ページぶんの完成画像（PNG）をキャッシュする。
    文字レイヤー・背景は大きいのでハッシュ対象にせず、layer_key・bg_cache_keyで区別する。
    完成画像の表示・Instagram投稿は、この関数が返す同じ結果をそのまま使う。
    """
    return generate_story_image(
        Image.open(io.BytesIO(_layer_png)),
        background_image=_background_image,
        overlay_opacity=overlay_opacity,
        bg_fit=bg_fit,
        bg_offset=bg_offset,
    )


@st.cache_data(show_spinner=False, max_entries=80)
def _cached_carousel_page_image(
    layer_key, bg_cache_key, overlay_opacity, _layer_png, _background_image
):
    """
    カルーセル1ページぶんの完成画像（PNG）をキャッシュする。

    これにより、ページを選択しただけ・他のページを編集しただけ・
    ページを並び替えただけ・前後移動しただけ・ページを削除しただけ、
    といった「そのページ自身の見た目には影響しない操作」では、
    各ページの画像を再生成せずに済む（キャッシュキーが変わらないため）。
    """
    return generate_carousel_page_image(
        Image.open(io.BytesIO(_layer_png)),
        background_image=_background_image,
        overlay_opacity=overlay_opacity,
    )


def _sanitize_font_choice_session_value(key: str) -> None:
    """
    st.selectbox()は、session_stateに既にkeyの値が入っている場合、それが
    optionsに含まれていないとエラーになる。フォントの選択肢を変更・削除
    したときに、過去のsession_state（例: 削除済みの「教科書体」「MS P明朝
    標準」や、以前のデフォルトだった「ゴシック体」）が残っていてもエラーに
    ならないよう、該当キーの値が現在のFONT_OPTIONSに含まれているかを描画前に
    確認し、含まれていなければ現在のデフォルト（游明朝体）へ差し替える。
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
        "overlay_percent": DEFAULT_OVERLAY_PERCENT,
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
        "texts": {pid: st.session_state.carousel_store.get(pid, []) for pid in page_ids},
        "selected_id": st.session_state.carousel_selected_id,
        "design": _current_carousel_design(reset_id),
    }


def _apply_carousel_state(state, reset_id):
    """
    Undo/Redoで選んだスナップショットの内容を、実際のセッション状態へ書き戻す。

    本文（文書）をPython側で差し替えるので、編集欄がそれを読み込み直すよう
    carousel_doc_revを進める（page_editor.pyを参照）。
    """
    st.session_state.carousel_page_ids = list(state["page_ids"])
    st.session_state.carousel_store = dict(state["texts"])
    st.session_state.carousel_selected_id = state["selected_id"]
    st.session_state.carousel_doc_rev += 1

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

    各ページにUUIDの内部IDを割り当て、その本文を書式なしの文書（rich_text.pyの
    形式）にして carousel_store（プレーンなdict）へ保存する。あわせて、Undo/Redo
    履歴もこの状態を起点として作り直す
    （別のThreads投稿を新しく変換したときだけ、ここが呼ばれる）。
    """
    new_ids = [_new_carousel_page_id() for _ in pages_text]
    pages_doc = [text_to_doc(text) for text in pages_text]
    st.session_state.carousel_page_ids = new_ids
    st.session_state.carousel_store = dict(zip(new_ids, pages_doc))
    st.session_state.carousel_selected_id = new_ids[0] if new_ids else None

    initial_state = {
        "page_ids": list(new_ids),
        "texts": dict(zip(new_ids, pages_doc)),
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
if "story_video_export" not in st.session_state:
    # 書き出し済みのストーリーズ動画 {"signature": 設定の識別子, "videos": ページ順のMP4}。
    st.session_state.story_video_export = None
if "story_edit" not in st.session_state:
    # ストーリーズの文章の編集状態（ストーリーズデザイン欄で初期化する）。
    st.session_state.story_edit = None
if "carousel_page_ids" not in st.session_state:
    st.session_state.carousel_page_ids = []
if "carousel_store" not in st.session_state:
    # ページID -> 本文（rich_text.pyの文書形式） のプレーンなdict。
    # 編集欄（コンポーネント）は選択中の1ページぶんしか描画しないため、
    # 選択中でないページの本文もここに保持しておく。
    st.session_state.carousel_store = {}
if "carousel_doc_rev" not in st.session_state:
    # Python側で本文を差し替える（Undo/Redo）たびに進める番号。
    st.session_state.carousel_doc_rev = 0
if "carousel_selected_id" not in st.session_state:
    st.session_state.carousel_selected_id = None
if "carousel_history" not in st.session_state:
    st.session_state.carousel_history = []
if "carousel_history_index" not in st.session_state:
    st.session_state.carousel_history_index = 0

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
                    own_replies = []

                    if post_summary:
                        post = get_post_detail(post_summary["id"], access_token)
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
                    original_text = post.get("text", "")
                    reply_texts = [r.get("text", "") for r in own_replies]

                    st.session_state.result = {
                        "mode": mode,
                        "account_label": selected_account,
                        "account_name": account_name,
                        "original_text": original_text,
                        "reply_texts": reply_texts,
                    }
                    st.session_state.design_reset_id += 1
                    st.session_state.ig_post_confirm_pending = False
                    st.session_state.story_video_export = None

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
            "背景の画像・動画を選択（未指定の場合は黒背景を使用します）",
            type=["png", "jpg", "jpeg", "webp", *VIDEO_EXTENSIONS],
            key=f"story_bg_{reset_id}",
        )
        inject_mobile_media_picker_fix()
        bg_fit_label = st.radio(
            "背景のサイズ",
            list(BG_FIT_CHOICES),
            index=0,
            horizontal=True,
            key=f"story_bg_fit_{reset_id}",
            help="位置は、下のプレビューで背景をドラッグ（スマホではスライド）して調整できます。",
        )
        bg_fit = BG_FIT_CHOICES[bg_fit_label]
        overlay_percent = st.slider(
            "背景の暗さ",
            min_value=0,
            max_value=80,
            value=DEFAULT_OVERLAY_PERCENT,
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
            value=STORY_DEFAULT_TEXT_COLOR,
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

    # 背景はここで一度だけ読み込む。動画の場合は、位置合わせ用の静止画を
    # 1枚取り出して画像背景と同じように扱い、動画への合成は「動画を書き出す」
    # を押したときだけ行う（デザイン設定を変えるたびに書き出すと重いため）。
    background_image = None
    bg_is_video = False
    bg_cache_key = None
    bg_file_bytes = bg_file.getvalue() if bg_file is not None else None
    bg_suffix = ""
    video_start = 0  # 動画のうち使用する区間の開始位置（秒）
    video_end = MAX_VIDEO_SECONDS  # 同じく終了位置（秒）
    preview_bg_key = None
    if bg_file_bytes is not None:
        bg_is_video = is_video_filename(bg_file.name)
        bg_suffix = "." + bg_file.name.rsplit(".", 1)[-1].lower()
        try:
            if bg_is_video:
                # 動画は大きいので中身のハッシュは取らず、アップロードごとに
                # 一意なfile_idでキャッシュを区別する。
                bg_cache_key = f"video:{bg_file.file_id}"
                background_image, video_duration = _cached_video_poster(
                    bg_cache_key, bg_file_bytes, bg_suffix
                )
                # 動画のうち使用する区間（開始〜終了）をここで選んでもらう。
                # 長さの上限はMAX_VIDEO_SECONDS秒。選んだ開始位置のコマを
                # プレビューに表示し、書き出しもその区間だけを行う。
                video_total = math.ceil(video_duration or 0)
                if video_total >= 1:
                    range_key = f"story_video_range_{reset_id}_{bg_file.file_id}"
                    st.markdown(
                        '<div class="tt-step-title">動画の使用区間</div>',
                        unsafe_allow_html=True,
                    )
                    with st.container(border=True):
                        st.write(
                            f"この動画は{_format_seconds(video_total)}あります。"
                            f"使用する区間の開始位置と終了位置を選んでください"
                            f"（最長{MAX_VIDEO_SECONDS}秒）。"
                        )
                        video_start, video_end = st.select_slider(
                            "使用する区間",
                            options=list(range(video_total + 1)),
                            value=(0, min(video_total, MAX_VIDEO_SECONDS)),
                            format_func=_format_seconds,
                            key=range_key,
                            on_change=_limit_video_range,
                            args=(range_key,),
                        )
                        st.session_state[range_key + "_prev"] = (video_start, video_end)
                        if video_end <= video_start:
                            # 開始と終了が同じ位置のときは、最低1秒ぶんを使う。
                            video_end = video_start + 1
                        st.caption(
                            f"使用する区間: {_format_seconds(video_start)} 〜 "
                            f"{_format_seconds(video_end)}"
                            f"（{video_end - video_start}秒間）"
                        )
                        with st.expander("選んだ区間を再生して確認する"):
                            st.video(
                                bg_file_bytes,
                                start_time=video_start,
                                end_time=video_end,
                            )
                    if video_start:
                        background_image, _ = _cached_video_poster(
                            bg_cache_key, bg_file_bytes, bg_suffix, start=video_start
                        )
                preview_bg_key = f"{bg_cache_key}@{video_start}"
            else:
                bg_cache_key = _background_cache_key(bg_file_bytes)
                preview_bg_key = bg_cache_key
                background_image = _cached_load_background_image(bg_file_bytes)
        except StoryImageError as e:
            bg_is_video = False
            st.error(e.friendly_message)
            with st.expander("デバッグ情報（エラー詳細）"):
                st.write(e.detail or "詳細情報はありません。")

    # --- 編集画面（プレビュー上で直接編集） ---
    # 文章は「書式つきの文書」（rich_text.py）をページごとに持つ。変換し直すたび
    # （reset_idが変わるたび）に、Threadsの本文と返信を空行でつないだ、書式なしの
    # 1ページへ戻す。改ページ・結合は編集画面の中で行われ、その結果のページ構成が
    # そのまま返ってくる。revは、Python側で文書を作り直すたびに進める番号
    # （編集画面がそれを見て読み込み直す。page_editor.pyを参照）。
    def _initial_story_pages():
        text = "\n\n".join([result["original_text"]] + result["reply_texts"])
        return [{"id": "p0", "doc": text_to_doc(text)}]

    story_edit = st.session_state.story_edit
    if story_edit is None or story_edit["reset_id"] != reset_id:
        story_edit = {"reset_id": reset_id, "pages": _initial_story_pages(), "rev": 0}
        st.session_state.story_edit = story_edit

    # 組版（折り返し・文字の位置）に影響する設定の識別子。編集画面から返ってきた
    # 配置が、いまの設定で組まれたものかどうかの判定に使う。
    story_style_key = f"{story_font_choice}:{font_size}"
    bg_token = f"{bg_cache_key}:{bg_fit}"
    story_background = None
    if background_image is not None:
        preview_bg_url, preview_fill_url = _cached_preview_background(
            preview_bg_key, background_image
        )
        story_background = {
            "url": preview_bg_url,
            "fill": preview_fill_url,
            "w": background_image.width,
            "h": background_image.height,
            "fit": bg_fit,
            "shade": overlay_percent / 100,
            "token": bg_token,
        }

    st.markdown('<div class="tt-step-title">プレビュー・編集</div>', unsafe_allow_html=True)
    st.caption(
        EDIT_HINT + "「改ページ」を押すと、カーソルから後ろが次の画像（2枚目以降）になります。"
    )
    story_edited = read_editor_value(
        page_editor(
            story_edit["pages"],
            rev=story_edit["rev"],
            style={
                "key": story_style_key,
                "font": browser_font(story_font_choice),
                "fallback": browser_fallback_font(story_font_choice),
                "base": font_size,
                "color": text_color,
                "band": text_bg_color,
            },
            canvas=STORY_CANVAS,
            key=f"story_page_editor_{reset_id}",
            background=story_background,
            structure=STRUCTURE_LOCAL,
        ),
        story_edit["rev"],
        story_style_key,
        STORY_CANVAS,
    )
    bg_offset = (0, 0)
    if story_edited and story_edited["pages"]:
        story_edit["pages"] = [
            {"id": page["id"], "doc": page["doc"]} for page in story_edited["pages"]
        ]
        story_page_infos = story_edited["pages"]
        # 別の背景・別の差し込み方のときに返ってきた古い位置は使わない。
        if story_edited["offset"][2] == bg_token:
            bg_offset = story_edited["offset"][:2]
    else:
        story_page_infos = story_edit["pages"]

    if st.button("文章を最初の状態に戻す", key=f"story_doc_reset_{reset_id}"):
        story_edit["pages"] = _initial_story_pages()
        story_edit["rev"] += 1
        st.rerun()

    # 文字のないページ（改ページしただけで何も書いていないページ）は、画像にしない。
    story_output_pages = [
        page for page in story_page_infos if doc_to_text(page["doc"]).strip()
    ] or story_page_infos[:1]
    story_page_count = len(story_output_pages)

    # 文字レイヤーは、編集画面が組んだ配置どおりにここで描く（Threads APIは呼ばず、
    # ローカルのPillow処理のみ）。背景を動かした位置（bg_offset）も書き出しに
    # そのまま使われる。
    page_layers = []  # ページごとの (文字レイヤーPNG, 警告)
    try:
        for story_page in story_output_pages:
            page_layers.append(
                _page_text_layer(
                    "story", story_page, text_color, text_bg_color, font_size, story_font_choice
                )
            )
    except StoryImageError as e:
        page_layers = []
        st.error(e.friendly_message)
        with st.expander("デバッグ情報（エラー詳細）"):
            st.write(e.detail or "詳細情報はありません。")

    # 投稿・保存の対象（画像ならPNG、動画なら書き出し済みのMP4）をページ順に並べたもの。
    post_media_list = []
    post_media_label = "動画" if bg_is_video else "画像"

    if page_layers:
        for number, (_, page_warning) in enumerate(page_layers, start=1):
            if page_warning:
                st.warning(
                    (f"{number}枚目: " if story_page_count > 1 else "") + page_warning
                )

        def _page_label(number):
            """複数ページのときだけ「N枚目」を付ける。"""
            return f"{number}枚目" if story_page_count > 1 else None

        if bg_is_video:
            st.caption(
                "上の画面は動画の1コマを表示しています。"
                "文章と位置を決めたら「動画を書き出す」を押してください。"
            )
            video_signature = hashlib.sha256(
                repr(
                    (
                        bg_cache_key,
                        [hashlib.sha256(layer_png).hexdigest() for layer_png, _ in page_layers],
                        bg_fit,
                        bg_offset,
                        overlay_percent,
                        video_start,
                        video_end,
                    )
                ).encode()
            ).hexdigest()

            if st.button("動画を書き出す", key="story_video_export_button", use_container_width=True):
                try:
                    exported = []
                    for number, (layer_png, _) in enumerate(page_layers, start=1):
                        with st.spinner(
                            f"動画を書き出しています（{number} / {story_page_count}枚目。"
                            "動画の長さによっては数分かかります）..."
                        ):
                            exported.append(
                                render_story_video(
                                    bg_file_bytes,
                                    layer_png,
                                    background_image.size,
                                    bg_fit=bg_fit,
                                    bg_offset=bg_offset,
                                    overlay_opacity=overlay_percent / 100,
                                    suffix=bg_suffix,
                                    start=video_start,
                                    duration=video_end - video_start,
                                )
                            )
                    st.session_state.story_video_export = {
                        "signature": video_signature,
                        "videos": exported,
                    }
                except StoryImageError as e:
                    st.error(e.friendly_message)
                    with st.expander("デバッグ情報（エラー詳細）"):
                        st.code(e.detail or "詳細情報はありません。")

            video_export = st.session_state.story_video_export
            if video_export and video_export["signature"] == video_signature:
                post_media_list = video_export["videos"]
                for number, video_bytes in enumerate(post_media_list, start=1):
                    if _page_label(number):
                        st.caption(_page_label(number))
                    st.video(video_bytes)
                    st.download_button(
                        "MP4をダウンロード"
                        + (f"（{_page_label(number)}）" if _page_label(number) else ""),
                        data=video_bytes,
                        file_name=(
                            f"threads_story_{number}.mp4"
                            if story_page_count > 1
                            else "threads_story.mp4"
                        ),
                        mime="video/mp4",
                        use_container_width=True,
                        key=f"story_video_download_{number}",
                    )
            elif video_export:
                st.info("設定が変更されました。もう一度「動画を書き出す」を押してください。")
        else:
            try:
                for layer_png, _ in page_layers:
                    post_media_list.append(
                        _cached_story_image(
                            _layer_key(layer_png),
                            bg_cache_key=bg_cache_key,
                            overlay_opacity=overlay_percent / 100,
                            bg_fit=bg_fit,
                            bg_offset=bg_offset,
                            _layer_png=layer_png,
                            _background_image=background_image,
                        )
                    )
            except StoryImageError as e:
                post_media_list = []
                st.error(e.friendly_message)
                with st.expander("デバッグ情報（エラー詳細）"):
                    st.write(e.detail or "詳細情報はありません。")
            else:
                # 完成画像は通常の画像として並べ、右クリック（スマホは長押し）で
                # そのまま保存できるようにする。上の編集画面は文字・背景を重ねた
                # 表示で、1枚の画像としては保存できないため、別に用意している。
                # 画面を増やさないよう、普段は折りたたんでおく。
                with st.expander(f"画像を保存する（全{story_page_count}枚）"):
                    st.caption(SAVE_IMAGE_HINT)
                    for number, page_png in enumerate(post_media_list, start=1):
                        _show_saveable_image(
                            page_png, key=f"story_{number}", caption=_page_label(number)
                        )

    if post_media_list:
        # Instagramへの投稿にも、常にこの（現在の設定から書き出した）
        # post_media_listを使う。古い設定の画像・動画を誤って
        # 投稿してしまうことがないようにするため。
        post_media_count = len(post_media_list)
        post_media_unit = (
            f"{post_media_label}{post_media_count}枚" if post_media_count > 1 else post_media_label
        )
        current_image_hash = hashlib.sha256(
            "".join(hashlib.sha256(media).hexdigest() for media in post_media_list).encode()
        ).hexdigest()
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
                        "公開URL取得テスト:",
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
                        f"この{post_media_unit}は既にInstagramへ投稿済みです。"
                        f"同じ{post_media_label}を再投稿する場合のみ「投稿する」を押してください。"
                    )
                st.warning(
                    f"この{post_media_unit}を「{result['account_label']}」の"
                    "Instagramストーリーズへ投稿しますか？"
                    + ("（1枚目から順に投稿します）" if post_media_count > 1 else "")
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
                    posted_count = 0  # 投稿まで完了した枚数（途中で失敗したときの案内用）
                    try:
                        for number, media_bytes in enumerate(post_media_list, start=1):
                            with st.spinner(
                                "Instagramへ投稿しています"
                                + (f"（{number} / {post_media_count}枚目）" if post_media_count > 1 else "")
                                + "..."
                            ):
                                if bg_is_video:
                                    media_url, public_id = upload_story_video(media_bytes)
                                else:
                                    jpeg_bytes = convert_png_to_jpeg(media_bytes)
                                    media_url, public_id = upload_story_image(jpeg_bytes)
                                debug_info["cloudinary_url"] = media_url

                                # Instagramに渡す前に、公開URLが実際に取得できる
                                # 正常なファイルになっているかを確認する（CDN反映待ちを考慮）。
                                verify_result = (
                                    verify_video_url(media_url)
                                    if bg_is_video
                                    else verify_image_url(media_url)
                                )
                                debug_info["verify_result"] = verify_result

                                post_story(
                                    ig_credentials["ig_user_id"],
                                    ig_credentials["access_token"],
                                    media_url,
                                    trace=ig_trace,
                                    is_video=bg_is_video,
                                )
                                # Instagram側でのメディアコンテナ作成・公開が成功した後にのみ、
                                # Cloudinaryの一時ファイルを削除する。削除に失敗しても
                                # 投稿自体は成功として扱う。
                                delete_story_image(
                                    public_id, resource_type="video" if bg_is_video else "image"
                                )
                                posted_count += 1
                    except (CloudinaryError, InstagramAPIError) as e:
                        st.session_state.ig_post_error = {
                            "message": e.friendly_message,
                            "detail": e.detail,
                        }
                    except Exception as e:
                        st.session_state.ig_post_error = {
                            "message": f"投稿用{post_media_label}の準備に失敗しました。もう一度お試しください。",
                            "detail": str(e),
                        }
                    else:
                        st.session_state.last_posted_image_hash = current_image_hash
                        st.session_state.ig_post_success_message = (
                            "Instagramストーリーズへの投稿が完了しました。"
                            + (f"（{post_media_count}枚）" if post_media_count > 1 else "")
                        )
                    finally:
                        st.session_state.ig_post_debug_info = debug_info
                        if st.session_state.ig_post_error and post_media_count > 1:
                            # 複数枚の途中で失敗した場合、どこまで投稿されたかを伝える
                            # （そのまま「投稿する」をやり直すと、投稿済みのぶんが重複するため）。
                            st.session_state.ig_post_error["message"] += (
                                f"（{post_media_count}枚中{posted_count}枚は投稿済みです。"
                                "もう一度投稿すると、投稿済みのぶんも重ねて投稿されます）"
                            )

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
            # 変更しないため、そのページを選択したまま維持される。
            st.session_state.carousel_page_ids = new_order
            page_ids = new_order

    # 選択中ページが何らかの理由でページ一覧から消えていたら、先頭ページへ戻す。
    if page_ids and st.session_state.carousel_selected_id not in page_ids:
        st.session_state.carousel_selected_id = page_ids[0]
    selected_id = st.session_state.carousel_selected_id

    # --- 編集画面の内容をcarousel_storeへ反映 ---
    # 編集画面（プレビュー上で直接編集）は、このブロックの下の方で描画している。
    # その最新の編集内容をここで先に読み取り、保存領域へ書き戻す。以降の
    # サムネイル生成・Undo履歴の判定は、すべてこの保存領域を参照する。
    # Undo/Redoの直後に残っている古い編集内容は、carousel_doc_revが違うことで
    # 捨てられる。
    carousel_editor_key = f"carousel_page_editor_{reset_id}"
    design_now = _current_carousel_design(reset_id)
    # 組版（折り返し・文字の位置）に影響する設定の識別子。
    carousel_style_key = f"{design_now['font_choice']}:{design_now['font_size']}"
    carousel_page_infos = {}  # ページID -> 編集画面が組んだ配置などの情報
    carousel_edited = read_editor_value(
        st.session_state.get(carousel_editor_key),
        st.session_state.carousel_doc_rev,
        carousel_style_key,
        CAROUSEL_CANVAS,
    )
    if carousel_edited:
        for page in carousel_edited["pages"]:
            if page["id"] in st.session_state.carousel_store:
                st.session_state.carousel_store[page["id"]] = page["doc"]
                carousel_page_infos[page["id"]] = page

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
        st.session_state.carousel_store[new_id] = text_to_doc("")
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

    if move_prev_clicked and can_move_prev:
        ids = list(st.session_state.carousel_page_ids)
        idx = ids.index(selected_id)
        ids[idx - 1], ids[idx] = ids[idx], ids[idx - 1]
        st.session_state.carousel_page_ids = ids
        # 選択中ページ自体（ID）は変えず、表示位置だけが1つ前へ動く。
        st.rerun()

    if move_next_clicked and can_move_next:
        ids = list(st.session_state.carousel_page_ids)
        idx = ids.index(selected_id)
        ids[idx + 1], ids[idx] = ids[idx], ids[idx + 1]
        st.session_state.carousel_page_ids = ids
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
            st.session_state[overlay_key] = DEFAULT_OVERLAY_PERCENT
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

    def _generate_carousel_preview(pid, page_doc):
        # 文字レイヤーは、編集画面が組んだ配置があればそのとおりに描く（まだ届いて
        # いない新しいページなどは、Python側で組んだもので代用する）。
        # 本文・背景・デザイン設定のいずれも変わっていないページは、キャッシュ済みの
        # 画像をそのまま返す（ページ選択・他ページの編集・並び替え・前後移動・削除の
        # いずれでも、影響を受けないページの画像は再生成されない）。
        info = carousel_page_infos.get(pid)
        if info is None or info["doc"] != page_doc:
            info = {"doc": page_doc}
        layer_png, warning = _page_text_layer(
            "carousel",
            info,
            carousel_text_color,
            carousel_text_bg_color,
            carousel_font_size,
            carousel_font_choice,
        )
        page_png = _cached_carousel_page_image(
            _layer_key(layer_png),
            bg_cache_key=carousel_bg_cache_key,
            overlay_opacity=carousel_overlay_percent / 100,
            _layer_png=layer_png,
            _background_image=carousel_background_image,
        )
        return page_png, warning

    # ここまでのボタン操作でページ構成が変わっていないことが確定した状態で描画する。
    page_ids = st.session_state.carousel_page_ids
    total_pages = len(page_ids)
    selected_id = st.session_state.carousel_selected_id

    if not page_ids:
        st.info("ページがありません。「ページを追加」から作成してください。")
    else:
        page_docs = {pid: st.session_state.carousel_store.get(pid, []) for pid in page_ids}
        selected_index = page_ids.index(selected_id)

        # --- メインプレビュー（横一列・全ページ表示） ---
        # すべてのページを同じ大きさで横一列に並べ、画面幅を超えた分は
        # 自由に横スクロールして閲覧できるようにする（選択中かどうかに
        # 関係なく、常に全ページを表示する）。
        inject_carousel_card_css(selected_id)

        selected_warning = None
        all_page_images = []  # 保存用の一覧に並べる (ページ番号, PNG)

        with st.container(horizontal=True, wrap=False, key="carousel_thumb_row"):
            for pid in page_ids:
                is_selected = pid == selected_id
                with st.container(key=f"carousel_card_{pid}", border=True, width="content"):
                    page_image_bytes = None
                    page_warning = None
                    try:
                        page_image_bytes, page_warning = _generate_carousel_preview(
                            pid, page_docs.get(pid, [])
                        )
                    except StoryImageError as e:
                        if is_selected:
                            st.error(e.friendly_message)
                            with st.expander("デバッグ情報（エラー詳細）"):
                                st.write(e.detail or "詳細情報はありません。")

                    if is_selected:
                        selected_warning = page_warning
                    if page_image_bytes:
                        all_page_images.append((page_ids.index(pid) + 1, page_image_bytes))

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

        # 選択中ページの編集画面。画像の上の文字をそのまま編集できる。
        # ページの追加・削除・並び替えは上の操作パネルとカードで行い、ここは
        # 全ページの文章を預かって、選択中の1ページだけを表示・編集する。
        # 編集内容は、次の再実行の冒頭（このブロックの上の方）でcarousel_storeへ
        # 反映している。
        st.caption(EDIT_HINT)
        carousel_background = None
        if carousel_background_image is not None:
            carousel_background = {
                "url": _cached_preview_background(
                    carousel_bg_cache_key, carousel_background_image
                )[0],
                "fill": None,
                "w": carousel_background_image.width,
                "h": carousel_background_image.height,
                "fit": "cover",
                "shade": carousel_overlay_percent / 100,
                "token": carousel_bg_cache_key,
            }
        page_editor(
            [{"id": pid, "doc": page_docs[pid]} for pid in page_ids],
            rev=st.session_state.carousel_doc_rev,
            style={
                "key": f"{carousel_font_choice}:{carousel_font_size}",
                "font": browser_font(carousel_font_choice),
                "fallback": browser_fallback_font(carousel_font_choice),
                "base": carousel_font_size,
                "color": carousel_text_color,
                "band": carousel_text_bg_color,
            },
            canvas=CAROUSEL_CANVAS,
            key=carousel_editor_key,
            background=carousel_background,
            structure=STRUCTURE_HOST,
            current=selected_id,
        )

        # 完成画像は通常の画像として全ページぶん並べ、右クリック（スマホは長押し）で
        # そのまま保存できるようにする。上のカードは長押しが並び替えのドラッグに
        # 使われ、編集画面は1枚の画像ではないため、保存用は別に用意している。
        if all_page_images:
            with st.expander(f"画像を保存する（全{len(all_page_images)}ページ）"):
                st.caption(SAVE_IMAGE_HINT)
                for page_number, page_png in all_page_images:
                    _show_saveable_image(
                        page_png, key=f"carousel_{page_number}", caption=f"ページ {page_number}"
                    )

elif result and result["mode"] == MODE_NOTE:
    st.markdown('<div class="tt-step-title">note投稿用テキスト</div>', unsafe_allow_html=True)
    st.text_area(
        "コピーしてお使いください（自由に編集できます）",
        height=600,
        key="note_text",
        label_visibility="collapsed",
    )
