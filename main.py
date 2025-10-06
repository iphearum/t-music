import os
import re
import json
import asyncio
import warnings
import requests
from datetime import datetime
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
import yt_dlp
import nest_asyncio
from dotenv import load_dotenv

load_dotenv()
warnings.filterwarnings("ignore", category=RuntimeWarning)

# Configuration
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Error: BOT_TOKEN is not set in environment variables")

CACHE_FILE = os.getenv("CACHE_FILE", "cache.json")
FILE_CACHE_FILE = "file_cache.json"
MAX_CONCURRENT = os.getenv("MAX_CONCURRENT", 5)
CACHE_DURATION_HOURS = 1
THUMBNAILS_DIR = "thumbnails"

# Ensure directories exist
os.makedirs(THUMBNAILS_DIR, exist_ok=True)

cache = {}
file_cache = {}
msg_err = (
    "❌ *Error occurred. Please try again.*\n\n"
    "☕ If this bot helped you, support me for a coffee\n"
    "💳 ABA Bank: NOP PHEARUM\n"
    "🔢 Account: 001 160 500\n\n"
    "💸 Quick payment:\n"
    "https://pay.ababank.com/oRF8/112ju0rp"
)

YDL_OPTS = {
    "format": "bestaudio[ext=m4a]/bestaudio/best",
    "postprocessors": [
        {
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "128",
        }
    ],
    "outtmpl": "temp_%(id)s.%(ext)s",
    "quiet": True,
    "no_warnings": True,
    "nocheckcertificate": True,
    "socket_timeout": 30,
    "extract_flat": False,
    "ignoreerrors": True,
}

PLAYLIST_PATTERN = re.compile(r"[?&]list=([^&]+)")
VIDEO_PATTERNS = [
    re.compile(r"(?:v=|/)([0-9A-Za-z_-]{11}).*"),
    re.compile(r'(?:embed/|v/|watch\?v=)([^"&?/\s]{11})'),
    re.compile(r'youtu\.be/([^"&?/\s]{11})'),
]
URL_PATTERN = re.compile(r"https?://[^\s]+")


def load_json_file(filepath, default=None):
    """Load JSON file with error handling"""
    try:
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"⚠️ Error loading {filepath}: {e}")
    return default if default is not None else {}


def save_json_file(filepath, data):
    """Save JSON file with error handling"""
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except IOError as e:
        print(f"⚠️ Error saving {filepath}: {e}")


def load_cache():
    """Load both cache files"""
    global cache, file_cache
    cache = load_json_file(CACHE_FILE)
    file_cache = load_json_file(FILE_CACHE_FILE)
    print(f"📦 Loaded {len(cache)} cached files, {len(file_cache)} file cache")


def save_cache():
    """Save main cache"""
    save_json_file(CACHE_FILE, cache)


def save_file_cache():
    """Save file cache"""
    save_json_file(FILE_CACHE_FILE, file_cache)


def cleanup_old_files():
    """Remove expired files and update cache"""
    current_time = datetime.now().timestamp()
    cutoff_time = current_time - (CACHE_DURATION_HOURS * 3600)
    expired_ids = []

    for video_id, data in list(file_cache.items()):
        if data.get("timestamp", 0) < cutoff_time:
            try:
                file_path = data.get("file_path")
                if file_path and os.path.exists(file_path):
                    os.remove(file_path)
                    print(f"🗑️ Cleaned up: {data.get('title', video_id)}")
            except OSError as e:
                print(f"⚠️ Cleanup error for {video_id}: {e}")
            expired_ids.append(video_id)

    for video_id in expired_ids:
        file_cache.pop(video_id, None)

    if expired_ids:
        save_file_cache()
        print(f"🗑️ Removed {len(expired_ids)} expired entries")


async def periodic_cleanup():
    """Run cleanup every 10 minutes"""
    while True:
        await asyncio.sleep(600)
        cleanup_old_files()


def extract_video_id(url):
    """Extract video ID from YouTube URL"""
    playlist_match = PLAYLIST_PATTERN.search(url)
    if playlist_match:
        return playlist_match.group(1)

    for pattern in VIDEO_PATTERNS:
        match = pattern.search(url)
        if match:
            return match.group(1)

    return None


