import os
import re
import sys
import uuid
import json
import subprocess
import tempfile
import textwrap
from pathlib import Path

from flask import Flask, request, jsonify, send_file, render_template
import deepl

app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent
TEMP_DIR = BASE_DIR / "temp"
DOWNLOADS_DIR = BASE_DIR / "downloads"
TEMP_DIR.mkdir(exist_ok=True)
DOWNLOADS_DIR.mkdir(exist_ok=True)

RENDER_TEXT_SCRIPT = Path(__file__).resolve().parent / "render_text.py"

def _ytdlp_cmd():
    """Return the yt-dlp command, preferring the one next to the current Python."""
    venv_bin = Path(sys.executable).parent / "yt-dlp"
    if venv_bin.exists():
        return str(venv_bin)
    return "yt-dlp"


def cleanup_old_files():
    """Remove files older than 1 hour from temp and downloads."""
    import time
    now = time.time()
    for folder in [TEMP_DIR, DOWNLOADS_DIR]:
        for f in folder.iterdir():
            if f.is_file() and (now - f.stat().st_mtime) > 3600:
                f.unlink(missing_ok=True)


def extract_tweet_info(url: str) -> dict:
    """Use yt-dlp to extract video URL and tweet description."""
    cmd = [
        _ytdlp_cmd(),
        "--no-check-certificates",
        "--dump-json",
        "--no-download",
        url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp error: {result.stderr.strip()}")

    info = json.loads(result.stdout)
    description = info.get("description", "") or ""
    uploader = info.get("uploader", "") or info.get("channel", "") or ""
    title = info.get("title", "") or ""

    tweet_text = description if description else title

    return {
        "tweet_text": tweet_text,
        "uploader": uploader,
        "url": url,
        "info": info,
    }


def download_video(url: str, output_path: str) -> str:
    """Download the video using yt-dlp."""
    cmd = [
        _ytdlp_cmd(),
        "--no-check-certificates",
        "-f", "best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "-o", output_path,
        url,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"Download error: {result.stderr.strip()}")
    return output_path


def get_video_info(video_path: str) -> tuple:
    """Get video width, height, and duration using ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,duration",
        "-show_entries", "format=duration",
        "-of", "json",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    data = json.loads(result.stdout)
    stream = data["streams"][0]
    w = int(stream["width"])
    h = int(stream["height"])
    dur = stream.get("duration") or data.get("format", {}).get("duration") or "30"
    return w, h, float(dur)


OUTPUT_W, OUTPUT_H = 1080, 1920
CARD_MARGIN_X = 36
CARD_RADIUS_HACK_T = 3
TOP_SAFE_MARGIN = 180


def clean_tweet_text(text: str) -> str:
    """Remove URLs and clean up whitespace."""
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"pic\.twitter\.com/\S+", "", text)
    lines = [line.strip() for line in text.strip().split("\n") if line.strip()]
    return "\n".join(lines)


def translate_to_french(text: str, api_key: str) -> str:
    """Translate text to French using DeepL API."""
    try:
        translator = deepl.Translator(api_key)
        result = translator.translate_text(text, target_lang="FR")
        return result.text
    except Exception as e:
        raise RuntimeError(f"Erreur DeepL : {str(e)}")


def wrap_text(text: str, max_chars_per_line: int = 45) -> str:
    """Wrap text to fit within the card."""
    lines = []
    for paragraph in text.split("\n"):
        if paragraph.strip():
            wrapped = textwrap.wrap(paragraph, width=max_chars_per_line)
            lines.extend(wrapped)
        else:
            lines.append("")
    return "\n".join(lines[:8])


def render_text_to_png(config: dict) -> int:
    """Render tweet-style text to PNG using Pillow. Returns image height."""
    _, config_path = tempfile.mkstemp(suffix=".json", prefix="tweet_cfg_")
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False)
        cmd = [sys.executable, str(RENDER_TEXT_SCRIPT), config_path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            raise RuntimeError(f"Text render error: {result.stderr.strip()}")
        height = int(result.stdout.strip())
        app.logger.info(f"Text PNG rendered: {config['output_path']} height={height}")
        return height
    finally:
        Path(config_path).unlink(missing_ok=True)


def create_video_with_banner(
    input_video: str,
    tweet_text: str,
    uploader: str,
    output_path: str,
    profile: dict = None,
) -> str:
    """
    Produces a 1080x1920 (9:16 Reel/Short) video:
    - Black background with top safe margin for Instagram
    - Tweet-style card: native text + emojis, optional profile header
    - No links
    """
    orig_w, orig_h, duration = get_video_info(input_video)

    display_text = clean_tweet_text(tweet_text)

    card_w = OUTPUT_W - (CARD_MARGIN_X * 2)

    _, text_png_path = tempfile.mkstemp(suffix=".png", prefix="tweet_text_")
    try:
        has_profile = profile and profile.get("display_name")
        render_config = {
            "text": display_text,
            "font_size": 48 if has_profile else 42,
            "max_width": card_w,
            "output_path": text_png_path,
            "bg_hex": "000000",
        }
        if has_profile:
            render_config["profile"] = profile

        text_img_h = render_text_to_png(render_config)

        if not Path(text_png_path).exists() or Path(text_png_path).stat().st_size == 0:
            raise RuntimeError(f"Text PNG not created at {text_png_path}")

        max_text_h = OUTPUT_H // 2
        text_area_h = min(text_img_h, max_text_h)
        vid_max_w = card_w
        vid_max_h = OUTPUT_H - TOP_SAFE_MARGIN - text_area_h - 4 - (CARD_MARGIN_X * 2)
        vid_max_h = max(vid_max_h, 200)

        scale_f = min(vid_max_w / orig_w, vid_max_h / orig_h)
        vid_w = int(orig_w * scale_f)
        vid_h = int(orig_h * scale_f)
        vid_w = max(vid_w - vid_w % 2, 2)
        vid_h = max(vid_h - vid_h % 2, 2)

        card_h = text_area_h + 2 + vid_h
        card_y = TOP_SAFE_MARGIN + (OUTPUT_H - TOP_SAFE_MARGIN - card_h - CARD_MARGIN_X) // 2
        card_y = max(card_y, 0)
        card_x = CARD_MARGIN_X

        text_overlay_x = card_x
        text_overlay_y = card_y
        sep_y = card_y + text_area_h
        vid_x = card_x + (card_w - vid_w) // 2
        vid_y = sep_y + 2

        app.logger.info(
            f"Layout: text_h={text_area_h} vid={vid_w}x{vid_h} "
            f"card={card_x},{card_y} {card_w}x{card_h} dur={duration:.2f}s "
            f"overlay=({text_overlay_x},{text_overlay_y}) vid_pos=({vid_x},{vid_y})"
        )

        filter_complex = (
            f"[0:v]scale={vid_w}:{vid_h}:flags=lanczos,setsar=1[scaled];"
            f"[scaled]pad={OUTPUT_W}:{OUTPUT_H}:{vid_x}:{vid_y}:black,"
            f"drawbox=x={card_x}:y={card_y}:w={card_w}:h={card_h}:color=0x3A3A3C@0.6:t={CARD_RADIUS_HACK_T},"
            f"drawbox=x={card_x}:y={sep_y}:w={card_w}:h=2:color=0x3A3A3C@0.5:t=fill[base];"
            f"[base][1:v]overlay={text_overlay_x}:{text_overlay_y}[out]"
        )

        cmd = [
            "ffmpeg", "-y",
            "-i", input_video,
            "-loop", "1", "-t", f"{duration:.3f}", "-i", text_png_path,
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-map", "0:a?",
            "-shortest",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "20",
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            output_path,
        ]

        app.logger.info(f"FFmpeg cmd: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            app.logger.error(f"FFmpeg stderr:\n{result.stderr}")
            raise RuntimeError(f"FFmpeg error: {result.stderr[-2000:]}")
    finally:
        Path(text_png_path).unlink(missing_ok=True)

    return output_path


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/process", methods=["POST"])
def process_tweet():
    cleanup_old_files()

    if request.content_type and "multipart/form-data" in request.content_type:
        url = request.form.get("url", "").strip()
        do_translate = request.form.get("translate") == "true"
        deepl_key = request.form.get("deepl_key", "").strip()
        profile_name = request.form.get("profile_name", "").strip()
        profile_handle = request.form.get("profile_handle", "").strip()
        profile_pic = request.files.get("profile_pic")
    else:
        data = request.get_json()
        url = data.get("url", "").strip()
        do_translate = data.get("translate", False)
        deepl_key = data.get("deepl_key", "").strip()
        profile_name = data.get("profile_name", "").strip()
        profile_handle = data.get("profile_handle", "").strip()
        profile_pic = None

    if not url:
        return jsonify({"error": "URL manquante"}), 400

    if not re.match(r"https?://(twitter\.com|x\.com|vxtwitter\.com|fxtwitter\.com)/", url):
        return jsonify({"error": "URL Twitter/X invalide"}), 400

    url = re.sub(r"(vxtwitter|fxtwitter)", "twitter", url)

    job_id = str(uuid.uuid4())[:8]

    try:
        info = extract_tweet_info(url)
    except Exception as e:
        return jsonify({"error": f"Impossible d'extraire le tweet : {str(e)}"}), 400

    tweet_text = info["tweet_text"]
    if do_translate:
        if not deepl_key:
            return jsonify({"error": "Clé API DeepL requise pour la traduction"}), 400
        try:
            tweet_text = translate_to_french(tweet_text, deepl_key)
        except Exception as e:
            return jsonify({"error": str(e)}), 400

    profile = None
    avatar_tmp_path = None
    if profile_name:
        profile = {
            "display_name": profile_name,
            "handle": profile_handle,
        }
        if profile_pic and profile_pic.filename:
            from PIL import Image
            avatar_tmp_path = str(TEMP_DIR / f"{job_id}_avatar.png")
            img = Image.open(profile_pic.stream)
            w, h = img.size
            side = min(w, h)
            left = (w - side) // 2
            top = (h - side) // 2
            img.crop((left, top, left + side, top + side)).save(avatar_tmp_path)
            profile["avatar_path"] = avatar_tmp_path

    raw_video = str(TEMP_DIR / f"{job_id}_raw.mp4")
    try:
        download_video(url, raw_video)
    except Exception as e:
        return jsonify({"error": f"Impossible de télécharger la vidéo : {str(e)}"}), 400

    output_video = str(DOWNLOADS_DIR / f"tweet_{job_id}.mp4")
    try:
        create_video_with_banner(
            raw_video,
            tweet_text,
            info["uploader"],
            output_video,
            profile=profile,
        )
    except Exception as e:
        return jsonify({"error": f"Erreur lors du traitement vidéo : {str(e)}"}), 500
    finally:
        Path(raw_video).unlink(missing_ok=True)
        if avatar_tmp_path:
            Path(avatar_tmp_path).unlink(missing_ok=True)

    return jsonify({
        "success": True,
        "download_url": f"/api/download/{job_id}",
        "tweet_text": tweet_text,
        "uploader": info["uploader"],
    })


@app.route("/api/download/<job_id>")
def download(job_id):
    if not re.match(r"^[a-f0-9]{8}$", job_id):
        return jsonify({"error": "ID invalide"}), 400

    file_path = DOWNLOADS_DIR / f"tweet_{job_id}.mp4"
    if not file_path.exists():
        return jsonify({"error": "Fichier non trouvé"}), 404

    return send_file(
        file_path,
        as_attachment=True,
        download_name=f"tweet_{job_id}.mp4",
        mimetype="video/mp4",
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV") != "production"
    app.run(debug=debug, host="0.0.0.0", port=port)
