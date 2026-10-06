# 🎬 YouTube Pro Downloader Telegram Bot

Powerful & modern Telegram bot to download YouTube Videos (with or without sound), Audio (MP3 320kbps), and Community Posts (Multi-Image albums) with interactive quality selection buttons.

---

## ✨ Features

- 🎥 **Video + Audio (Normal Mode):** Interactive quality menu (4K 2160p, 2K 1440p, 1080p FHD, 720p HD, 480p, 360p). Audio & Video seamlessly merged with FFmpeg.
- 🔇 **Video Only (Mute Mode):** Download video without any background sound or voice (audio stripped with `-an`).
- 🎵 **Audio Only (MP3):** Instant 1-click download of highest bitrate (320kbps) audio with album art and ID3 metadata tags (No extra questions asked).
- 🖼️ **YouTube Community Posts:** Download all images in a post (1 to 5+ photos) together in high resolution as a Telegram media album.
- ⚡ **Real-time Progress Bar:** Shows download speed (MB/s), percentage, and uploaded size with 5% interval flood-wait protection.
- 🛡️ **Anti-Bot & PO-Token Fix:** Built with Deno & `bgutil-ytdlp-pot-provider` to bypass YouTube's 403 / "Sign in to confirm you're not a bot" errors.
- 📢 **Channel Integration:** Direct button to your updates channel `https://t.me/MoviesGroupG3`.
- 👑 **Admin Commands:** `/broadcast`, `/stats`, and `/help`.

---

## ⚙️ Configuration (.env)

```env
API_ID=30720676
API_HASH=a078e3476750afbd6db7d6c5e5e658d9
BOT_TOKEN=8780722962:AAGL9e4IVewXxLoB-tuoCzI8b7Rfdkfm6XM
ADMINS=5566977478
CHANNEL_URL=https://t.me/MoviesGroupG3
WHITELIST_ENABLED=false
COOKIES_PATH=data/cookies.txt
```

---

## 🚀 How to Run

### Option 1: Docker (Recommended)
```bash
# Build & Run Container
docker compose up -d --build

# View real-time logs
docker compose logs -f
```

### Option 2: VPS / Linux Server (Direct Python)
```bash
# Install FFmpeg & Deno
sudo apt update && sudo apt install -y ffmpeg curl
curl -fsSL https://deno.land/install.sh | sh

# Install Python requirements
pip install -r requirements.txt

# Run bot
python main.py
```

---

## 🤖 Bot Commands
- `/start` - Start bot & show main menu
- `/help` - How to use the bot
- `/stats` - Check server & user statistics (Admin only)
- `/broadcast <msg>` - Send announcement to all users (Admin only)
