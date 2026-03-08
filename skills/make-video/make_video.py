#!/usr/bin/env python3
"""
make_video.py — Gulliver Video Factory
Génère des vidéos démo MP4 à partir d'un script JSON.

Usage:
  python3 make_video.py --script /data/video_scripts/demo.json
  python3 make_video.py --script /data/video_scripts/demo.json --output /data/videos/out.mp4
  cat script.json | python3 make_video.py
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from playwright.async_api import async_playwright

VOICE_DEFAULT = "fr-FR-DeniseNeural"
RESOLUTION = {"width": 1280, "height": 800}


# ── Helpers ─────────────────────────────────────────────────────────────────

def log(msg: str):
    print(msg, flush=True)


def check_deps() -> list[str]:
    missing = []
    r = subprocess.run(["ffmpeg", "-version"], capture_output=True)
    if r.returncode != 0:
        missing.append("ffmpeg (apt-get install -y ffmpeg)")
    try:
        import edge_tts  # noqa: F401
    except ImportError:
        missing.append("edge-tts (pip install edge-tts)")
    return missing


def get_audio_duration(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except Exception:
        return 3.0


async def tts(text: str, out_path: str, voice: str) -> bool:
    """Generate MP3 via edge-tts."""
    try:
        import edge_tts as et
        communicate = et.Communicate(text, voice)
        await communicate.save(out_path)
        return os.path.exists(out_path)
    except Exception as e:
        log(f"  [TTS] Error: {e}")
        return False


# ── Scene execution ──────────────────────────────────────────────────────────

async def run_scene(page, scene: dict):
    stype = scene.get("type", "wait")
    wait_ms = scene.get("wait_ms", 1000)
    sid = scene.get("id", stype)

    try:
        if stype == "navigate":
            await page.goto(scene["url"], wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(wait_ms)

        elif stype == "click":
            if "selector" in scene:
                await page.click(scene["selector"], timeout=5000)
            elif "x" in scene and "y" in scene:
                await page.mouse.click(float(scene["x"]), float(scene["y"]))
            await page.wait_for_timeout(wait_ms)

        elif stype == "fill":
            await page.fill(scene["selector"], scene.get("value", ""), timeout=5000)
            await page.wait_for_timeout(wait_ms)

        elif stype == "key":
            await page.keyboard.press(scene.get("key", "Enter"))
            await page.wait_for_timeout(wait_ms)

        elif stype == "scroll":
            await page.mouse.wheel(0, float(scene.get("delta_y", 400)))
            await page.wait_for_timeout(wait_ms)

        elif stype == "narrate":
            duration_ms = int(scene.get("duration", 3)) * 1000
            await page.wait_for_timeout(duration_ms)

        elif stype == "wait":
            await page.wait_for_timeout(wait_ms)

        log(f"  ✓ [{sid}] {stype}")

    except Exception as e:
        log(f"  ✗ [{sid}] {stype}: {e} — skipping")


# ── Main ─────────────────────────────────────────────────────────────────────

async def make_video(script: dict, output_override: str | None = None) -> bool:
    title = script.get("title", "Demo")
    scenes = script.get("scenes", [])
    voice = script.get("voice", VOICE_DEFAULT)
    output = output_override or script.get(
        "output", "/data/videos/output.mp4"
    )

    Path(output).parent.mkdir(parents=True, exist_ok=True)

    log(f"\n🎬 {title}")
    log(f"   {len(scenes)} scenes · voice: {voice} · output: {output}\n")

    # Check deps
    missing = check_deps()
    if missing:
        log("❌ Missing dependencies:")
        for m in missing:
            log(f"   - {m}")
        return False

    with tempfile.TemporaryDirectory() as tmp:

        # ── Step 1: TTS ──────────────────────────────────────────────────────
        log("── Step 1/3: Generating voiceovers...")
        audio_parts = []

        for i, scene in enumerate(scenes):
            text = scene.get("text", "").strip()
            if not text:
                continue
            apath = os.path.join(tmp, f"audio_{i:04d}.mp3")
            ok = await tts(text, apath, voice)
            if ok:
                dur = get_audio_duration(apath)
                audio_parts.append({"index": i, "path": apath, "duration": dur})
                # If scene has no explicit wait_ms, use audio duration + 500ms buffer
                if "wait_ms" not in scene and scene.get("type") not in ("narrate",):
                    scene["wait_ms"] = int(dur * 1000) + 500
                log(f"   [{i:02d}] {dur:.1f}s — {text[:60]}")

        # ── Step 2: Browser recording ────────────────────────────────────────
        log("\n── Step 2/3: Recording browser session...")

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                      "--disable-setuid-sandbox"],
            )
            ctx = await browser.new_context(
                viewport=RESOLUTION,
                record_video_dir=tmp,
                record_video_size=RESOLUTION,
                locale="fr-FR",
                timezone_id="Europe/Paris",
            )
            page = await ctx.new_page()

            for scene in scenes:
                await run_scene(page, scene)

            # Close context → flushes the webm file
            await ctx.close()
            await browser.close()

        webm_files = list(Path(tmp).glob("*.webm"))
        if not webm_files:
            log("\n❌ No webm recorded")
            return False

        video_raw = str(webm_files[0])
        log(f"   Recorded: {Path(video_raw).name}")

        # ── Step 3: FFmpeg assembly ──────────────────────────────────────────
        log("\n── Step 3/3: Assembling with FFmpeg...")

        if audio_parts:
            # Concatenate audio parts
            list_file = os.path.join(tmp, "audio_concat.txt")
            with open(list_file, "w") as f:
                for ap in audio_parts:
                    f.write(f"file '{ap['path']}'\n")

            combined_audio = os.path.join(tmp, "audio.mp3")
            r = subprocess.run(
                ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                 "-i", list_file, "-c", "copy", combined_audio],
                capture_output=True,
            )
            if r.returncode != 0:
                log(f"   [ffmpeg concat] {r.stderr.decode()[-300:]}")

            # Merge video + audio → output MP4
            r = subprocess.run(
                ["ffmpeg", "-y",
                 "-i", video_raw,
                 "-i", combined_audio,
                 "-c:v", "libx264", "-preset", "fast",
                 "-c:a", "aac", "-b:a", "128k",
                 "-pix_fmt", "yuv420p",
                 "-movflags", "+faststart",
                 "-shortest",
                 output],
                capture_output=True,
            )
        else:
            # No audio — just convert webm → mp4
            r = subprocess.run(
                ["ffmpeg", "-y",
                 "-i", video_raw,
                 "-c:v", "libx264", "-preset", "fast",
                 "-pix_fmt", "yuv420p",
                 "-movflags", "+faststart",
                 output],
                capture_output=True,
            )

        if r.returncode != 0:
            log(f"   [ffmpeg] Error:\n{r.stderr.decode()[-500:]}")
            return False

    if not os.path.exists(output):
        log(f"\n❌ Output not found: {output}")
        return False

    size_mb = os.path.getsize(output) / 1024 / 1024
    try:
        dur = get_audio_duration(output)
        log(f"\n✅ Done: {output}")
        log(f"   Duration: {int(dur//60)}m{int(dur%60)}s · Size: {size_mb:.1f} MB")
    except Exception:
        log(f"\n✅ Done: {output} ({size_mb:.1f} MB)")

    return output


# ── YouTube upload ────────────────────────────────────────────────────────────

def youtube_upload(mp4_path: str, title: str, description: str = "",
                   privacy: str = "private",
                   token_path: str = "/data/youtube-token.json") -> str | None:
    """
    Upload MP4 to YouTube as private (= draft). Returns video URL or None.
    Requires: google-api-python-client, youtube-token.json with youtube.upload scope.
    """
    try:
        import google.oauth2.credentials
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        import urllib.request, urllib.parse
    except ImportError:
        log("❌ Missing: pip install google-api-python-client")
        return None

    if not os.path.exists(token_path):
        log(f"❌ YouTube token not found: {token_path}")
        log("   Run setup_youtube_auth.py to generate it.")
        return None

    with open(token_path) as f:
        tok = json.load(f)

    # Refresh access token
    try:
        data = urllib.parse.urlencode({
            "client_id": tok["client_id"],
            "client_secret": tok["client_secret"],
            "refresh_token": tok["refresh_token"],
            "grant_type": "refresh_token",
        }).encode()
        resp = json.loads(urllib.request.urlopen(
            "https://oauth2.googleapis.com/token", data
        ).read())
        access_token = resp["access_token"]
    except Exception as e:
        log(f"❌ Token refresh failed: {e}")
        return None

    creds = google.oauth2.credentials.Credentials(
        token=access_token,
        refresh_token=tok["refresh_token"],
        client_id=tok["client_id"],
        client_secret=tok["client_secret"],
        token_uri="https://oauth2.googleapis.com/token",
    )

    youtube = build("youtube", "v3", credentials=creds)

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "categoryId": "28",  # Science & Technology
        },
        "status": {
            "privacyStatus": privacy,  # "private" = draft visible only to you
            "selfDeclaredMadeForKids": False,
        },
    }

    log(f"\n── YouTube upload: {Path(mp4_path).name} → '{title}' ({privacy})")
    media = MediaFileUpload(mp4_path, mimetype="video/mp4", resumable=True, chunksize=5 * 1024 * 1024)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            log(f"   Uploading... {int(status.progress() * 100)}%")

    video_id = response.get("id")
    if video_id:
        url = f"https://youtu.be/{video_id}"
        log(f"✅ Uploaded: {url}")
        return url
    else:
        log(f"❌ Upload failed: {response}")
        return None


def main():
    parser = argparse.ArgumentParser(description="Gulliver Video Factory")
    parser.add_argument("--script", help="Path to script JSON file (default: stdin)")
    parser.add_argument("--output", help="Override output MP4 path")
    parser.add_argument("--youtube", action="store_true",
                        help="Upload to YouTube as private draft after generation")
    parser.add_argument("--youtube-privacy", default="private",
                        choices=["private", "unlisted"],
                        help="YouTube privacy (default: private)")
    args = parser.parse_args()

    if args.script:
        with open(args.script, encoding="utf-8") as f:
            script = json.load(f)
    else:
        script = json.load(sys.stdin)

    result = asyncio.run(make_video(script, args.output))
    if not result:
        sys.exit(1)

    if args.youtube:
        title = script.get("title", "Gulliver Demo")
        description = script.get("description", "Vidéo générée par Gulliver")
        url = youtube_upload(result, title, description, args.youtube_privacy)
        if not url:
            log("⚠️  Video generated but YouTube upload failed.")
            sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
