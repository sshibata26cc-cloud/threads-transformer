"""
Streamlit Community Cloud上のアプリが休止しないよう、定期的にブラウザで開く。

GitHub Actions（.github/workflows/keep-awake.yml）から数時間おきに実行される。
Community Cloudは一定時間（現在は約12時間）閲覧がないとアプリを休止させるため、
ヘッドレスのブラウザで実際にアプリを開いて「閲覧があった」状態にする。
すでに休止していた場合は、表示される起動ボタン
（"Yes, get this app back up!"）を押して起こす。

アプリが表示できなかった場合は終了コード1で終わる（GitHub Actions上で
失敗として記録され、通知で気づけるようにするため）。
"""

import os
import re
import sys
import time

from playwright.sync_api import sync_playwright

APP_URL = os.environ.get("APP_URL", "").strip()
# アプリが表示できたと判断する目印（streamlit_app.pyのヘッダーの文字）。
READY_TEXT = "Threads Transformer"
WAKE_BUTTON = re.compile(r"get this app back up", re.IGNORECASE)
TIMEOUT_SECONDS = 300


def app_is_ready(page) -> bool:
    """ページ本体と、その中のiframe（Community Cloudはアプリをiframeで表示する）を探す。"""
    for frame in page.frames:
        try:
            if frame.get_by_text(READY_TEXT).count() > 0:
                return True
        except Exception:
            pass
    return False


def main() -> int:
    if not APP_URL:
        print("APP_URL が設定されていません。", file=sys.stderr)
        return 1

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(APP_URL, wait_until="domcontentloaded", timeout=120_000)

        deadline = time.time() + TIMEOUT_SECONDS
        woke = False
        while time.time() < deadline:
            if app_is_ready(page):
                # 少しの間開いたままにして、閲覧として確実に記録させる。
                page.wait_for_timeout(20_000)
                print("アプリは起動しています。" + ("（休止から起動しました）" if woke else ""))
                browser.close()
                return 0
            button = page.get_by_role("button", name=WAKE_BUTTON)
            if not woke and button.count() > 0:
                print("アプリが休止していたため、起動ボタンを押しました。")
                button.first.click()
                woke = True
            page.wait_for_timeout(5_000)

        page.screenshot(path="keep_awake_failure.png", full_page=True)
        browser.close()

    print(f"{TIMEOUT_SECONDS}秒待ってもアプリが表示されませんでした。", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
