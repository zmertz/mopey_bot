import discord
from discord.ext import commands
from discord.ext import tasks
import os
import asyncio
import yt_dlp
from dotenv import load_dotenv
import urllib.parse, urllib.request, re
from time import time

# Initialize global variables
current_song_data = {}  # Tracks the current song info per guild, including start time and duration
last_activity = {}  # Format: {guild_id: {'last_time': time(), 'channel': channel}}

MAX_QUEUE_SIZE = 50  # Set a maximum size for the queue
INACTIVITY_LIMIT = 600  # 10 minutes in seconds

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
        print(f'{client.user} is fully operational')
        check_inactivity.start()  # Start the inactivity check loop

    @tasks.loop(seconds=120)  # Runs every 5 minutes
    async def check_inactivity():
        current_time = time()  # Get the current time in seconds
        for guild_id, data in last_activity.items():
            voice_client = voice_clients.get(guild_id)
            
            # If the bot is playing music, don't disconnect
            if voice_client and voice_client.is_playing():
                continue  # Skip inactivity check if the bot is playing music
            
            # If the bot is not playing music and has been inactive for more than 10 minutes
            if current_time - data['last_time'] > INACTIVITY_LIMIT:
                if guild_id in voice_clients and voice_clients[guild_id].is_connected():
                    # Stop the music and disconnect
                    voice_clients[guild_id].stop()
                    await voice_clients[guild_id].disconnect()
                    del voice_clients[guild_id]  # Remove the voice client reference
                    
                    # Send the disconnect message to the stored channel
                    if 'channel' in data:
                        channel = data['channel']
                        await channel.send("Music stopped and disconnected due to inactivity.")


    async def play_next(ctx):
        if queues[ctx.guild.id] != []:
            next_song = queues[ctx.guild.id].pop(0)
            link = next_song["link"]
            await play(ctx, link=link)

    @client.command(name="join")
    async def join(ctx):
        try:
            # Check if the bot is already connected to a voice channel
            if ctx.guild.id in voice_clients and voice_clients[ctx.guild.id].is_connected():
                await ctx.send("I'm already in a channel!")
                return  # Exit the function if the bot is already in a channel

            # Check if the user is in a voice channel
            if ctx.author.voice:
                voice_channel = ctx.author.voice.channel
                voice_client = await voice_channel.connect()
                voice_clients[ctx.guild.id] = voice_client

                # Stop any potential playback to prevent the 'speaking' status
                voice_client.stop()
                await ctx.send(f"Connected to the voice channel!")
            else:
                await ctx.send("You need to be in a voice channel to use this command.")
        except Exception as e:
            print(e)
            await ctx.send("An error occurred while trying to join the voice channel.")


    @client.command(name="play")
    async def play(ctx, *, link=None):
        try:
            # Update last activity time when music is played and what text channel request was sent in
            last_activity[ctx.guild.id] = {'last_time': time(), 'channel': ctx.channel}

            # Resume if no link is provided and music is paused
            if link is None:
                if ctx.guild.id in voice_clients and voice_clients[ctx.guild.id].is_paused():
                    voice_clients[ctx.guild.id].resume()
                    await ctx.send("Resumed the music!")
                    return
                else:
                    await ctx.send("No music is currently paused to resume.")
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

            # Extract song data
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, lambda: ytdl.extract_info(link, download=False))
            song = data['url']
            title = data['title']
            duration = data.get('duration', 0)  # Duration in seconds

            # Add to queue if a song is already playing
            if voice_client.is_playing():
                if ctx.guild.id not in queues:
                    queues[ctx.guild.id] = []
                if len(queues[ctx.guild.id]) >= MAX_QUEUE_SIZE:
                    await ctx.send("The queue is full. Please wait for some songs to finish before adding more.")
                    return
                queues[ctx.guild.id].append({"title": title, "link": link})
                await ctx.send(f"Added to queue: **{title}** (Position: {len(queues[ctx.guild.id])})")
            else:
                # Play the song if nothing is playing
                player = discord.FFmpegOpusAudio(song, **ffmpeg_options)
                voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), client.loop))

                # Update current song data
                current_song_data[ctx.guild.id] = {
                    "url": song,
                    "start_time": time(),
                    "duration": duration
                }

                await ctx.send(f"Now playing: **{title}**")

        except Exception as e:
            print(e)
            await ctx.send("An error occurred while trying to play the song.")

    @client.command(name="clear")
    async def clear(ctx):
        if ctx.guild.id in queues:
            queues[ctx.guild.id].clear()
            await ctx.send("Queue cleared!")
        else:
            await ctx.send("There is no queue to clear")

    @client.command(name="remove")
    async def remove(ctx, position: int = 0):
        if ctx.guild.id in queues and 0 < position <= len(queues[ctx.guild.id]):
            removed_song = queues[ctx.guild.id].pop(position - 1)
            await ctx.send(f"Removed: **{removed_song['title']}** from the queue.")
        else:
            await ctx.send("Invalid position. Please provide a valid number within the queue.")

    @client.command(name="playqueue")
    async def playqueue(ctx, position: int = 0):
        if ctx.guild.id in queues and 0 < position <= len(queues[ctx.guild.id]):
            # Move the selected song to the front of the queue
            selected_song = queues[ctx.guild.id].pop(position - 1)
            queues[ctx.guild.id].insert(0, selected_song)

            # Stop the current song to trigger the after callback
            voice_clients[ctx.guild.id].stop()

            await ctx.send(f"Grabbing **{selected_song['title']}** from the queue.")
        else:
            await ctx.send("Invalid position. Please provide a valid number within the queue.")

    @client.command(name="pause")
    async def pause(ctx):
        try:
            voice_clients[ctx.guild.id].pause()
            await ctx.send("Music paused!")
        except Exception as e:
            print(e)

    @client.command(name="resume")
    async def resume(ctx):
        try:
            voice_clients[ctx.guild.id].resume()
            await ctx.send("Music resumed!")
        except Exception as e:
            print(e)

    @client.command(name="stop")
    async def stop(ctx):
        try:
            voice_clients[ctx.guild.id].stop()
            await voice_clients[ctx.guild.id].disconnect()
            del voice_clients[ctx.guild.id]
            await ctx.send("Music stopped and disconnected.")
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
            voice_clients[ctx.guild.id].stop()
            await ctx.send("Song skipped, now playing next in the queue.")
        except Exception as e:
            print(e)
            await ctx.send("An error occurred while trying to skip the song.")

    @client.command(name="seek")
    async def seek(ctx, seconds: int):
        try:
            if ctx.guild.id not in voice_clients or not voice_clients[ctx.guild.id].is_playing():
                await ctx.send("No song is currently playing.")
                return

            if ctx.guild.id not in current_song_data:
                await ctx.send("Cannot fast forward or rewind right now.")
                return

            # Get current song data
            song_data = current_song_data[ctx.guild.id]
            elapsed = time() - song_data["start_time"]
            new_position = max(0, elapsed + seconds)  # Ensure it doesn't go below 0

            if new_position >= song_data["duration"]:
                await ctx.send("Cannot seek beyond the length of the song.")
                return

            # Restart the song from the new position
            voice_client = voice_clients[ctx.guild.id]
            voice_client.stop()  # Stop the current playback

            ffmpeg_options_with_seek = {
                'before_options': f'-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -ss {new_position}',
                'options': '-vn -filter:a "volume=0.25"'
            }
            player = discord.FFmpegOpusAudio(song_data["url"], **ffmpeg_options_with_seek)

            # Start playback from the new position
            voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), client.loop))
            current_song_data[ctx.guild.id]["start_time"] = time() - new_position  # Update start time
            await ctx.send(f"Seeked to {int(new_position)} seconds.")

        except Exception as e:
            print(e)
            await ctx.send("An error occurred while trying to seek.")

    @client.command(name="commands")
    async def commands_list(ctx):
        command_list = (
            "**.join** - Mopey bot joins the user's current voice channel\n"
            "**.play <link>** - Play a song (either YouTube link or search query, or resume if paused)\n"
            "**.queue** - Show the current queue\n"
            "**.clear** - Clear the entire queue\n"
            "**.remove <position>** - Remove a specific song from the queue by its position\n"
            "**.playqueue <position>** - Play a specific song from the queue\n"
            "**.pause** - Pause the currently playing song\n"
            "**.resume** - Resume the paused song\n"
            "**.stop** - Stop the song and disconnect from the voice channel\n"
            "**.skip** - Skip the current song and play the next one in the queue\n"
            "**.seek <seconds>** - Fast forward or rewind the current song by the specified number of seconds\n"
            "**.commands** - Show this list of commands"
        )

        await ctx.send(f"Here are the available commands:\n{command_list}")

    client.run(TOKEN)
