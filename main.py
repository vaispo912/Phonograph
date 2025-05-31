import discord
from discord.ext import commands
import yt_dlp
import asyncio
import os
from concurrent.futures import ThreadPoolExecutor

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

# Simplified FFMPEG options
FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn -filter:a "volume=0.5"'
}

# Simple, working YDL options
YDL_OPTIONS = {
    'format': 'worstaudio/worst',  # Use worst quality to reduce load times
    'noplaylist': True,
    'quiet': True,
    'no_warnings': True,
    'ignoreerrors': True,
    'extractaudio': True,
    'audioformat': 'webm',
    'socket_timeout': 20,
    'retries': 1,
    'fragment_retries': 1,
}

class MusicBot(commands.Cog):
    def __init__(self, client):
        self.client = client
        self.queue = []
        self.executor = ThreadPoolExecutor(max_workers=2)
        self.is_playing = False
        self.current_song = None

    def sync_extract_info(self, search):
        """Synchronous version of extract_info for threading"""
        try:
            with yt_dlp.YoutubeDL(YDL_OPTIONS) as ydl:
                # Try direct URL first, then search
                if search.startswith('http'):
                    info = ydl.extract_info(search, download=False)
                else:
                    info = ydl.extract_info(f"ytsearch1:{search}", download=False)
                return info
        except Exception as e:
            print(f"Extract error: {e}")
            return None

    @commands.command()
    async def play(self, ctx, *, search):
        """Play a song from YouTube"""
        # Check if user is in voice channel
        if not ctx.author.voice:
            return await ctx.send("❌ You need to be in a voice channel!")
        
        # Connect to voice channel if not already connected
        if not ctx.voice_client:
            try:
                await ctx.author.voice.channel.connect()
                await ctx.send(f"🔗 Connected to **{ctx.author.voice.channel.name}**")
            except Exception as e:
                return await ctx.send(f"❌ Could not connect to voice channel: {e}")

        # Show typing indicator
        async with ctx.typing():
            try:
                # Run extraction in thread to avoid blocking
                loop = asyncio.get_event_loop()
                info = await loop.run_in_executor(self.executor, self.sync_extract_info, search)
                
                if not info:
                    return await ctx.send("❌ Could not find that song. Try:\n• A direct YouTube URL\n• A simpler search term\n• Checking if the video exists")

                # Handle search results
                if 'entries' in info:
                    if not info['entries']:
                        return await ctx.send("❌ No search results found.")
                    info = info['entries'][0]

                # Extract necessary info
                url = info.get('url')
                title = info.get('title', 'Unknown Title')
                duration = info.get('duration', 0)

                if not url:
                    return await ctx.send("❌ Could not get audio stream URL.")

                # Add to queue
                song_data = {
                    'url': url,
                    'title': title,
                    'duration': duration,
                    'requester': ctx.author.mention,
                    'ctx': ctx
                }
                
                self.queue.append(song_data)
                await ctx.send(f"✅ **{title}** added to queue! (Position: {len(self.queue)})")

            except Exception as e:
                return await ctx.send(f"❌ Error: {str(e)}\n\n**Try:**\n• Using a direct YouTube URL\n• Waiting a moment and trying again\n• A different song")

        # Start playing if nothing is currently playing
        if not self.is_playing:
            await self.play_next(ctx)

    def song_finished(self, error):
        """Callback when a song finishes playing"""
        if error:
            print(f'Player error: {error}')
        
        # Set playing status to False
        self.is_playing = False
        self.current_song = None
        
        # Schedule the next song to play
        asyncio.run_coroutine_threadsafe(self.play_next_auto(), self.client.loop)

    async def play_next_auto(self):
        """Automatically play next song (called from callback)"""
        if self.queue:
            # Get the context from the first song in queue
            song = self.queue[0]
            ctx = song.get('ctx')
            if ctx and ctx.voice_client:
                await self.play_next(ctx)

    async def play_next(self, ctx):
        """Play the next song in queue"""
        if not self.queue:
            self.is_playing = False
            self.current_song = None
            return await ctx.send("📭 Queue is empty.")

        if not ctx.voice_client:
            self.is_playing = False
            return await ctx.send("❌ Not connected to a voice channel.")

        if ctx.voice_client.is_playing():
            return  # Don't interrupt current song

        # Set playing status
        self.is_playing = True
        
        # Get next song
        song = self.queue.pop(0)
        self.current_song = song
        
        try:
            # Create audio source
            source = discord.FFmpegPCMAudio(song['url'], **FFMPEG_OPTIONS)
            
            # Play the song with proper callback
            ctx.voice_client.play(source, after=self.song_finished)
            
            # Send now playing message
            embed = discord.Embed(
                title="🎵 Now Playing",
                description=f"**{song['title']}**",
                color=0x00ff00
            )
            embed.add_field(name="Requested by", value=song['requester'], inline=True)
            if song['duration']:
                mins, secs = divmod(song['duration'], 60)
                embed.add_field(name="Duration", value=f"{mins}:{secs:02d}", inline=True)
            embed.add_field(name="Queue", value=f"{len(self.queue)} songs remaining", inline=True)
            
            await ctx.send(embed=embed)

        except Exception as e:
            await ctx.send(f"❌ Error playing **{song['title']}**: {str(e)}")
            self.is_playing = False
            self.current_song = None
            # Try next song if current one fails
            await self.play_next(ctx)

    @commands.command()
    async def skip(self, ctx):
        """Skip the current song"""
        if not ctx.voice_client:
            return await ctx.send("❌ Not connected to a voice channel.")
        
        if ctx.voice_client.is_playing():
            ctx.voice_client.stop()  # This will trigger the callback to play next
            await ctx.send("⏭️ Skipped!")
        else:
            await ctx.send("❌ Nothing is playing.")

    @commands.command()
    async def stop(self, ctx):
        """Stop playing and clear queue"""
        if ctx.voice_client:
            self.queue.clear()
            self.is_playing = False
            self.current_song = None
            ctx.voice_client.stop()
            await ctx.voice_client.disconnect()
            await ctx.send("⏹️ Stopped and disconnected.")
        else:
            await ctx.send("❌ Not connected to a voice channel.")

    @commands.command()
    async def pause(self, ctx):
        """Pause the current song"""
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.pause()
            await ctx.send("⏸️ Paused!")
        else:
            await ctx.send("❌ Nothing is playing.")

    @commands.command()
    async def resume(self, ctx):
        """Resume the paused song"""
        if ctx.voice_client and ctx.voice_client.is_paused():
            ctx.voice_client.resume()
            await ctx.send("▶️ Resumed!")
        else:
            await ctx.send("❌ Nothing is paused.")

    @commands.command()
    async def queue(self, ctx):
        """Show the current queue"""
        if not self.queue:
            return await ctx.send("📭 Queue is empty.")

        embed = discord.Embed(title="🎵 Music Queue", color=0x00ff00)
        
        queue_text = ""
        for i, song in enumerate(self.queue[:10], 1):
            queue_text += f"`{i}.` **{song['title']}** - {song['requester']}\n"
        
        if len(self.queue) > 10:
            queue_text += f"\n... and {len(self.queue) - 10} more songs"
        
        embed.description = queue_text
        embed.set_footer(text=f"Total songs: {len(self.queue)}")
        
        await ctx.send(embed=embed)

    @commands.command()
    async def clear(self, ctx):
        """Clear the queue"""
        self.queue.clear()
        await ctx.send("🗑️ Queue cleared!")

    @commands.command()
    async def nowplaying(self, ctx):
        """Show currently playing song"""
        if self.current_song and ctx.voice_client and ctx.voice_client.is_playing():
            embed = discord.Embed(
                title="🎵 Currently Playing",
                description=f"**{self.current_song['title']}**",
                color=0x00ff00
            )
            embed.add_field(name="Requested by", value=self.current_song['requester'], inline=True)
            if self.current_song['duration']:
                mins, secs = divmod(self.current_song['duration'], 60)
                embed.add_field(name="Duration", value=f"{mins}:{secs:02d}", inline=True)
            embed.add_field(name="Queue", value=f"{len(self.queue)} songs remaining", inline=True)
            await ctx.send(embed=embed)
        else:
            await ctx.send("❌ Nothing is currently playing.")

# Bot setup
client = commands.Bot(command_prefix="!", intents=intents)

@client.event
async def on_ready():
    print(f'✅ {client.user} is online and ready!')
    print(f'Bot is in {len(client.guilds)} servers')

@client.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ Missing required argument. Use `!help <command>` for usage.")
    else:
        print(f"Command error: {error}")
        await ctx.send("❌ An error occurred. Please try again.")

@client.command()
async def ping(ctx):
    """Check bot latency"""
    latency = round(client.latency * 1000)
    await ctx.send(f"Pong! Latency: {latency}ms")

async def main():
    await client.add_cog(MusicBot(client))
    await client.start('TOKEN HERE')

if __name__ == "__main__":
    asyncio.run(main())
