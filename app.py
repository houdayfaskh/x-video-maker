import os
import re
import sys
import uuid
import json
import subprocess
import tempfile
import textwrap
import time
from pathlib import Path

from flask import Flask, request, jsonify, send_file, render_template
import deepl

app = Flask(__name__)

# #region agent log
DEBUG_LOG = Path(__file__).resolve().parent / ".cursor" / "debug.log"
def _dlog(msg, data=None, hyp="", loc=""):
    try:
        DEBUG_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {"timestamp": int(time.time()*1000), "message": msg, "location": loc, "hypothesisId": hyp}
        if data is not None: entry["data"] = data
        with open(DEBUG_LOG, "a") as f: f.write(json.dumps(entry) + "\n")
    except Exception: pass
# #endregion

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

        # Build full 1080x1920 background with Pillow, then convert to
        # a real H.264 video via raw pixel pipe (no image file input to
        # FFmpeg at all — avoids all -loop / overlay / eof bugs in FFmpeg 7.x).
        from PIL import Image as PILImage, ImageDraw as PILDraw
        bg = PILImage.new("RGB", (OUTPUT_W, OUTPUT_H), (0, 0, 0))
        text_img = PILImage.open(text_png_path).convert("RGB")
        if text_img.height > text_area_h:
            text_img = text_img.crop((0, 0, text_img.width, text_area_h))
        bg.paste(text_img, (text_overlay_x, text_overlay_y))
        d = PILDraw.Draw(bg)
        border_col = (58, 58, 60)
        d.rectangle(
            [card_x, card_y, card_x + card_w, card_y + card_h],
            outline=border_col, width=CARD_RADIUS_HACK_T,
        )
        d.rectangle(
            [card_x, sep_y, card_x + card_w, sep_y + 2],
            fill=border_col,
        )
        bg_bytes = bg.tobytes()

        _, bg_vid_path = tempfile.mkstemp(suffix=".mp4", prefix="tweet_bg_")
        try:
            bg_proc = subprocess.Popen(
                [
                    "ffmpeg", "-y", "-hide_banner",
                    "-f", "rawvideo", "-pix_fmt", "rgb24",
                    "-s", f"{OUTPUT_W}x{OUTPUT_H}",
                    "-r", "1", "-i", "pipe:0",
                    "-frames:v", "1",
                    "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart",
                    bg_vid_path,
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            bg_proc.stdin.write(bg_bytes)
            bg_proc.stdin.close()
            bg_proc.wait(timeout=30)
            bg_stderr = bg_proc.stderr.read().decode()
            # #region agent log
            bg_vid_size = Path(bg_vid_path).stat().st_size if Path(bg_vid_path).exists() else 0
            _dlog("bg_video_created", {"rc": bg_proc.returncode, "size": bg_vid_size, "stderr": bg_stderr[-300:]}, hyp="B", loc="app.py:bg_creation")
            # #endregion
            if bg_proc.returncode != 0:
                raise RuntimeError(f"BG video error: {bg_stderr[-500:]}")

            filter_complex = (
                f"[1:v]loop=-1:size=1:start=0,setpts=N/30/TB[bg];"
                f"[0:v]scale={vid_w}:{vid_h}:flags=lanczos,setsar=1[vid];"
                f"[bg][vid]overlay={vid_x}:{vid_y}[out]"
            )

            # Detect audio: only add audio options if stream exists
            probe_audio = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "a",
                 "-show_entries", "stream=index", "-of", "json", input_video],
                capture_output=True, text=True, timeout=10,
            )
            has_audio = '"index"' in probe_audio.stdout

            cmd = [
                "ffmpeg", "-y", "-hide_banner",
                "-i", input_video,
                "-i", bg_vid_path,
                "-filter_complex", filter_complex,
                "-map", "[out]",
            ]
            if has_audio:
                cmd += ["-map", "0:a", "-c:a", "aac", "-b:a", "128k"]
            cmd += [
                "-t", f"{duration:.3f}",
                "-r", "30",
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "20",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                output_path,
            ]

            # #region agent log
            _dlog("ffmpeg_main_cmd", {"filter": filter_complex, "duration": duration, "vid_w": vid_w, "vid_h": vid_h}, hyp="A,C,D", loc="app.py:main_ffmpeg")
            # #endregion

            # Capture input video properties for diagnostics
            probe_cmd = ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", input_video]
            probe_r = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=10)
            input_probe = probe_r.stdout[:2000] if probe_r.returncode == 0 else f"ffprobe failed: {probe_r.stderr[:300]}"

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                err = result.stderr
                error_lines = [l for l in err.split("\n") if any(
                    k in l.lower() for k in ["error", "invalid", "cannot", "failed", "no such", "discarding"]
                )]
                # #region agent log
                _dlog("ffmpeg_failed", {"rc": result.returncode, "error_lines": error_lines[:10], "stderr_start": err[:2000], "stderr_end": err[-500:]}, hyp="A,C,D,E", loc="app.py:ffmpeg_error")
                # #endregion
                diag = {
                    "stderr_start": err[:2500],
                    "error_lines": error_lines[:10],
                    "input_video_probe": input_probe,
                    "filter": filter_complex,
                    "bg_vid_size": Path(bg_vid_path).stat().st_size if Path(bg_vid_path).exists() else 0,
                }
                raise RuntimeError(f"FFmpeg_DIAG:{json.dumps(diag)}")
        finally:
            Path(bg_vid_path).unlink(missing_ok=True)
    finally:
        Path(text_png_path).unlink(missing_ok=True)

    return output_path


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/healthcheck")
def healthcheck():
    """Quick sanity check: FFmpeg version + text render."""
    import shutil
    info = {"ffmpeg": None, "render": None}
    ffmpeg_path = shutil.which("ffmpeg")
    info["ffmpeg_path"] = ffmpeg_path
    try:
        r = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5)
        info["ffmpeg"] = r.stdout.split("\n")[0]
    except Exception as e:
        info["ffmpeg"] = str(e)
    try:
        _, tmp = tempfile.mkstemp(suffix=".png")
        cfg = {"text": "test", "font_size": 42, "max_width": 500,
               "output_path": tmp, "bg_hex": "000000"}
        h = render_text_to_png(cfg)
        info["render"] = f"OK height={h}"
        Path(tmp).unlink(missing_ok=True)
    except Exception as e:
        info["render"] = str(e)
    return jsonify(info)


