import os
import re
import json
import asyncio
import warnings
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import yt_dlp
import nest_asyncio
import concurrent.futures
from functools import partial
# import .env
from dotenv import load_dotenv
load_dotenv()


yt_dlp.YoutubeDL({'ffmpeg_location': '/usr/bin/ffmpeg'})

warnings.filterwarnings("ignore", category=RuntimeWarning)

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError("Error: BOT_TOKEN is not set in environment variables")

CACHE_FILE = os.getenv("CACHE_FILE", "cache.json")
FILE_CACHE_FILE = 'file_cache.json'
CACHE_DURATION_HOURS = 1

# Cache structure: {video_id: {'file_id': str, 'chat_id': int, 'message_id': int, 'title': str}}
cache = {}
file_cache = {}  # {video_id: {'file_path': str, 'timestamp': float, 'title': str, 'duration': int}}

# Optimized yt-dlp settings
YDL_OPTS = {
    'format': 'bestaudio[ext=m4a]/bestaudio/best',  # Prefer m4a (faster, smaller)
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'mp3',
        'preferredquality': '128',
    }],
    'outtmpl': 'temp_%(id)s.%(ext)s',
    'quiet': True,
    'no_warnings': True,
    'nocheckcertificate': True,
    'no_check_certificate': True,
    'prefer_insecure': True,
    'geo_bypass': True,
    'socket_timeout': 30,
}

def load_cache():
    """Load cache from file"""
    global cache
    try:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, 'r') as f:
                cache = json.load(f)
            print(f"📦 Loaded {len(cache)} cached files")
    except Exception as e:
        print(f"⚠️ Cache load error: {e}")
        cache = {}


def save_cache():
    """Save cache to file"""
    try:
        with open(CACHE_FILE, 'w') as f:
            json.dump(cache, f, indent=2)
    except Exception as e:
        print(f"⚠️ Cache save error: {e}")


def load_file_cache():
    global file_cache
    try:
        if os.path.exists(FILE_CACHE_FILE):
            with open(FILE_CACHE_FILE, 'r') as f:
                file_cache = json.load(f)
    except:
        file_cache = {}


def save_file_cache():
    try:
        with open(FILE_CACHE_FILE, 'w') as f:
            json.dump(file_cache, f)
    except:
        pass


def cleanup_old_files():
    """Remove files older than CACHE_DURATION_HOURS"""
    current_time = datetime.now().timestamp()
    expired_ids = []
    
    for video_id, data in file_cache.items():
        if current_time - data['timestamp'] > CACHE_DURATION_HOURS * 3600:
            try:
                if os.path.exists(data['file_path']):
                    os.remove(data['file_path'])
                    print(f"🗑️ Cleaned up: {data.get('title', video_id)}")
            except Exception as e:
                print(f"⚠️ Cleanup error: {e}")
            expired_ids.append(video_id)
    
    for video_id in expired_ids:
        del file_cache[video_id]
    
    if expired_ids:
        save_file_cache()


async def periodic_cleanup():
    """Run cleanup every 10 minutes"""
    while True:
        await asyncio.sleep(600)  # 10 minutes
        cleanup_old_files()


