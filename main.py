import asyncio
import contextlib
import html
import http.server
import json
import logging
import os
import re
import shutil
import signal
import socketserver
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

import requests
import yt_dlp
from dotenv import load_dotenv
from mutagen.id3 import APIC, ID3, TALB, TIT2, TPE1
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)

from runtime_state import RuntimeState

# Load environment variables
load_dotenv()

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(message)s",
)

# Ensure Deno is in PATH for yt-dlp POT-provider
deno_path = os.path.expanduser("~/.deno/bin")
if deno_path not in os.environ.get("PATH", ""):
    os.environ["PATH"] = f"{deno_path}:{os.environ.get('PATH', '')}"
    logging.info(f"Added Deno to PATH: {deno_path}")


def find_ffmpeg_path() -> str:
    """Find FFmpeg binary on the system."""
    try:
        if os.name == "nt":
            common_paths = [
                r"C:\ffmpeg\bin\ffmpeg.exe",
                r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
                r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe",
            ]
            for p in common_paths:
                if os.path.exists(p):
                    return p
            res = subprocess.run(["where", "ffmpeg"], capture_output=True, text=True)
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip().split("\n")[0]
        else:
            res = subprocess.run(["which", "ffmpeg"], capture_output=True, text=True)
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
    except Exception as e:
        logging.warning(f"FFmpeg path check warning: {e}")
    return "ffmpeg"


FFMPEG_PATH = find_ffmpeg_path()
logging.info(f"FFmpeg binary path: {FFMPEG_PATH}")

# Configuration
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "data")
DOWNLOAD_PATH = os.path.join(SCRIPT_DIR, "downloads")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(DOWNLOAD_PATH, exist_ok=True)

# Credentials & Admin Setup
API_ID = int(os.getenv("API_ID", "30720676"))
API_HASH = os.getenv("API_HASH", "a078e3476750afbd6db7d6c5e5e658d9")
BOT_TOKEN = os.getenv("BOT_TOKEN", "8780722962:AAGL9e4IVewXxLoB-tuoCzI8b7Rfdkfm6XM")
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/MoviesGroupG3")
ADMINS = [
    int(x.strip())
    for x in os.getenv("ADMINS", "5566977478").split(",")
    if x.strip().isdigit()
]

# Access Control (Public by default)
WHITELIST_ENABLED = os.getenv("WHITELIST_ENABLED", "false").lower() in (
    "true",
    "1",
    "yes",
    "on",
)

# Cookies configuration
COOKIES_PATH_ENV = os.getenv("COOKIES_PATH", "data/cookies.txt")
COOKIES_PATH = os.path.join(SCRIPT_DIR, COOKIES_PATH_ENV)
if os.path.exists(COOKIES_PATH):
    logging.info(f"Using cookies file: {COOKIES_PATH}")
else:
    logging.info("Running without cookies file (standard public downloads)")

# YouTube Player Clients (iOS and Android clients bypass bot-detection reliably)
YOUTUBE_PLAYER_CLIENTS = [
    c.strip()
    for c in os.getenv("YOUTUBE_PLAYER_CLIENT", "ios,android,mweb,web").split(",")
    if c.strip()
]

# Runtime State
runtime_state = RuntimeState(DATA_DIR)
thread_pool = ThreadPoolExecutor(max_workers=6)
BOT_START_TIME = time.time()


def cleanup_session_locks():
    """Remove SQLite lock files from previous unclean shutdowns."""
    session_base = os.path.join(DATA_DIR, "youtube_downloader_bot.session")
    for lock_file in [
        f"{session_base}-journal",
        f"{session_base}-wal",
        f"{session_base}-shm",
    ]:
        if os.path.exists(lock_file):
            try:
                os.remove(lock_file)
                logging.info(f"Removed stale lock file: {lock_file}")
            except Exception as e:
                logging.warning(f"Could not remove lock file: {e}")


