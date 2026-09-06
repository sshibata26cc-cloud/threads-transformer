"""
Threads Transformerの見た目を、落ち着いたカフェのような雰囲気に整えるための
CSSをまとめたモジュール。

配色や文字サイズなどのアクセント的な部分をここに集約し、
streamlit_app.py側からinject_custom_css()を呼び出すだけで
全ページに適用できるようにしている。

色そのものの基本テーマ（ボタンやラジオボタンの主要色など）は
.streamlit/config.toml のtheme設定で管理し、
ここではレイアウト・余白・角丸・フォントなど、
config.tomlだけでは調整できない部分を補っている。
"""

import streamlit as st

CUSTOM_CSS = """
<style>
:root {
    --tt-bg: #F6F1E6;
    --tt-bg-soft: #FBF6EC;
    --tt-card-bg: #FFFDF8;
    --tt-text: #3B2E27;
    --tt-text-muted: #8A7768;
    --tt-accent: #A9673F;
    --tt-accent-hover: #8C5330;
    --tt-border: #E7DAC5;
}

[data-testid="stAppViewContainer"] {
    background-color: var(--tt-bg);
}

[data-testid="stHeader"] {
    background-color: transparent;
}

#MainMenu, footer {
    visibility: hidden;
}

.block-container {
    max-width: 720px;
    padding-top: 2.5rem;
    padding-bottom: 4rem;
}

html, body {
    color: var(--tt-text);
}

[data-testid="stWidgetLabel"] p {
    color: var(--tt-text);
}

/* ブランドヘッダー */
.tt-header {
    text-align: center;
    margin-bottom: 2.2rem;
}
.tt-brand {
    font-family: Georgia, "Hiragino Mincho ProN", "Yu Mincho", serif;
    font-size: 2.3rem;
    letter-spacing: 0.03em;
    color: var(--tt-text);
    margin-bottom: 0.3rem;
}
.tt-tagline {
    font-size: 1.05rem;
    color: var(--tt-accent);
    margin-bottom: 0.7rem;
}
.tt-description {
    font-size: 0.9rem;
    color: var(--tt-text-muted);
    line-height: 1.8;
}

/* ステップ見出し */
.tt-step-title {
    font-size: 1.02rem;
    font-weight: 600;
    color: var(--tt-text);
    margin-top: 2rem;
    margin-bottom: 0.7rem;
    padding-bottom: 0.4rem;
    border-bottom: 1px solid var(--tt-border);
}

/* st.container(border=True) をカード風に */
[data-testid="stVerticalBlockBorderWrapper"] {
    background-color: var(--tt-card-bg);
    border: 1px solid var(--tt-border) !important;
    border-radius: 14px !important;
}

/* 入力欄 */
.stTextInput input, .stTextArea textarea {
    background-color: var(--tt-bg-soft) !important;
    border: 1px solid var(--tt-border) !important;
    border-radius: 10px !important;
    color: var(--tt-text) !important;
}
.stTextInput input:focus, .stTextArea textarea:focus {
    border-color: var(--tt-accent) !important;
    box-shadow: 0 0 0 1px var(--tt-accent) !important;
}

.stTextArea textarea {
    font-size: 1.05rem !important;
    line-height: 1.9 !important;
    padding: 1.1rem !important;
}

.stSelectbox div[data-baseweb="select"] > div {
    background-color: var(--tt-bg-soft) !important;
    border-color: var(--tt-border) !important;
    border-radius: 10px !important;
}

/* ボタン */
.stButton > button, .stDownloadButton > button {
    background-color: var(--tt-accent);
    color: #FFF8F0;
    border: none;
    border-radius: 999px;
    padding: 0.6rem 1.6rem;
    font-weight: 600;
    letter-spacing: 0.02em;
    transition: background-color 0.2s ease;
}
.stButton > button:hover, .stDownloadButton > button:hover {
    background-color: var(--tt-accent-hover);
    color: #FFF8F0;
}
.stButton > button:focus, .stDownloadButton > button:focus {
    box-shadow: 0 0 0 2px var(--tt-accent-hover) !important;
}

/* アラート */
.stAlert {
    border-radius: 10px;
}

/* スマートフォン対応 */
@media (max-width: 600px) {
    .block-container {
        padding-left: 1.1rem;
        padding-right: 1.1rem;
    }
    .tt-brand {
        font-size: 1.7rem;
    }
    .stButton > button, .stDownloadButton > button {
        width: 100%;
    }
}
</style>
"""


def inject_custom_css():
    """カフェ風デザイン用のCSSをページに適用する。"""
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)
