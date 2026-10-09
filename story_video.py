"""
ストーリーズの背景に動画を使う場合の処理をまとめたモジュール。

- 動画から位置合わせ用の静止画（1フレーム）を取り出す
- 背景動画 + 文字レイヤー（透明PNG）を合成し、Instagramストーリーズ向けの
  1080x1920のMP4（H.264 / AAC）として書き出す

背景の拡大縮小・位置は story_image.compute_background_placement() の結果を
そのまま使うので、画像背景のときと同じ見た目になる。

ffmpegは、pipで入るimageio-ffmpeg同梱のバイナリを優先して使う
（Streamlit Community CloudのようにOSへffmpegを入れていない環境でも動く）。
見つからない場合はPATH上のffmpegを使う。
"""

import io
import os
import re
import shutil
import subprocess
import tempfile

from PIL import Image

from story_image import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    DEFAULT_BG_FIT,
    LETTERBOX_COLOR,
    MAX_OVERLAY_OPACITY,
    StoryImageError,
    compute_background_placement,
)

VIDEO_EXTENSIONS = ("mp4", "mov", "m4v")

# Instagramストーリーズの動画は最長60秒。これより長い動画は先頭60秒だけ使う。
MAX_VIDEO_SECONDS = 60
OUTPUT_FPS = 30

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def is_video_filename(name: str) -> bool:
    return (name or "").lower().rsplit(".", 1)[-1] in VIDEO_EXTENSIONS


def _ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    exe = shutil.which("ffmpeg")
    if not exe:
        raise StoryImageError(
            "動画の処理に必要なffmpegが見つかりませんでした。",
            detail="imageio-ffmpeg をインストールするか、ffmpegをPATHに追加してください。",
        )
    return exe


def _run(args):
    return subprocess.run(
        [_ffmpeg_exe(), "-hide_banner", "-nostdin", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _stderr_tail(completed, lines=15) -> str:
    text = completed.stderr.decode("utf-8", errors="replace")
    return "\n".join(text.strip().splitlines()[-lines:])


def load_video_poster(video_bytes: bytes, suffix: str = ".mp4", start: float = 0.0):
    """
    動画から静止画を1枚取り出す。プレビューでの位置合わせと、
    配置計算（動画の縦横サイズ）に使う。startは使用する区間の開始位置（秒）で、
    その少し先のコマを取り出す。

    戻り値: (RGB画像, 動画の長さ[秒] または None)
    読み込めない場合はStoryImageErrorを送出する。
    """
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "in" + suffix)
        with open(src, "wb") as f:
            f.write(video_bytes)

        # 冒頭は真っ暗なことがあるので少し進んだ位置を取り、
        # 短すぎて取れなければ先頭フレームにする。
        for seek in (f"{start + 0.5:.2f}", f"{start:.2f}", "0"):
            completed = _run(
                ["-ss", seek, "-i", src, "-frames:v", "1", "-f", "image2pipe", "-c:v", "png", "-"]
            )
            if completed.returncode == 0 and completed.stdout:
                break
        else:
            raise StoryImageError(
                "背景動画を読み込めませんでした。別の動画でお試しください。",
                detail=_stderr_tail(completed),
            )

    try:
        frame = Image.open(io.BytesIO(completed.stdout)).convert("RGB")
    except Exception as e:
        raise StoryImageError(
            "背景動画を読み込めませんでした。別の動画でお試しください。",
            detail=str(e),
        )

    duration = None
    match = _DURATION_RE.search(completed.stderr.decode("utf-8", errors="replace"))
    if match:
        hours, minutes, seconds = match.groups()
        duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)

    return frame, duration


def render_story_video(
    video_bytes: bytes,
    text_layer_png: bytes,
    frame_size,
    bg_fit=DEFAULT_BG_FIT,
    bg_offset=(0, 0),
    overlay_opacity=0.0,
    suffix: str = ".mp4",
    start: float = 0.0,
    duration: float = MAX_VIDEO_SECONDS,
) -> bytes:
    """
    背景動画の上に文字レイヤーを重ねた、1080x1920のMP4を書き出して返す。

    引数:
        text_layer_png: render_story_text_layer()の結果をPNGにしたもの。
        frame_size: load_video_poster()で得た静止画の (幅, 高さ)。
        bg_fit / bg_offset / overlay_opacity: generate_story_image()と同じ意味。
        start: 元の動画のうち、使用する区間の開始位置（秒）。
        duration: startから書き出す長さ（秒）。MAX_VIDEO_SECONDSを超える指定は
                  MAX_VIDEO_SECONDSに丸める。
    """
    src_w, src_h = frame_size
    offset_x, offset_y = bg_offset or (0, 0)
    new_w, new_h, left, top = compute_background_placement(
        src_w, src_h, bg_fit, offset_x, offset_y
    )
    # H.264(yuv420p)で扱いやすいよう、拡大縮小後のサイズは偶数に揃える。
    new_w += new_w % 2
    new_h += new_h % 2

    opacity = max(0.0, min(overlay_opacity or 0.0, MAX_OVERLAY_OPACITY))
    brightness = f"{1 - opacity:.3f}"
    canvas_color = "0x%02X%02X%02X" % LETTERBOX_COLOR

    filter_graph = (
        f"color=c={canvas_color}:s={CANVAS_WIDTH}x{CANVAS_HEIGHT}:r={OUTPUT_FPS}[canvas];"
        f"[0:v]scale={new_w}:{new_h}:flags=lanczos,setsar=1,"
        f"colorchannelmixer=rr={brightness}:gg={brightness}:bb={brightness}[bg];"
        f"[canvas][bg]overlay={left}:{top}:shortest=1[base];"
        f"[base][1:v]overlay=0:0:format=auto,format=yuv420p[out]"
    )

    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "in" + suffix)
        overlay = os.path.join(tmp, "text.png")
        dst = os.path.join(tmp, "out.mp4")
        with open(src, "wb") as f:
            f.write(video_bytes)
        with open(overlay, "wb") as f:
            f.write(text_layer_png)

        completed = _run(
            [
                "-y",
                "-ss", f"{max(0.0, start):.2f}",
                "-i", src,
                "-i", overlay,
                "-filter_complex", filter_graph,
                "-map", "[out]",
                "-map", "0:a?",
                "-t", f"{max(0.1, min(duration, MAX_VIDEO_SECONDS)):.2f}",
                "-r", str(OUTPUT_FPS),
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "21",
                "-maxrate", "8M",
                "-bufsize", "16M",
                "-c:a", "aac",
                "-b:a", "128k",
                "-ar", "48000",
                "-movflags", "+faststart",
                dst,
            ]
        )
        if completed.returncode != 0 or not os.path.exists(dst):
            raise StoryImageError(
                "動画の書き出しに失敗しました。別の動画でお試しください。",
                detail=_stderr_tail(completed),
            )
        with open(dst, "rb") as f:
            return f.read()