def clear_downloads_folder():
    """Clean the temporary downloads folder on startup."""
    try:
        for item in os.listdir(DOWNLOAD_PATH):
            item_path = os.path.join(DOWNLOAD_PATH, item)
            if os.path.isdir(item_path):
                shutil.rmtree(item_path, ignore_errors=True)
            else:
                os.remove(item_path)
        logging.info("Cleaned temporary downloads directory.")
    except Exception as e:
        logging.warning(f"Failed to clear downloads folder: {e}")


def get_yt_dlp_options(extra_opts: Optional[Dict] = None) -> Dict:
    """Standard yt-dlp extractor options with modern multi-client fallback and JS runtime support."""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "cookiefile": COOKIES_PATH if os.path.exists(COOKIES_PATH) else None,
        "nocheckcertificate": True,
        "geo_bypass": True,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-us,en;q=0.5",
            "Sec-Fetch-Mode": "navigate",
        },
        "extractor_args": {
            "youtube": {
                "player_client": YOUTUBE_PLAYER_CLIENTS,
            }
        },
    }
    if extra_opts:
        opts.update(extra_opts)
    return opts


# Pyrogram Client Setup
app = Client(
    "youtube_downloader_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workdir=DATA_DIR,
    sleep_threshold=60,
    max_concurrent_transmissions=2,
)


class ProgressTracker:
    """Manages throttled Telegram progress bar updates."""

    def __init__(self, message: Message, action: str = "Downloading"):
        self.message = message
        self.action = action
        self.start_time = time.time()
        self.last_percent = 0
        self.last_update = 0

    @staticmethod
    def format_bytes(size: float) -> str:
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"

    def make_bar(self, current: int, total: int) -> str:
        progress = current / max(total, 1)
        filled = int(18 * progress)
        bar = "▓" * filled + "░" * (18 - filled)
        percent = progress * 100
        elapsed = max(time.time() - self.start_time, 0.1)
        speed = current / elapsed
        return (
            f"⚡ **{self.action}...**\n\n"
            f"`[{bar}]` **{percent:.1f}%**\n"
            f"🚀 **Speed:** `{self.format_bytes(speed)}/s`\n"
            f"📦 **Size:** `{self.format_bytes(current)}` / `{self.format_bytes(total)}`"
        )

    async def update(self, current: int, total: int):
        now = time.time()
        if total <= 0:
            return
        percent = int((current / total) * 100)
        # Update at 5% increments or after 3 seconds
        if (percent - self.last_percent >= 5 or percent == 100) and (
            now - self.last_update >= 2.5
        ):
            try:
                await self.message.edit_text(self.make_bar(current, total))
                self.last_percent = percent
                self.last_update = now
            except Exception:
                pass


