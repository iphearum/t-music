import os
import re
import json
import asyncio
import warnings
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
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
FILE_CACHE_FILE = 'file_cache.json'
CACHE_DURATION_HOURS = 1

cache = {}
file_cache = {}

YDL_OPTS = {
    'format': 'bestaudio[ext=m4a]/bestaudio/best',
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'mp3',
        'preferredquality': '128',
    }],
    'outtmpl': 'temp_%(id)s.%(ext)s',
    'quiet': True,
    'no_warnings': True,
    'nocheckcertificate': True,
    'socket_timeout': 30,
    'extract_flat': False,
}

PLAYLIST_PATTERN = re.compile(r'[?&]list=([^&]+)')
VIDEO_PATTERNS = [
    re.compile(r'(?:v=|/)([0-9A-Za-z_-]{11}).*'),
    re.compile(r'(?:embed/|v/|watch\?v=)([^"&?/\s]{11})'),
    re.compile(r'youtu\.be/([^"&?/\s]{11})'),
]
URL_PATTERN = re.compile(r'https?://[^\s]+')


def load_json_file(filepath, default=None):
    try:
        if os.path.exists(filepath):
            with open(filepath, 'r') as f:
                return json.load(f)
    except Exception as e:
        print(f"⚠️ Error loading {filepath}: {e}")
    return default or {}


def save_json_file(filepath, data):
    try:
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"⚠️ Error saving {filepath}: {e}")


def load_cache():
    global cache, file_cache
    cache = load_json_file(CACHE_FILE)
    file_cache = load_json_file(FILE_CACHE_FILE)
    print(f"📦 Loaded {len(cache)} cached files, {len(file_cache)} file cache")


def save_cache():
    save_json_file(CACHE_FILE, cache)


def save_file_cache():
    save_json_file(FILE_CACHE_FILE, file_cache)


def cleanup_old_files():
    current_time = datetime.now().timestamp()
    cutoff_time = current_time - (CACHE_DURATION_HOURS * 3600)
    expired_ids = []
    
    for video_id, data in file_cache.items():
        if data['timestamp'] < cutoff_time:
            try:
                file_path = data['file_path']
                if os.path.exists(file_path):
                    os.remove(file_path)
                    print(f"🗑️ Cleaned up: {data.get('title', video_id)}")
            except Exception as e:
                print(f"⚠️ Cleanup error for {video_id}: {e}")
            expired_ids.append(video_id)
    
    for video_id in expired_ids:
        file_cache.pop(video_id, None)
    
    if expired_ids:
        save_file_cache()


async def periodic_cleanup():
    while True:
        await asyncio.sleep(600)
        cleanup_old_files()


def extract_video_id(url):
    playlist_match = PLAYLIST_PATTERN.search(url)
    if playlist_match:
        return playlist_match.group(1)
    
    for pattern in VIDEO_PATTERNS:
        match = pattern.search(url)
        if match:
            return match.group(1)
    
    return None


def extract_url(text):
    if not text:
        return None
    
    urls = URL_PATTERN.findall(text)
    for url in urls:
        if 'youtube.com' in url or 'youtu.be' in url:
            return url
    return None


def truncate_title(title, max_length=100):
    return title if len(title) <= max_length else title[:max_length-3] + '...'