def extract_video_id(url):
    """Extract video_id or playlist_id from URL"""
    # Check for playlist
    playlist_match = re.search(r'[?&]list=([^&]+)', url)
    if playlist_match:
        return playlist_match.group(1)
    
    # Check for video
    patterns = [
        r'(?:v=|/)([0-9A-Za-z_-]{11}).*',
        r'(?:embed/|v/|watch\?v=)([^"&?/\s]{11})',
        r'youtu\.be/([^"&?/\s]{11})',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    
    return None


def extract_url(text):
    """Extract YouTube URL from text"""
    if not text:
        return None
    urls = re.findall(r'https?://[^\s]+', text)
    for url in urls:
        if any(x in url for x in ['youtube.com', 'youtu.be']):
            return url
    return None


def download_sync(url, video_id):
    """Synchronous download function for single video or playlist"""
    with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
        info = ydl.extract_info(url, download=True)
        
        # Check if the URL is a playlist or a single video
        if 'entries' in info:  # Playlist or album
            album = []
            for entry in info['entries']:
                entry_video_id = entry['id']
                album.append({
                    'title': (entry['title'][:97] + '...') if len(entry['title']) > 100 else entry['title'],
                    'file': f"temp_{entry_video_id}.mp3",
                    'duration': entry.get('duration', 0),
                })
            return album  # Return the album list
        else:  # Single video
            return {
                'title': (info['title'][:97] + '...') if len(info['title']) > 100 else info['title'],
                'file': f"temp_{video_id}.mp3",
                'duration': info.get('duration', 0),
            }


async def download_audio(url, video_id):
    """Async wrapper for download"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, download_sync, url, video_id)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    await update.message.reply_text(
        f"🎵 *Telegram Music*\n\n"
        f"📦 Total exist: {len(cache)} songs\n"
        f"⚡ Just send or forward YouTube links!",
        parse_mode='Markdown'
    )


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show statistics"""
    total_size = sum(1 for _ in cache)
    await update.message.reply_text(
        f"📊 *Statistics*\n\n"
        f"📦 Total song: {total_size}\n"
        f"💾 Space Size: {os.path.getsize(CACHE_FILE) if os.path.exists(CACHE_FILE) else 0} bytes",
        parse_mode='Markdown'
    )


async def clear_cache(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear cache"""
    global cache
    old_count = len(cache)
    cache = {}
    save_cache()
    await update.message.reply_text(f"🗑️ Cleared {old_count} cached files")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming messages"""
    msg = update.message
    url = extract_url(msg.text or msg.caption)
    
    if not url:
        return
    
    video_id = extract_video_id(url)
    if not video_id:
        await msg.reply_text("❌ Invalid URL")
        return
    
    # For playlists, we need to get all video IDs first
    is_playlist = 'list=' in url
    
    if not is_playlist:
        # Check Telegram file_id cache first (single video)
        if video_id in cache:
            cached = cache[video_id]
            try:
                await context.bot.forward_message(
                    chat_id=msg.chat_id,
                    from_chat_id=cached['chat_id'],
                    message_id=cached['message_id'],
                )
                print(f"⚡ Forwarded: {cached.get('title', video_id)}")
                return
            except:
                try:
                    await msg.reply_audio(audio=cached['file_id'])
                    print(f"⚡ Sent file_id: {cached.get('title', video_id)}")
                    return
                except:
                    del cache[video_id]
                    save_cache()
        
        # Check file cache (single video)
        if video_id in file_cache:
            file_data = file_cache[video_id]
            if os.path.exists(file_data['file_path']):
                age_hours = (datetime.now().timestamp() - file_data['timestamp']) / 3600
                if age_hours < CACHE_DURATION_HOURS:
                    print(f"📁 Using cached file: {file_data.get('title', video_id)}")
                    with open(file_data['file_path'], 'rb') as f:
                        sent = await msg.reply_audio(
                            audio=f,
                            title=file_data.get('title'),
                            duration=file_data.get('duration'),
                            performer="YouTube",
                            caption="@tmusicamdia_bot",
                        )
                    
                    cache[video_id] = {
                        'file_id': sent.audio.file_id,
                        'chat_id': sent.chat_id,
                        'message_id': sent.message_id,
                        'title': file_data.get('title')
                    }
                    save_cache()
                    return
    
    status = await msg.reply_text("⏳ Processing...")
    
    try:
        # Download
        result = await download_audio(url, video_id)
        
        if isinstance(result, list):  # Album/Playlist
            await status.edit_text(f"⏳ Found {len(result)} tracks...")
            
            async def send_track(track):
                """Send a single track with cache checking"""
                try:
                    track_video_id = os.path.splitext(os.path.basename(track['file']))[0].replace("temp_", "")
                    
                    # Check if this specific track is already cached
                    if track_video_id in cache:
                        try:
                            await context.bot.forward_message(
                                chat_id=msg.chat_id,
                                from_chat_id=cache[track_video_id]['chat_id'],
                                message_id=cache[track_video_id]['message_id'],
                            )
                            print(f"⚡ Forwarded cached: {track['title']}")
                            return
                        except:
                            pass
                    
                    # Send the track
                    with open(track['file'], 'rb') as f:
                        sent = await msg.reply_audio(
                            audio=f,
                            title=track['title'],
                            duration=track['duration'],
                            performer="YouTube",
                            caption="@tmusicamdia_bot",
                        )
                    
                    # Cache both systems
                    cache[track_video_id] = {
                        'file_id': sent.audio.file_id,
                        'chat_id': sent.chat_id,
                        'message_id': sent.message_id,
                        'title': track['title']
                    }
                    save_cache()
                    
                    file_cache[track_video_id] = {
                        'file_path': track['file'],
                        'timestamp': datetime.now().timestamp(),
                        'title': track['title'],
                        'duration': track['duration']
                    }
                    save_file_cache()
                    
                    print(f"✅ Sent: {track['title']}")
                except Exception as e:
                    print(f"❌ Error sending {track['title']}: {e}")
            
            # Send tracks concurrently
            tasks = [send_track(track) for track in result]
            await asyncio.gather(*tasks)
            
        else:  # Single video
            with open(result['file'], 'rb') as f:
                sent = await msg.reply_audio(
                    audio=f,
                    title=result['title'],
                    duration=result['duration'],
                    performer="YouTube",
                    caption="@tmusicamdia_bot",
                )
            
            cache[video_id] = {
                'file_id': sent.audio.file_id,
                'chat_id': sent.chat_id,
                'message_id': sent.message_id,
                'title': result['title']
            }
            save_cache()
            
            file_cache[video_id] = {
                'file_path': result['file'],
                'timestamp': datetime.now().timestamp(),
                'title': result['title'],
                'duration': result['duration']
            }
            save_file_cache()
        
        await status.delete()
        
    except Exception as e:
        await status.edit_text(f"❌ Error: {e}")


if __name__ == '__main__':
    """Start the bot"""
    load_cache()
    load_file_cache()

    # Build application
    app = Application.builder().token(BOT_TOKEN).build()

    # Add handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    # app.add_handler(CommandHandler("clear", clear_cache))
    app.add_handler(MessageHandler(
        filters.TEXT | filters.CAPTION, 
        handle_message
    ))

    print("🤖 Bot started!")
    print(f"📦 Cache: {len(cache)} files")
    print("Press Ctrl+C to stop\n")
    
    # Allow nested event loops
    nest_asyncio.apply()

    async def main():
        # Start periodic cleanup task
        asyncio.create_task(periodic_cleanup())
        await app.run_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES
        )

    asyncio.run(main())