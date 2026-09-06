"""
Threadsの投稿本文を、Gemini APIを使ってnote投稿用に改行整形するモジュール。

Geminiには「改行を追加すること」だけを許可し、
・言葉の変更、要約、言い換え
・文章の追加、削除
・句読点や記号の変更
・行の順番の変更
は一切行わせない。

さらに、Geminiの出力をそのまま信用せず、Python側で
・1行が20文字を超えていないか
・原文と文字が完全に一致しているか（改行を除いて比較）
を必ず検証する。検証に失敗した場合は例外を送出する。

Gemini APIキーは st.secrets["GEMINI_API_KEY"] から読み込み、
コード内に直接書かない。
"""

import streamlit as st
from google import genai
from google.genai import types

MODEL_NAME = "gemini-2.0-flash"
MAX_LINE_LENGTH = 20

PROMPT_TEMPLATE = """\
あなたはテキストに改行だけを追加するツールです。
以下の【原文】に対して、次のルールを厳密に守って改行を追加してください。

# 絶対に守るルール
- 原文の文字・言葉を一切変更しないでください（言い換え・要約・追加・削除は禁止です）。
- 句読点や記号も一切変更しないでください。
- 文章や行の順番を変更しないでください。
- 行ってよいのは「改行を追加すること」だけです。それ以外は1文字も変えないでください。

# 改行のルール
- 1行はできるだけ20文字以内にしてください。
- 意味のまとまりを優先して改行してください。
- 単語や助詞の途中で不自然に改行しないでください。
- 句読点だけが行の先頭や、次の行に取り残されないようにしてください。

# 出力形式
- 改行を追加したあとの文章だけを出力してください。
- 説明文・前置き・後書き・コードブロックの記号は一切不要です。
- 「元投稿」「返信」などのラベルは付けないでください。

【原文】
{text}
"""


class GeminiFormatError(Exception):
    """Gemini APIでの整形処理中に発生したエラー。"""

    def __init__(self, friendly_message, detail=None):
        super().__init__(friendly_message)
        self.friendly_message = friendly_message
        self.detail = detail


def _strip_newlines(text: str) -> str:
    return text.replace("\r\n", "").replace("\n", "").replace("\r", "")


def _get_client() -> genai.Client:
    api_key = st.secrets.get("GEMINI_API_KEY")
    if not api_key:
        raise GeminiFormatError(
            "Gemini APIキーが設定されていません。"
            "Streamlit SecretsにGEMINI_API_KEYを追加してください。"
        )
    return genai.Client(api_key=api_key)


def _call_gemini(text: str) -> str:
    client = _get_client()
    prompt = PROMPT_TEMPLATE.format(text=text)

    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.2),
        )
    except GeminiFormatError:
        raise
    except Exception as e:
        raise GeminiFormatError(
            "Gemini APIとの通信に失敗しました。しばらくしてから再度お試しください。",
            detail=str(e),
        )

    try:
        formatted = response.text
    except Exception as e:
        raise GeminiFormatError(
            "Gemini APIの応答を読み取れませんでした。",
            detail=str(e),
        )

    if not formatted:
        raise GeminiFormatError(
            "Gemini APIから空の応答が返されました。",
            detail=str(response),
        )

    return formatted.strip("\n")


def _validate_formatting(original: str, formatted: str):
    for line in formatted.split("\n"):
        if len(line) > MAX_LINE_LENGTH:
            raise GeminiFormatError(
                "文章の整形に失敗しました。再度お試しください",
                detail=f"{MAX_LINE_LENGTH}文字を超える行があります: {line!r}",
            )

    if _strip_newlines(formatted) != _strip_newlines(original):
        raise GeminiFormatError(
            "文章の整形に失敗しました。再度お試しください",
            detail=(
                "整形後の文章（改行除去後）が原文と一致しません。\n"
                f"--- 原文（改行除去） ---\n{_strip_newlines(original)}\n"
                f"--- 整形後（改行除去） ---\n{_strip_newlines(formatted)}"
            ),
        )


def _format_block(text: str) -> str:
    formatted = _call_gemini(text)
    _validate_formatting(text, formatted)
    return formatted


def format_for_note(original_text: str, own_replies: list) -> str:
    """
    元投稿本文と本人返信をそれぞれGeminiで改行整形し、
    ブロックの間に空行を入れて結合したnote投稿用の文章を返す。

    各ブロックの整形結果は、改行を除くと元の文章と完全に一致することを
    検証してから結合する。検証に失敗した場合はGeminiFormatErrorを送出する。
    """
    blocks = [b for b in [original_text] + list(own_replies) if b]

    if not blocks:
        return ""

    formatted_blocks = [_format_block(block) for block in blocks]
    result = "\n\n".join(formatted_blocks)

    # 結合後の文章全体でも、行の長さと文字の一致を最終確認する
    _validate_formatting("".join(blocks), result)

    return result