def extract_video_id(url: str) -> Optional[str]:
    """Extract YouTube Video ID from various URL patterns."""
    patterns = [
        r"(?:v=|\/)([0-9A-Za-z_-]{11}).*",
        r"youtu\.be\/([0-9A-Za-z_-]{11})",
        r"youtube\.com\/shorts\/([0-9A-Za-z_-]{11})",
        r"youtube\.com\/embed\/([0-9A-Za-z_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def sanitize_title(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()


def get_video_info(url: str):
    """Extract full video metadata."""
    opts = get_yt_dlp_options({"extract_flat": False})
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def scrape_community_post(url: str) -> Dict:
    """Scrape images and text from a YouTube Community Post."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }
    resp = requests.get(url, headers=headers, timeout=15)
    text = resp.text

    # Extract initial data JSON
    images = []
    post_text = ""

    # Look for image URLs in YT initial data
    img_matches = re.findall(
        r'https://yt3\.(?:ggpht|googleusercontent)\.com/[a-zA-Z0-9_-]+', text
    )
    for img in img_matches:
        full_img = img + "=s0"
        if full_img not in images and "yt4.ggpht.com" not in img:
            images.append(full_img)

    # Secondary pattern for images
    img_matches2 = re.findall(
        r'"url":"(https://[^"]+(?:ggpht|googleusercontent)[^"]+)"', text
    )
    for img in img_matches2:
        clean = img.replace("\\u0026", "&")
        # Ensure highest resolution s0
        high_res = re.sub(r"=s\d+.*", "=s0", clean)
        if high_res not in images:
            images.append(high_res)

    # Extract post caption
    title_match = re.search(r'<meta property="og:description" content="([^"]+)"', text)
    if title_match:
        post_text = html.unescape(title_match.group(1))

    return {"text": post_text, "images": images}


# -------------------- BOT HANDLERS --------------------


@app.on_message(filters.command("start"))
async def start_handler(client: Client, message: Message):
    user_id = message.from_user.id
    runtime_state.record_user_activity(user_id)

    welcome_text = (
        f"👋 **Namaste {message.from_user.first_name}!**\n\n"
        f"Main hoon aapka **YouTube Pro Downloader Bot** 🚀\n\n"
        f"📌 **Aap kya-kya download kar sakte hain:**\n"
        f"• 🎬 **Video + Audio** (Sabhi qualities: 4K, 1080p, 720p, 480p, 360p)\n"
        f"• 🔇 **Video Only (Mute)** (Bina sound ke direct video)\n"
        f"• 🎵 **Audio Only (MP3)** (High Quality 320kbps Music with Cover)\n"
        f"• 🖼️ **YouTube Community Posts** (Saari high-res photos ek sath)\n\n"
        f"👇 **Bas kisi bhi YouTube Video ya Post ki link yahan bhejo!**"
    )

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📢 Updates Channel", url=CHANNEL_URL
                ),
            ],
            [
                InlineKeyboardButton("💡 How to Use / Help", callback_data="help_menu"),
            ],
        ]
    )

    await message.reply_text(
        welcome_text,
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )


@app.on_message(filters.command("help"))
async def help_handler(client: Client, message: Message):
    help_text = (
        "📖 **YouTube Downloader Bot Guide:**\n\n"
        "1️⃣ **YouTube Video link bhejo:**\n"
        "   - Bot aapse puchega: `Video+Audio`, `Video Only (Mute)` ya `Audio Only`.\n"
        "2️⃣ **Agar Video select kiya:**\n"
        "   - Quality choose karein (1080p, 720p, 480p, etc.) aur video deliver ho jayegi.\n"
        "3️⃣ **Agar Audio select kiya:**\n"
        "   - Turant bina kuch pooche highest quality MP3 song download ho jayega.\n"
        "4️⃣ **Community Post:**\n"
        "   - Post link bhejne par saari images ek album me download hongi."
    )
    await message.reply_text(help_text)


@app.on_message(filters.command("stats"))
async def stats_handler(client: Client, message: Message):
    if message.from_user.id not in ADMINS:
        return

    uptime_seconds = int(time.time() - BOT_START_TIME)
    uptime_hours = uptime_seconds // 3600
    uptime_minutes = (uptime_seconds % 3600) // 60

    stats_text = (
        f"📊 **Bot Server Statistics:**\n\n"
        f"👥 **Total Users:** `{runtime_state.get_known_users_count()}`\n"
        f"📥 **Total Downloads:** `{runtime_state.total_downloads}`\n"
        f"⏳ **Uptime:** `{uptime_hours}h {uptime_minutes}m`\n"
        f"🔒 **Mode:** `{'Whitelist (Private)' if WHITELIST_ENABLED else 'Public (Everyone)'}`\n"
        f"🚀 **FFmpeg:** `{FFMPEG_PATH}`"
    )
    await message.reply_text(stats_text)


@app.on_message(filters.command("broadcast") & filters.user(ADMINS))
async def broadcast_handler(client: Client, message: Message):
    if not message.reply_to_message and len(message.command) < 2:
        await message.reply_text(
            "⚠️ **Usage:** `/broadcast <message>` ya kisi message ko reply karke `/broadcast` likhein."
        )
        return

    users = runtime_state.get_all_known_users()
    msg = await message.reply_text(
        f"📢 Broadcast shuru ho raha hai ({len(users)} users)..."
    )

    success, failed = 0, 0
    for uid in users:
        try:
            if message.reply_to_message:
                await message.reply_to_message.copy(uid)
            else:
                text_to_send = message.text.split(None, 1)[1]
                await client.send_message(uid, text_to_send)
            success += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1

    await msg.edit_text(
        f"✅ **Broadcast Complete!**\n\n🎯 Success: `{success}`\n❌ Failed: `{failed}`"
    )


# -------------------- YOUTUBE LINK PROCESSING --------------------


@app.on_message(
    filters.text
    & filters.regex(
        r"(https?://)?(www\.|m\.)?(youtube\.com|youtu\.be|music\.youtube\.com)/.+"
    )
)
async def process_youtube_link(client: Client, message: Message):
    user_id = message.from_user.id
    runtime_state.record_user_activity(user_id)

    if WHITELIST_ENABLED and not runtime_state.is_user_whitelisted(user_id):
        await message.reply_text(
            "🔒 **Bot is in private mode.** You are not authorized to use this bot."
        )
        return

    url = message.text.strip()

    # Check if it's a Community Post
    if "/post/" in url or "/community" in url:
        status_msg = await message.reply_text("🔍 **YouTube Post analyze ho raha hai...**")
        try:
            post_data = await asyncio.get_running_loop().run_in_executor(
                thread_pool, scrape_community_post, url
            )
            images = post_data.get("images", [])
            caption = post_data.get("text", "")

            if not images:
                await status_msg.edit_text("❌ **Is post me koi image nahi mili.**")
                return

            await status_msg.edit_text(
                f"📥 **{len(images)} Images download aur upload ho rahi hain...**"
            )

            # If multiple images, send as Album
            if len(images) > 1:
                media_group = []
                for i, img_url in enumerate(images[:10]):
                    cap = (
                        f"🖼️ **YouTube Post Images ({i+1}/{len(images)})**\n\n{caption[:900]}"
                        if i == 0
                        else ""
                    )
                    media_group.append(InputMediaPhoto(media=img_url, caption=cap))
                await client.send_media_group(chat_id=message.chat.id, media=media_group)
            else:
                cap = f"🖼️ **YouTube Post Image**\n\n{caption[:900]}"
                await client.send_photo(
                    chat_id=message.chat.id, photo=images[0], caption=cap
                )

            await status_msg.delete()
            runtime_state.increment_download_count()
            return
        except Exception as e:
            logging.error(f"Error scraping post: {e}")
            await status_msg.edit_text(f"❌ Post download karne me error: {str(e)}")
            return

    # Standard Video URL Processing
    video_id = extract_video_id(url)
    if not video_id:
        await message.reply_text("❌ **Invalid YouTube URL.** Kripya sahi video link bhejein.")
        return

    status_msg = await message.reply_text("🔍 **Video analyze ki jaa rahi hai...**")

    try:
        info = await asyncio.get_running_loop().run_in_executor(
            thread_pool, get_video_info, f"https://www.youtube.com/watch?v={video_id}"
        )
        title = info.get("title", "YouTube Video")
        duration = info.get("duration", 0)
        uploader = info.get("uploader", "YouTube")
        views = info.get("view_count", 0)

        mins, secs = divmod(duration, 60)
        hours, mins = divmod(mins, 60)
        dur_str = (
            f"{hours:02d}:{mins:02d}:{secs:02d}"
            if hours
            else f"{mins:02d}:{secs:02d}"
        )

        menu_text = (
            f"🎬 **{title}**\n\n"
            f"👤 **Channel:** `{uploader}`\n"
            f"⏳ **Duration:** `{dur_str}` | 👁️ **Views:** `{views:,}`\n\n"
            f"👇 **Aapko kis format me download karna hai?**"
        )

        # 3 Main Mode Buttons
        buttons = [
            [
                InlineKeyboardButton(
                    "🎥 Video + Audio (Normal)",
                    callback_data=f"mode_va_{video_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    "🔇 Video Only (Mute)",
                    callback_data=f"mode_vo_{video_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    "🎵 Audio Only (MP3 Song)",
                    callback_data=f"mode_ao_{video_id}",
                )
            ],
            [
                InlineKeyboardButton("📢 Updates Channel", url=CHANNEL_URL),
            ],
        ]

        await status_msg.edit_text(menu_text, reply_markup=InlineKeyboardMarkup(buttons))

    except Exception as e:
        logging.error(f"Error extracting video info: {e}")
        await status_msg.edit_text(f"❌ Video scan karne me error: {str(e)}")


# -------------------- CALLBACK QUERY HANDLER --------------------


@app.on_callback_query()
async def callback_handler(client: Client, query):
    data = query.data
    user_id = query.from_user.id
    runtime_state.record_user_activity(user_id)

    if data == "help_menu":
        await query.answer()
        await query.message.reply_text(
            "💡 **Help:** Bas koi bhi YouTube Video ya Post link bhejo, fir mode aur quality select karo!"
        )
        return

    # Mode 1: Video + Audio Selection Screen
    if data.startswith("mode_va_"):
        video_id = data.replace("mode_va_", "")
        await query.answer("Fetching available qualities...")
        await render_qualities_menu(query, video_id, mode="va")
        return

    # Mode 2: Video Only (Mute) Selection Screen
    if data.startswith("mode_vo_"):
        video_id = data.replace("mode_vo_", "")
        await query.answer("Fetching available qualities...")
        await render_qualities_menu(query, video_id, mode="vo")
        return

    # Mode 3: Audio Only (MP3) -> Immediate Download! No extra questions asked.
    if data.startswith("mode_ao_"):
        video_id = data.replace("mode_ao_", "")
        await query.answer("Downloading MP3 Audio...")
        await download_mp3_audio(client, query.message, video_id)
        return

    # Download execution for selected quality
    # Format: dl_{mode}_{format_id}_{video_id}
    if data.startswith("dl_"):
        parts = data.split("_")
        mode = parts[1]  # "va" or "vo"
        format_id = parts[2]
        video_id = "_".join(parts[3:])
        await query.answer("Download shuru ho raha hai...")
        await download_custom_video(client, query.message, video_id, format_id, mode=mode)
        return


async def render_qualities_menu(query, video_id: str, mode: str):
    """Show distinct resolution buttons for Video+Audio or Video-Only."""
    try:
        url = f"https://www.youtube.com/watch?v={video_id}"
        info = await asyncio.get_running_loop().run_in_executor(
            thread_pool, get_video_info, url
        )
        formats = info.get("formats", [])
        title = info.get("title", "Video")

        # Group and filter unique heights
        unique_resolutions = {}
        for f in formats:
            height = f.get("height")
            vcodec = f.get("vcodec", "none")
            if not height or vcodec == "none" or "storyboard" in vcodec:
                continue

            # Pick best format ID for each distinct height
            if height not in unique_resolutions:
                unique_resolutions[height] = f["format_id"]

        # Sort heights descending
        sorted_heights = sorted(unique_resolutions.keys(), reverse=True)

        keyboard = []
        row = []
        for h in sorted_heights:
            label = f"{h}p"
            if h >= 2160:
                label += " (4K)"
            elif h >= 1440:
                label += " (2K)"
            elif h >= 1080:
                label += " (FHD)"
            elif h >= 720:
                label += " (HD)"

            fmt_id = unique_resolutions[h]
            btn = InlineKeyboardButton(
                label,
                callback_data=f"dl_{mode}_{fmt_id}_{video_id}",
            )
            row.append(btn)
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

        # Back button
        keyboard.append(
            [InlineKeyboardButton("⬅️ Back to Main Options", callback_data=f"back_{video_id}")]
        )

        mode_name = "🎥 Video + Audio" if mode == "va" else "🔇 Video Only (Mute)"
        text = (
            f"🎬 **{title}**\n\n"
            f"📌 **Selected Mode:** `{mode_name}`\n"
            f"🎯 **Konsi Quality me download karna hai?**"
        )
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    except Exception as e:
        logging.error(f"Error rendering qualities: {e}")
        await query.message.edit_text(f"❌ Error fetching qualities: {str(e)}")


async def download_mp3_audio(client: Client, message: Message, video_id: str):
    """Download highest quality MP3 with thumbnail and tags directly."""
    status_msg = await message.edit_text("🎵 **Best Quality MP3 download ho raha hai...**")
    url = f"https://www.youtube.com/watch?v={video_id}"
    req_id = str(uuid.uuid4())[:8]
    temp_dir = os.path.join(DOWNLOAD_PATH, f"audio_{req_id}")
    os.makedirs(temp_dir, exist_ok=True)

    try:
        tracker = ProgressTracker(status_msg, action="🎵 Audio Downloading")

        def run_dl():
            out_template = os.path.join(temp_dir, "%(title)s.%(ext)s")
            ydl_opts = get_yt_dlp_options(
                {
                    "format": "bestaudio/best",
                    "outtmpl": out_template,
                    "writethumbnail": True,
                    "postprocessors": [
                        {
                            "key": "FFmpegExtractAudio",
                            "preferredcodec": "mp3",
                            "preferredquality": "320",
                        },
                        {"key": "EmbedThumbnail"},
                        {"key": "FFmpegMetadata"},
                    ],
                }
            )
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(url, download=True)

        info = await asyncio.get_running_loop().run_in_executor(thread_pool, run_dl)
        title = info.get("title", "Audio")
        uploader = info.get("uploader", "Unknown Artist")
        duration = info.get("duration", 0)

        # Locate converted MP3
        mp3_files = [
            os.path.join(temp_dir, f)
            for f in os.listdir(temp_dir)
            if f.endswith(".mp3")
        ]
        if not mp3_files:
            raise Exception("MP3 file generate nahi ho paayi.")

        mp3_path = mp3_files[0]
        thumb_path = None
        for ext in [".jpg", ".png", ".webp"]:
            possible_thumb = mp3_path.rsplit(".", 1)[0] + ext
            if os.path.exists(possible_thumb):
                thumb_path = possible_thumb
                break

        await status_msg.edit_text("📤 **Telegram par MP3 upload ho raha hai...**")
        upload_tracker = ProgressTracker(status_msg, action="📤 Uploading MP3")

        await client.send_audio(
            chat_id=message.chat.id,
            audio=mp3_path,
            title=title,
            performer=uploader,
            duration=duration,
            thumb=thumb_path if thumb_path and os.path.exists(thumb_path) else None,
            caption=f"🎵 **{title}**\n👤 `{uploader}`\n\n🤖 @YouTube_Downloader_Bot",
            progress=upload_tracker.update,
        )

        await status_msg.delete()
        runtime_state.increment_download_count()

    except Exception as e:
        logging.error(f"Error in MP3 download: {e}")
        await status_msg.edit_text(f"❌ MP3 download error: {str(e)}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


async def download_custom_video(
    client: Client, message: Message, video_id: str, format_id: str, mode: str
):
    """Download Video+Audio or Video-Only (Mute) for selected quality."""
    mode_name = "Video+Audio" if mode == "va" else "Video-Only (Mute)"
    status_msg = await message.edit_text(f"⚡ **{mode_name} download shuru ho raha hai...**")
    url = f"https://www.youtube.com/watch?v={video_id}"
    req_id = str(uuid.uuid4())[:8]
    temp_dir = os.path.join(DOWNLOAD_PATH, f"vid_{req_id}")
    os.makedirs(temp_dir, exist_ok=True)

    try:
        out_template = os.path.join(temp_dir, "raw_video.%(ext)s")

        # Select format specification based on mode
        if mode == "va":
            # Video + Best Audio merged
            format_spec = f"{format_id}+bestaudio/best"
        else:
            # Video Only (no audio)
            format_spec = f"{format_id}/bestvideo"

        def run_dl():
            ydl_opts = get_yt_dlp_options(
                {
                    "format": format_spec,
                    "outtmpl": out_template,
                    "writethumbnail": True,
                    "merge_output_format": "mp4",
                }
            )
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(url, download=True)

        info = await asyncio.get_running_loop().run_in_executor(thread_pool, run_dl)
        title = info.get("title", "Video")
        duration = info.get("duration", 0)
        width = info.get("width", 0)
        height = info.get("height", 0)

        # Locate raw downloaded file
        downloaded_files = [
            os.path.join(temp_dir, f)
            for f in os.listdir(temp_dir)
            if not f.endswith((".jpg", ".png", ".webp", ".temp"))
        ]
        if not downloaded_files:
            raise Exception("Video download fail ho gaya.")

        raw_file = downloaded_files[0]
        final_video_file = os.path.join(temp_dir, f"{sanitize_title(title)}.mp4")

        # If Mode is Video-Only (Mute), strip audio stream completely with FFmpeg
        if mode == "vo":
            await status_msg.edit_text("🔇 **Audio remove karke mute video process ho rahi hai...**")
            cmd = [
                FFMPEG_PATH,
                "-y",
                "-i",
                raw_file,
                "-c:v",
                "copy",
                "-an",
                final_video_file,
            ]
            res = subprocess.run(cmd, capture_output=True)
            if res.returncode != 0:
                # Fallback to copy
                shutil.copy(raw_file, final_video_file)
        else:
            # Ensure mp4 container
            if raw_file.endswith(".mp4"):
                final_video_file = raw_file
            else:
                cmd = [
                    FFMPEG_PATH,
                    "-y",
                    "-i",
                    raw_file,
                    "-c",
                    "copy",
                    final_video_file,
                ]
                subprocess.run(cmd, capture_output=True)
                if not os.path.exists(final_video_file):
                    final_video_file = raw_file

        # Locate thumbnail
        thumb_path = None
        for f in os.listdir(temp_dir):
            if f.endswith((".jpg", ".png", ".webp")):
                thumb_path = os.path.join(temp_dir, f)
                break

        await status_msg.edit_text("📤 **Telegram par Video upload ho rahi hai...**")
        upload_tracker = ProgressTracker(status_msg, action="📤 Uploading Video")

        cap = (
            f"🎬 **{title}**\n"
            f"📌 **Format:** `{mode_name}`\n"
            f"📺 **Resolution:** `{height}p`\n\n"
            f"🤖 @YouTube_Downloader_Bot"
        )

        await client.send_video(
            chat_id=message.chat.id,
            video=final_video_file,
            caption=cap,
            duration=duration,
            width=width,
            height=height,
            thumb=thumb_path if thumb_path and os.path.exists(thumb_path) else None,
            supports_streaming=True,
            progress=upload_tracker.update,
        )

        await status_msg.delete()
        runtime_state.increment_download_count()

    except Exception as e:
        logging.error(f"Error in video download: {e}")
        await status_msg.edit_text(f"❌ Video download error: {str(e)}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def start_render_health_server():
    """Start a lightweight HTTP server on $PORT for Render Web Service health checks."""
    port = int(os.getenv("PORT", "8080"))

    class HealthHandler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"YouTube Pro Downloader Bot is Running and Healthy!\n")

        def log_message(self, format, *args):
            return  # silence standard request logs

    def run_server():
        try:
            with socketserver.TCPServer(("", port), HealthHandler) as httpd:
                logging.info(f"Health check HTTP server listening on port {port}")
                httpd.serve_forever()
        except Exception as e:
            logging.warning(f"Could not start health check server on port {port}: {e}")

    t = threading.Thread(target=run_server, daemon=True)
    t.start()


# -------------------- MAIN PROCESS RUNNER --------------------

if __name__ == "__main__":
    def signal_handler(signum, frame):
        logging.info("Shutting down gracefully...")
        try:
            app.stop()
        except Exception:
            pass
        sys.exit(0)

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    cleanup_session_locks()
    clear_downloads_folder()
    start_render_health_server()

    logging.info("Starting YouTube Pro Downloader Telegram Bot...")
    logging.info(f"Bot Token: {BOT_TOKEN[:15]}...")
    logging.info(f"Admins: {ADMINS}")
    logging.info(f"Channel: {CHANNEL_URL}")

    try:
        app.run()
    except Exception as e:
        logging.critical(f"Fatal error while running bot: {e}")
        sys.exit(1)

