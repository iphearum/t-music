import os
import re
import json
import asyncio
import warnings
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import yt_dlp
import nest_asyncio

yt_dlp.YoutubeDL({'ffmpeg_location': '/usr/bin/ffmpeg'})

warnings.filterwarnings("ignore", category=RuntimeWarning)

BOT_TOKEN = '7726886049:AAGfpWA4i8tF2iHV5RYfA5scuHh02LkjtF8'
CACHE_FILE = './cache.json'


# Cache structure: {video_id: {'file_id': str, 'chat_id': int, 'message_id': int, 'title': str}}
cache = {}

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


def extract_video_id(url):
    """Extract YouTube video ID"""
    patterns = [
        r'(?:v=|/)([0-9A-Za-z_-]{11}).*',
        r'(?:embed/|v/|youtu.be/)([0-9A-Za-z_-]{11})',
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
    """Synchronous download function"""
    with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
        info = ydl.extract_info(url, download=True)
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
        return
    
    # Check cache first
    if video_id in cache:
        cached = cache[video_id]
        
        # Try forward (fastest)
        try:
            await context.bot.forward_message(
                chat_id=msg.chat_id,
                from_chat_id=cached['chat_id'],
                message_id=cached['message_id'],
            )
            print(f"⚡ Forwarded: {cached.get('title', video_id)}")
            return
        except:
            pass
        
        # Try file_id (fast)
        try:
            await msg.reply_audio(audio=cached['file_id'])
            print(f"⚡ Sent try: {cached.get('title', video_id)}")
            return
        except:
            # Cache invalid
            del cache[video_id]
            save_cache()
    
    # Download needed
    status = await msg.reply_text("⏳")
    
    try:
        # Download
        result = await download_audio(url, video_id)
        
        # Upload
        with open(result['file'], 'rb') as f:
            sent = await msg.reply_audio(
                audio=f,
                title=result['title'],
                duration=result['duration'],
                performer="YouTube",
                caption="@tmusicamdia_bot",
            )
        
        # Cache it
        cache[video_id] = {
            'file_id': sent.audio.file_id,
            'chat_id': sent.chat_id,
            'message_id': sent.message_id,
            'title': result['title']
        }
        save_cache()
        
        await status.delete()
        print(f"✅ Downloaded: {result['title']}")
        
        # Cleanup
        try:
            os.remove(result['file'])
        except:
            pass
            
    except Exception as e:
        error_msg = str(e)[:100]
        await status.edit_text(
            f"❌ Error: please try again.\n\n"
            f"ABA account for support:\n"
            f"Account-holder name: NOP PHEARUM\nAccount number: 001 160 500\n"
            f"To send payment, you can also use this link:\n"
            f"https://pay.ababank.com/oRF8/112ju0rp",
            parse_mode='Markdown'
        )
        print(f"❌ Error: {error_msg}")
        
        
if __name__ == '__main__':
    """Start the bot"""
    load_cache()

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
        await app.run_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES
        )

    asyncio.run(main())