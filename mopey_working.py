import discord
from discord.ext import commands
import os
import asyncio
import yt_dlp
from dotenv import load_dotenv
import urllib.parse, urllib.request, re

def run_bot():
    load_dotenv()
    TOKEN = os.getenv('discord_token')
    intents = discord.Intents.default()
    intents.message_content = True
    client = commands.Bot(command_prefix=".", intents=intents)

    queues = {}
    voice_clients = {}
    youtube_base_url = 'https://www.youtube.com/'
    youtube_results_url = youtube_base_url + 'results?'
    youtube_watch_url = youtube_base_url + 'watch?v='
    yt_dl_options = {"format": "bestaudio/best"}
    ytdl = yt_dlp.YoutubeDL(yt_dl_options)

    ffmpeg_options = {'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5','options': '-vn -filter:a "volume=0.25"'}

    @client.event
    async def on_ready():
        print(f'{client.user} is now jamming')

    async def play_next(ctx):
        if queues[ctx.guild.id] != []:
            link = queues[ctx.guild.id].pop(0)['link']
            await play(ctx, link=link)
    
    #  @commands.command(name="play", aliases=["p", "playing"], help="Plays a selected song")
    @client.command(name="play")
    async def play(ctx, *, link=None):
        try:
            # If no link is provided, check if the music is paused and resume it
            if link is None:
                if ctx.guild.id in voice_clients and voice_clients[ctx.guild.id].is_paused():
                    voice_clients[ctx.guild.id].resume()
                    await ctx.send("Resumed the music!")
                    return
                else:
                    await ctx.send("No music to play.")
                    return
                
            # Connect to the voice channel if not already connected
            if ctx.guild.id not in voice_clients or not voice_clients[ctx.guild.id].is_connected():
                voice_client = await ctx.author.voice.channel.connect()
                voice_clients[ctx.guild.id] = voice_client
            else:
                voice_client = voice_clients[ctx.guild.id]

            # Check if the link is a search query or direct YouTube link
            if youtube_base_url not in link:
                query_string = urllib.parse.urlencode({'search_query': link})
                content = urllib.request.urlopen(youtube_results_url + query_string)
                search_results = re.findall(r'/watch\?v=(.{11})', content.read().decode())
                link = youtube_watch_url + search_results[0]

            # Extract song information
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, lambda: ytdl.extract_info(link, download=False))
            song = data['url']
            title = data['title']

            # Add to queue if a song is already playing
            if voice_client.is_playing():
                if ctx.guild.id not in queues:
                    queues[ctx.guild.id] = []
                queues[ctx.guild.id].append({"title": title, "link": link})
                position = len(queues[ctx.guild.id])  # Get the current position in the queue
                await ctx.send(f"Added to queue: **{title}** (Position: {position})")

            else:
                # Play the song if nothing is playing
                player = discord.FFmpegOpusAudio(song, **ffmpeg_options)

                # Play the song and set up to play the next song in the queue
                voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), client.loop))
                await ctx.send(f"Now playing: {title}")

        except Exception as e:
            print(e)
            await ctx.send("An error occurred while trying to play the song.")

    @client.command(name="clear_queue")
    async def clear_queue(ctx):
        if ctx.guild.id in queues:
            queues[ctx.guild.id].clear()
            await ctx.send("Queue cleared!")
        else:
            await ctx.send("There is no queue to clear")

    @client.command(name="pause")
    async def pause(ctx):
        try:
            voice_clients[ctx.guild.id].pause()
            await ctx.send("Paused the music!")
        except Exception as e:
            print(e)

    @client.command(name="resume")
    async def resume(ctx):
        try:
            voice_clients[ctx.guild.id].resume()
            await ctx.send("Resumed the music!")
        except Exception as e:
            print(e)

    @client.command(name="stop")
    async def stop(ctx):
        try:
            voice_clients[ctx.guild.id].stop()
            await voice_clients[ctx.guild.id].disconnect()
            del voice_clients[ctx.guild.id]
            await ctx.send("Stopped the music and left the voice channel!")
        except Exception as e:
            print(e)

    @client.command(name="queue")
    async def queue(ctx):
        if ctx.guild.id in queues and queues[ctx.guild.id]:
            # Display the queue with titles and positions
            queue_list = "\n".join([f"{idx + 1}. {item['title']}" for idx, item in enumerate(queues[ctx.guild.id])])
            await ctx.send(f"Current queue:\n{queue_list}")
        else:
            await ctx.send("The queue is empty!")

    @client.command(name="skip")
    async def skip(ctx):
        try:
            # Stop the current song
            voice_clients[ctx.guild.id].stop()

            # Call play_next to start the next song in the queue
            await ctx.send("Song skipped!")
        except Exception as e:
            print(e)
            await ctx.send("An error occurred while trying to skip the song.")

    @client.command(name="commands")
    async def commands_list(ctx):
        command_list = (
            "**.play <link>** - Play a song (either YouTube link or search query)\n"
            "**.queue** - Show the current queue\n"
            "**.clear_queue** - Clear the entire queue\n"
            "**.pause** - Pause the currently playing song\n"
            "**.resume** - Resume the paused song\n"
            "**.stop** - Stop the song and disconnect from the voice channel\n"
            "**.skip** - Skip the current song and play the next one in the queue\n"
            "**.commands** - Show this list of commands"
        )
        
        await ctx.send(f"Here are the available commands:\n{command_list}")


    ################## FEATURE ##########################

    @client.command(name="playlocal")
    async def playlocal(ctx, folder_path: str):
        try:
            # Validate folder path
            if not os.path.isdir(folder_path):
                await ctx.send(f"Invalid folder path: {folder_path}")
                return
            
            # Get all audio files in the folder
            songs = [f for f in os.listdir(folder_path) if f.endswith(('.mp3', '.wav', '.ogg'))]

            if not songs:
                await ctx.send("No audio files found in the directory.")
                return

            # Connect to the voice channel if not already connected
            if ctx.guild.id not in voice_clients or not voice_clients[ctx.guild.id].is_connected():
                voice_client = await ctx.author.voice.channel.connect()
                voice_clients[ctx.guild.id] = voice_client
            else:
                voice_client = voice_clients[ctx.guild.id]

            # Play each song in the folder sequentially
            for song in songs:
                song_path = os.path.join(folder_path, song)
                player = discord.FFmpegOpusAudio(song_path, **ffmpeg_options)
                voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), client.loop))

                # Wait for the song to finish before moving on to the next one
                while voice_client.is_playing():
                    await asyncio.sleep(1)
                    # Maybe add it to a queue and then dont have to worry about starting over
                    # Otherwise it would have to restart. I want .skip to work with this

                await ctx.send(f"Now playing: {song}")

        except Exception as e:
            print(e)
            await ctx.send("An error occurred while trying to play the songs.")

    



    client.run(TOKEN)