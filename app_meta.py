"""
ブラウザタブの表示（タイトル・アイコン）と、
iPhoneのSafariで「ホーム画面に追加」したときの表示名・アイコン・
テーマカラーを設定するためのモジュール。

st.set_page_config() だけでは設定できない項目
（apple-mobile-web-app-title など）は、st.components.v1.html() を使って
ページの<head>に直接メタタグを追加することで対応している。

assets/icon.png や assets/apple-touch-icon.png が存在しない場合でも、
アプリがエラーにならないようにしている（存在しなければ何もしない、
または絵文字にフォールバックする）。
"""

import base64
import os

import streamlit.components.v1 as components

ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets")
ICON_PATH = os.path.join(ASSETS_DIR, "icon.png")
APPLE_TOUCH_ICON_PATH = os.path.join(ASSETS_DIR, "apple-touch-icon.png")

APP_TITLE = "Threads XC"
DEFAULT_PAGE_ICON = "🧵"
THEME_COLOR = "#A9673F"


def resolve_page_icon():
    """
    st.set_page_config()のpage_iconに渡す値を返す。

    assets/icon.png が存在すればそのファイルパスを、
    存在しなければ従来通り絵文字を返す（存在しなくてもエラーにしない）。
    """
    if os.path.isfile(ICON_PATH):
        return ICON_PATH
    return DEFAULT_PAGE_ICON


def _load_apple_touch_icon_data_uri():
    """
    assets/apple-touch-icon.png をBase64のdata URIとして読み込む。
    ファイルが存在しない・読み込めない場合はNoneを返す。
    """
    if not os.path.isfile(APPLE_TOUCH_ICON_PATH):
        return None
    try:
        with open(APPLE_TOUCH_ICON_PATH, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("ascii")
        return f"data:image/png;base64,{encoded}"
    except OSError:
        return None


def inject_mobile_meta_tags():
    """
    iPhoneのSafariで「ホーム画面に追加」した際の表示名・アイコン・
    テーマカラーなどを設定する。

    Streamlitの画面はページ内のiframeとして描画されるため、
    window.parent.document を使って、外側のページの<head>に
    直接タグを追加している（同一オリジンのため実行可能）。
    """
    apple_icon_data_uri = _load_apple_touch_icon_data_uri()
    apple_icon_script = ""
    if apple_icon_data_uri:
        apple_icon_script = f"""
        var existingIcon = head.querySelector('link[rel="apple-touch-icon"]');
        if (existingIcon) {{ existingIcon.remove(); }}
        var iconLink = document.createElement('link');
        iconLink.setAttribute('rel', 'apple-touch-icon');
        iconLink.setAttribute('href', '{apple_icon_data_uri}');
        head.appendChild(iconLink);
        """

    script = f"""
    <script>
        var head = window.parent.document.querySelector('head');

        function setMetaTag(name, content) {{
            var existing = head.querySelector('meta[name="' + name + '"]');
            if (existing) {{
                existing.setAttribute('content', content);
                return;
            }}
            var meta = document.createElement('meta');
            meta.setAttribute('name', name);
            meta.setAttribute('content', content);
            head.appendChild(meta);
        }}

        setMetaTag('apple-mobile-web-app-title', '{APP_TITLE}');
        setMetaTag('apple-mobile-web-app-capable', 'yes');
        setMetaTag('mobile-web-app-capable', 'yes');
        setMetaTag('theme-color', '{THEME_COLOR}');

        {apple_icon_script}
    </script>
    """
    components.html(script, height=0)