@app.route("/api/ffmpeg-test")
def ffmpeg_test():
    """Comprehensive FFmpeg overlay test — returns full stderr for each step."""
    from PIL import Image as PILImage
    results = {}
    tmp_files = []

    def mktmp(suffix):
        _, p = tempfile.mkstemp(suffix=suffix)
        tmp_files.append(p)
        return p

    try:
        # --- Hyp A/B: Create 1-frame bg video from raw pixels ---
        bg_vid = mktmp(".mp4")
        frame = PILImage.new("RGB", (320, 240), (255, 0, 0))
        p1 = subprocess.Popen(
            ["ffmpeg", "-y", "-hide_banner",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "320x240",
             "-r", "1", "-i", "pipe:0", "-frames:v", "1",
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", bg_vid],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        p1.stdin.write(frame.tobytes())
        p1.stdin.close()
        p1.wait(timeout=15)
        bg_size = Path(bg_vid).stat().st_size if Path(bg_vid).exists() else 0
        results["step1_bg_video"] = {
            "rc": p1.returncode,
            "file_size": bg_size,
            "stderr": p1.stderr.read().decode()[-500:],
        }

        # --- Hyp C: Create 3s synthetic video (like a Twitter vid) ---
        src_vid = mktmp(".mp4")
        r2 = subprocess.run(
            ["ffmpeg", "-y", "-hide_banner",
             "-f", "lavfi", "-i", "testsrc=duration=3:size=640x360:rate=25",
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             src_vid],
            capture_output=True, text=True, timeout=15,
        )
        results["step2_src_video"] = {"rc": r2.returncode}

        # --- Hyp A: Test loop filter on 1-frame video ---
        loop_out = mktmp(".mp4")
        r_loop = subprocess.run(
            ["ffmpeg", "-y", "-hide_banner",
             "-i", bg_vid,
             "-vf", "loop=-1:size=1:start=0,setpts=N/25/TB",
             "-t", "2", "-c:v", "libx264", "-preset", "ultrafast",
             "-pix_fmt", "yuv420p", loop_out],
            capture_output=True, text=True, timeout=15,
        )
        results["step3_loop_only"] = {
            "rc": r_loop.returncode,
            "stderr_full": r_loop.stderr[:2000],
            "errors": [l for l in r_loop.stderr.split("\n")
                       if any(k in l.lower() for k in ["error", "invalid", "failed"])],
        }

        # --- Hyp A+D: Full overlay pipeline at small scale ---
        out_small = mktmp(".mp4")
        fc_small = (
            "[1:v]loop=-1:size=1:start=0,setpts=N/25/TB[bg];"
            "[0:v]fps=25,scale=160:120,setsar=1[vid];"
            "[bg][vid]overlay=80:60[out]"
        )
        r3 = subprocess.run(
            ["ffmpeg", "-y", "-hide_banner",
             "-i", src_vid, "-i", bg_vid,
             "-filter_complex", fc_small,
             "-map", "[out]", "-t", "3",
             "-c:v", "libx264", "-preset", "ultrafast",
             "-pix_fmt", "yuv420p", out_small],
            capture_output=True, text=True, timeout=30,
        )
        results["step4_overlay_small"] = {
            "rc": r3.returncode,
            "stderr_full": r3.stderr[:2500],
            "errors": [l for l in r3.stderr.split("\n")
                       if any(k in l.lower() for k in ["error", "invalid", "failed"])],
        }

        # --- Hyp E: Full-size overlay (1080x1920) ---
        bg_vid_big = mktmp(".mp4")
        frame_big = PILImage.new("RGB", (1080, 1920), (0, 0, 0))
        p_big = subprocess.Popen(
            ["ffmpeg", "-y", "-hide_banner",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "1080x1920",
             "-r", "1", "-i", "pipe:0", "-frames:v", "1",
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", bg_vid_big],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        p_big.stdin.write(frame_big.tobytes())
        p_big.stdin.close()
        p_big.wait(timeout=30)
        bg_big_size = Path(bg_vid_big).stat().st_size if Path(bg_vid_big).exists() else 0

        out_big = mktmp(".mp4")
        fc_big = (
            "[1:v]loop=-1:size=1:start=0,setpts=N/25/TB[bg];"
            "[0:v]fps=25,scale=540:960,setsar=1[vid];"
            "[bg][vid]overlay=270:480[out]"
        )
        r4 = subprocess.run(
            ["ffmpeg", "-y", "-hide_banner",
             "-i", src_vid, "-i", bg_vid_big,
             "-filter_complex", fc_big,
             "-map", "[out]", "-t", "3",
             "-c:v", "libx264", "-preset", "ultrafast",
             "-pix_fmt", "yuv420p", out_big],
            capture_output=True, text=True, timeout=60,
        )
        results["step5_overlay_fullsize"] = {
            "rc": r4.returncode,
            "bg_file_size": bg_big_size,
            "stderr_full": r4.stderr[:2500],
            "errors": [l for l in r4.stderr.split("\n")
                       if any(k in l.lower() for k in ["error", "invalid", "failed"])],
        }

    except Exception as e:
        results["exception"] = str(e)
    finally:
        for p in tmp_files:
            Path(p).unlink(missing_ok=True)

    return jsonify(results)


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
        # #region agent log
        err_str = str(e)
        if err_str.startswith("FFmpeg_DIAG:"):
            try:
                diag = json.loads(err_str[len("FFmpeg_DIAG:"):])
                return jsonify({"error": "FFmpeg failed", "diagnostics": diag}), 500
            except Exception:
                pass
        # #endregion
        return jsonify({"error": f"Erreur lors du traitement vidéo : {err_str}"}), 500
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