def extract_url(text):
    """Extract YouTube URL from text"""
    if not text:
        return None

    urls = URL_PATTERN.findall(text)
    for url in urls:
        if "youtube.com" in url or "youtu.be" in url:
            return url
    return None


def truncate_title(title, max_length=100):
    """Truncate title to max length"""
    if not title:
        return "Unknown"
    return title if len(title) <= max_length else title[: max_length - 3] + "..."


def get_playlist_info(url):
    """Get playlist info without downloading"""
    ydl_opts = {**YDL_OPTS, "extract_flat": True, "skip_download": True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info and "entries" in info:
                return [
                    {
                        "video_id": entry["id"],
                        "title": truncate_title(entry.get("title", "Unknown")),
                        "duration": entry.get("duration", 0),
                    }
                    for entry in info["entries"]
                    if entry and entry.get("id")
                ]
    except Exception as e:
        print(f"⚠️ Error getting playlist info: {e}")
    return []


def get_thumbnail(video_id):
    """Retrieve or download thumbnail image"""
    thumbnail_path = os.path.join(THUMBNAILS_DIR, f"{video_id}.jpg")

    if os.path.exists(thumbnail_path):
        return thumbnail_path

    url = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        with open(thumbnail_path, "wb") as f:
            f.write(response.content)
        print(f"✅ Downloaded thumbnail for {video_id}")
        return thumbnail_path
    except requests.RequestException as e:
        print(f"⚠️ Failed to download thumbnail for {video_id}: {e}")
        return None


def download_single_track(video_id):
    """Download only a single track"""
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
            info = ydl.extract_info(url, download=True)
            return {
                "title": truncate_title(info.get("title", "Unknown")),
                "file": f"temp_{video_id}.mp3",
                "duration": info.get("duration", 0),
                "video_id": video_id,
            }
    except Exception as e:
        print(f"⚠️ Download error for {video_id}: {e}")
        raise


async def send_cached_track(context, msg, video_id):
    """Send track from cache (Telegram or file)"""
    # Try Telegram cache first
    if video_id in cache:
        cached = cache[video_id]
        try:
            await context.bot.forward_message(
                chat_id=msg.chat_id,
                from_chat_id=cached["chat_id"],
                message_id=cached["message_id"],
            )
            print(f"⚡ Forwarded: {cached.get('title', video_id)}")
            return True
        except Exception as e:
            print(f"⚠️ Forward failed: {e}")
            try:
                await msg.reply_audio(audio=cached["file_id"])
                print(f"⚡ Sent cached: {cached.get('title', video_id)}")
                return True
            except Exception as e2:
                print(f"⚠️ Cache send failed: {e2}")
                cache.pop(video_id, None)
                save_cache()

    # Try file cache
    if video_id in file_cache:
        file_data = file_cache[video_id]
        file_path = file_data.get("file_path")

        if file_path and os.path.exists(file_path):
            age_hours = (
                datetime.now().timestamp() - file_data.get("timestamp", 0)
            ) / 3600
            if age_hours < CACHE_DURATION_HOURS:
                print(f"📁 Using file cache: {file_data.get('title', video_id)}")
                try:
                    with open(file_path, "rb") as f:
                        sent = await msg.reply_audio(
                            audio=f,
                            title=file_data.get("title", "Unknown"),
                            duration=file_data.get("duration", 0),
                            performer="YouTube",
                            caption="@tmusicamdia_bot",
                        )

                    # Update Telegram cache
                    cache[video_id] = {
                        "file_id": sent.audio.file_id,
                        "chat_id": sent.chat_id,
                        "message_id": sent.message_id,
                        "title": file_data.get("title", "Unknown"),
                    }
                    save_cache()
                    return True
                except Exception as e:
                    print(f"⚠️ File cache send failed: {e}")
            else:
                # Remove expired file
                file_cache.pop(video_id, None)
                save_file_cache()

    return False


async def download_and_send_track(msg, track_info):
    """Download and send a single track"""
    video_id = track_info["video_id"]

    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, download_single_track, video_id)

        file_path = result["file"]
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Downloaded file not found: {file_path}")

        with open(file_path, "rb") as f:
            sent = await msg.reply_audio(
                audio=f,
                title=result["title"],
                duration=result["duration"],
                performer="YouTube",
                caption="@tmusicamdia_bot",
            )

        # Cache in both systems
        cache[video_id] = {
            "file_id": sent.audio.file_id,
            "chat_id": sent.chat_id,
            "message_id": sent.message_id,
            "title": result["title"],
        }
        save_cache()

        file_cache[video_id] = {
            "file_path": file_path,
            "timestamp": datetime.now().timestamp(),
            "title": result["title"],
            "duration": result["duration"],
        }
        save_file_cache()

        print(f"✅ Downloaded & sent: {result['title']}")
    except Exception as e:
        print(f"❌ Error downloading/sending {video_id}: {e}")
        # Cleanup partial download
        file_path = f"temp_{video_id}.mp3"
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except:
                pass
        raise