def get_playlist_info(url):
    """Get playlist info without downloading"""
    ydl_opts = {**YDL_OPTS, 'extract_flat': True, 'skip_download': True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        if 'entries' in info:
            return [
                {
                    'video_id': entry['id'],
                    'title': truncate_title(entry.get('title', 'Unknown')),
                    'duration': entry.get('duration', 0)
                }
                for entry in info['entries'] if entry
            ]
    return []


def download_single_track(video_id):
    """Download only a single track"""
    url = f"https://www.youtube.com/watch?v={video_id}"
    with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
        info = ydl.extract_info(url, download=True)
        return {
            'title': truncate_title(info['title']),
            'file': f"temp_{video_id}.mp3",
            'duration': info.get('duration', 0),
            'video_id': video_id
        }


async def send_cached_track(context, msg, video_id):
    """Send track from cache (Telegram or file)"""
    # Try Telegram cache first
    if video_id in cache:
        cached = cache[video_id]
        try:
            await context.bot.forward_message(
                chat_id=msg.chat_id,
                from_chat_id=cached['chat_id'],
                message_id=cached['message_id'],
            )
            print(f"⚡ Forwarded: {cached.get('title', video_id)}")
            return True
        except:
            try:
                await msg.reply_audio(audio=cached['file_id'])
                print(f"⚡ Sent cached: {cached.get('title', video_id)}")
                return True
            except:
                cache.pop(video_id, None)
                save_cache()
    
    # Try file cache
    if video_id in file_cache:
        file_data = file_cache[video_id]
        file_path = file_data['file_path']
        
        if os.path.exists(file_path):
            age_hours = (datetime.now().timestamp() - file_data['timestamp']) / 3600
            if age_hours < CACHE_DURATION_HOURS:
                print(f"📁 Using file cache: {file_data.get('title', video_id)}")
                with open(file_path, 'rb') as f:
                    sent = await msg.reply_audio(
                        audio=f,
                        title=file_data.get('title'),
                        duration=file_data.get('duration'),
                        performer="YouTube",
                        caption="@tmusicamdia_bot",
                    )
                
                # Update Telegram cache
                cache[video_id] = {
                    'file_id': sent.audio.file_id,
                    'chat_id': sent.chat_id,
                    'message_id': sent.message_id,
                    'title': file_data['title']
                }
                save_cache()
                return True
    
    return False


async def download_and_send_track(msg, track_info):
    """Download and send a single track"""
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, download_single_track, track_info['video_id'])
    
    with open(result['file'], 'rb') as f:
        sent = await msg.reply_audio(
            audio=f,
            title=result['title'],
            duration=result['duration'],
            performer="YouTube",
            caption="@tmusicamdia_bot",
        )
    
    # Cache in both systems
    cache[track_info['video_id']] = {
        'file_id': sent.audio.file_id,
        'chat_id': sent.chat_id,
        'message_id': sent.message_id,
        'title': result['title']
    }
    save_cache()
    
    file_cache[track_info['video_id']] = {
        'file_path': result['file'],
        'timestamp': datetime.now().timestamp(),
        'title': result['title'],
        'duration': result['duration']
    }
    save_file_cache()
    
    print(f"✅ Downloaded & sent: {result['title']}")


async def start(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"🎵 *Telegram Music*\n\n"
        f"📦 Cached: {len(cache)} songs\n"
        f"📁 Files: {len(file_cache)} files\n"
        f"⚡ Send YouTube links!",
        parse_mode='Markdown'
    )


async def stats(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    cache_size = os.path.getsize(CACHE_FILE) if os.path.exists(CACHE_FILE) else 0
    await update.message.reply_text(
        f"📊 *Statistics*\n\n"
        f"📦 Exist songs: {len(cache)}\n"
        f"📁 File size: {len(file_cache)}\n"
        f"💾 Space: {cache_size:,} bytes",
        parse_mode='Markdown'
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    url = extract_url(msg.text or msg.caption)
    
    msg_err = (
        "❌ *Error occurred. Please try again.*\n\n"
        "☕ If this bot helped you, support me for a coffee\n"
        "💳 ABA Bank: NOP PHEARUM\n"
        "🔢 Account: 001 160 500\n\n"
        "💸 Quick payment:\n"
        "https://pay.ababank.com/oRF8/112ju0rp"
    )
    
    if not url:
        return
    
    video_id = extract_video_id(url)
    if not video_id:
        await msg.reply_text("❌ Invalid URL")
        return
    
    is_playlist = 'list=' in url
    
    if not is_playlist:
        # Single video - check cache first
        if await send_cached_track(context, msg, video_id):
            return
        
        status = await msg.reply_text("⏳ Downloading...")
        try:
            await download_and_send_track(msg, {'video_id': video_id})
            await status.delete()
        except Exception as e:
            await status.edit_text(msg_err, parse_mode='Markdown')
    
    else:
        # Playlist - get info first without downloading
        status = await msg.reply_text("⏳ Fetching playlist info...")
        
        try:
            loop = asyncio.get_running_loop()
            tracks = await loop.run_in_executor(None, get_playlist_info, url)
            
            if not tracks:
                await status.edit_text("❌ No tracks found")
                return
            
            # Separate cached and non-cached tracks
            cached_tracks = []
            to_download = []
            
            for track in tracks:
                track_id = track['video_id']
                if track_id in cache or track_id in file_cache:
                    cached_tracks.append(track)
                else:
                    to_download.append(track)
            
            await status.edit_text(
                f"⏳ Found {len(tracks)} tracks\n"
                f"⚡ Cached: {len(cached_tracks)}\n"
                f"⬇️ To download: {len(to_download)}"
            )
            
            # Send cached tracks first (fast)
            for track in cached_tracks:
                await send_cached_track(context, msg, track['video_id'])
            
            # Download and send new tracks
            for track in to_download:
                try:
                    await download_and_send_track(msg, track)
                except Exception as e:
                    print(f"❌ Error with {track['title']}: {e}")
            
            await status.delete()
            
        except Exception as e:
            await status.edit_text(msg_err, parse_mode='Markdown')


if __name__ == '__main__':
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

    async def main():
        asyncio.create_task(periodic_cleanup())
        await app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

    asyncio.run(main())