async def start(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    await update.message.reply_text(
        f"🎵 *Telegram Music Bot*\n\n"
        f"📦 Cached: {len(cache)} songs\n"
        f"📁 Files: {len(file_cache)} files\n"
        f"⚡ Send YouTube links to download music!\n\n"
        f"Commands:\n"
        f"/start - Show this message\n"
        f"/stats - Show statistics",
        parse_mode="Markdown",
    )


async def stats(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    """Handle /stats command"""
    cache_size = os.path.getsize(CACHE_FILE) if os.path.exists(CACHE_FILE) else 0
    file_cache_size = (
        os.path.getsize(FILE_CACHE_FILE) if os.path.exists(FILE_CACHE_FILE) else 0
    )

    # Calculate total file size
    total_file_size = 0
    for data in file_cache.values():
        file_path = data.get("file_path")
        if file_path and os.path.exists(file_path):
            total_file_size += os.path.getsize(file_path)

    # Calculate storage details
    thumbnail_count = (
        len([f for f in os.listdir(THUMBNAILS_DIR) if f.endswith(".jpg")])
        if os.path.exists(THUMBNAILS_DIR)
        else 0
    )
    thumbnail_size = (
        sum(
            os.path.getsize(os.path.join(THUMBNAILS_DIR, f))
            for f in os.listdir(THUMBNAILS_DIR)
            if f.endswith(".jpg")
        )
        if os.path.exists(THUMBNAILS_DIR)
        else 0
    )

    # Format sizes
    def format_size(bytes_size):
        for unit in ["B", "KB", "MB", "GB"]:
            if bytes_size < 1024.0:
                return f"{bytes_size:.2f} {unit}"
            bytes_size /= 1024.0
        return f"{bytes_size:.2f} TB"

    # Calculate cache hit rate
    total_requests = len(cache) + len(file_cache)
    cache_efficiency = (len(cache) / total_requests * 100) if total_requests > 0 else 0

    # Calculate oldest and newest cache entries
    oldest_cache = (
        min(file_cache.values(), key=lambda x: x.get("timestamp", 0))["timestamp"]
        if file_cache
        else None
    )
    newest_cache = (
        max(file_cache.values(), key=lambda x: x.get("timestamp", 0))["timestamp"]
        if file_cache
        else None
    )

    stats_message = (
        f"📊 *Bot Statistics*\n\n"
        f"🎵 *Audio Cache:*\n"
        f"  • Telegram cache: {len(cache)} songs\n"
        f"  • File cache: {len(file_cache)} songs\n"
        f"  • Cache efficiency: {cache_efficiency:.1f}%\n\n"
        f"💾 *Storage Usage:*\n"
        f"  • Cached audio files: {format_size(total_file_size)}\n"
        f"  • Thumbnails: {thumbnail_count} files ({format_size(thumbnail_size)})\n"
        f"  • Cache metadata: {format_size(cache_size)}\n"
        f"  • File metadata: {format_size(file_cache_size)}\n"
        f"  • *Total storage:* {format_size(total_file_size + thumbnail_size + cache_size + file_cache_size)}\n\n"
        f"⏱️ *Cache Duration:* {CACHE_DURATION_HOURS} hour(s)\n"
    )

    if oldest_cache and newest_cache:
        cache_age = (datetime.now().timestamp() - oldest_cache) / 3600
        stats_message += f"📅 *Oldest cache:* {cache_age:.1f}h ago\n"

    stats_message += f"\n💡 _Cache auto-cleans every 10 minutes_"

    await update.message.reply_text(stats_message, parse_mode="Markdown")


async def handle_single_video(msg, context, video_id):
    """Handle single video download and send"""
    if await send_cached_track(context, msg, video_id):
        return

    status = await msg.reply_text("⏳ Downloading...")
    try:
        await download_and_send_track(msg, {"video_id": video_id})
        await status.delete()
    except Exception as e:
        print(f"❌ Error handling single video: {e}")
        await status.edit_text(msg_err, parse_mode="Markdown")


async def send_cached_playlist_tracks(msg, context, cached_tracks):
    """Send all cached tracks from playlist"""
    for track in cached_tracks:
        try:
            await send_cached_track(context, msg, track["video_id"])
        except Exception as e:
            print(f"⚠️ Error sending cached track {track['video_id']}: {e}")


async def download_playlist_tracks(msg, status, to_download):
    """Download playlist tracks concurrently with progress updates"""
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    completed = {"count": 0}
    lock = asyncio.Lock()

    async def download_with_semaphore(track):
        async with semaphore:
            try:
                await download_and_send_track(msg, track)
                async with lock:
                    completed["count"] += 1
                    if completed["count"] % 3 == 0:
                        try:
                            await status.edit_text(
                                f"⬇️ Progress: {completed['count']}/{len(to_download)} tracks downloaded"
                            )
                        except Exception:
                            pass
            except Exception as e:
                print(f"❌ Error with {track['title']}: {e}")

    tasks = [download_with_semaphore(track) for track in to_download]
    await asyncio.gather(*tasks, return_exceptions=True)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming messages with YouTube URLs"""
    msg = update.message
    url = extract_url(msg.text or msg.caption)

    if not url:
        return

    video_id = extract_video_id(url)
    if not video_id:
        await msg.reply_text("❌ Invalid YouTube URL")
        return

    is_playlist = "list=" in url

    if not is_playlist:
        await handle_single_video(msg, context, video_id)
    else:
        status = await msg.reply_text("⏳ Fetching playlist info...")

        try:
            loop = asyncio.get_running_loop()
            tracks = await loop.run_in_executor(None, get_playlist_info, url)

            if not tracks:
                await status.edit_text("❌ No tracks found or invalid playlist")
                return

            # Separate cached and non-cached tracks
            cached_tracks = [
                t
                for t in tracks
                if t["video_id"] in cache or t["video_id"] in file_cache
            ]
            to_download = [
                t
                for t in tracks
                if t["video_id"] not in cache and t["video_id"] not in file_cache
            ]

            await status.edit_text(
                f"📋 Found {len(tracks)} tracks\n"
                f"⚡ Cached: {len(cached_tracks)}\n"
                f"⬇️ To download: {len(to_download)}\n\n"
                f"Sending cached tracks first..."
            )

            # Send cached tracks first
            await send_cached_playlist_tracks(msg, context, cached_tracks)

            # Download new tracks concurrently
            if to_download:
                await status.edit_text(
                    f"⬇️ Downloading {len(to_download)} new tracks with "
                    f"{min(3, len(to_download))} concurrent threads..."
                )
                await download_playlist_tracks(msg, status, to_download)

            await status.delete()
            await msg.reply_text(f"✅ Playlist complete! Sent {len(tracks)} tracks.")

        except Exception as e:
            print(f"❌ Playlist error: {e}")
            await status.edit_text(msg_err, parse_mode="Markdown")


def main():
    """Main entry point"""
    load_cache()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(MessageHandler(filters.TEXT | filters.CAPTION, handle_message))

    print("🤖 Bot started!")
    print(f"📦 Cache: {len(cache)} files")
    print(f"📁 File cache: {len(file_cache)} files")
    print("Press Ctrl+C to stop\n")

    nest_asyncio.apply()

    async def run():
        asyncio.create_task(periodic_cleanup())
        await app.run_polling(
            drop_pending_updates=True, allowed_updates=Update.ALL_TYPES
        )

    asyncio.run(run())


if __name__ == "__main__":
    main()
