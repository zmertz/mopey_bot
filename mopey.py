import discord
from discord.ext import commands, tasks
import os
import asyncio
import yt_dlp
from dotenv import load_dotenv
from time import time
from plexapi.server import PlexServer
import traceback
import playback_controls

plex = None

current_song_data = {}  # Tracks the current song info per guild, including start time and duration
last_activity = {}  # Format: {guild_id: {'last_time': time(), 'channel': channel}}

MAX_QUEUE_SIZE = 50  # Set a maximum size for the queue
INACTIVITY_LIMIT = 600  # 10 minutes in seconds

def format_time(seconds):
        return f"{seconds // 60}:{seconds % 60:02d}"

def create_song_info_text(title, duration, artist=None, album=None):
    song_info_string = title
    if artist:
        song_info_string += f" - {artist}"
    if album:
        song_info_string += f" - {album}"
    song_info_string += f" - (*{format_time(duration)}*)"
    return song_info_string

async def show_now_playing(ctx, curr_song, next_song, client):
    """
    Displays a full embed with detailed information about the currently playing song.
    """

    if not curr_song or "title" not in curr_song:
        await ctx.send("❌ No song is currently playing.")
        return

    title = curr_song.get("title", "Unknown Title")
    duration = curr_song.get("duration", 0)
    artist = curr_song.get("artist", None)
    album = curr_song.get("album", None)
    start_time = curr_song.get("start_time", time())
    thumbnail = curr_song.get("thumbnail", None)  # Optional

    elapsed = int(time() - start_time)
    elapsed = max(0, min(elapsed, duration))  # Clamp

    detail_line = create_song_info_text(title, duration, artist, album)

    # Build next song line
    if next_song:
        next_title = next_song.get("title", "Unknown Title")
        next_duration = next_song.get("duration", 0)
        next_artist = next_song.get("artist", None)
        next_album = next_song.get("album", None)

        next_detail_line = create_song_info_text(next_title, next_duration, next_artist, next_album)
    else:
        next_detail_line = "❌ `Nothing next in queue`"

    
    # Optional time progress bar (just for visual appeal)
    def build_progress_bar(elapsed, total, length=20):
        progress = int((elapsed / total) * length) if total else 0
        bar = "■" * progress + "─ " * (length - progress)
        return f"[{bar}]"

    embed = discord.Embed(
        title="Now Playing",
        #description=f"Currently Playing:\n{detail_line}",
        description=f"{detail_line}",
        color=discord.Color.blurple()
    )

    embed.add_field(
        name="Progress",
        value=f"▶️ {format_time(elapsed)} - {build_progress_bar(elapsed, duration)} - {format_time(duration)}",
        inline=False
    )

    embed.add_field(name="Next", value=next_detail_line, inline=False)

    if thumbnail:
        embed.set_thumbnail(url=thumbnail)

    view = playback_controls.PlaybackControls(ctx, client)
    await ctx.send(embed=embed, view=view)


def get_plex_connection(url, token):
    global plex
    if plex:
        return plex
    try:
        plex = PlexServer(url, token)
        print("✅ Connected to Plex server.")
        return plex
    except Exception as e:
        print(f"❌ Failed to connect to Plex: {e}")
        print(f"PLEX URL: {url}")
        print(f"PLEX_TOKEN: {token}")
        return None


def search_plex_music(query, url, token):
    plex_conn = get_plex_connection(url, token)
    if not plex_conn:
        print("Plex connection is not available.")
        return []

    try:
        results = plex_conn.search(query, mediatype='track')
    except Exception as e:
        print(f"Error during Plex search: {e}")
        return []

    songs = []
    for track in results:
        try:
            if hasattr(track, 'title') and hasattr(track, 'parentTitle') and track.media:
                # Build the stream URL
                media_part = track.media[0].parts[0]
                key = media_part.key
                stream_url = f"{url}{key}?X-Plex-Token={token}"

                songs.append({
                    'title': track.title,
                    'artist': getattr(track, 'grandparentTitle', None),
                    'album': getattr(track, 'parentTitle', None),
                    'duration': track.duration // 1000 if track.duration else 0,
                    'url': stream_url,
                    'thumbnail': track.artUrl
                })
        except Exception as e:
            print(f"Error parsing Plex result: {e}")
    return songs


def format_duration(duration):
    minutes, seconds = divmod(duration, 60)
    return f"{minutes}:{seconds:02}"

def run_bot():
    load_dotenv()
    TOKEN = os.getenv('discord_token')
    PLEX_BASE_URL = os.getenv("plex_base_url")
    PLEX_TOKEN = os.getenv("plex_token")
    intents = discord.Intents.default()
    intents.message_content = True
    client = commands.Bot(command_prefix=".", intents=intents)

    queues = {}
    voice_clients = {}
    yt_dl_options = {
        "format": "bestaudio/best",     # Best available audio-only format
        "noplaylist": True,             # Only download/play a single video, not entire playlists
        "quiet": True,                  # Suppress verbose console output from yt-dlp
        "default_search": "ytsearch",  # If no URL is given, treat input as a search term
        "source_address": "0.0.0.0",    # Sometimes helps avoid geo-restriction issues
    }
    ytdl = yt_dlp.YoutubeDL(yt_dl_options)

    ffmpeg_options = {
        'before_options': (
            '-reconnect 1 '
            '-reconnect_streamed 1 '
            '-reconnect_delay_max 5 '
            '-probesize 5000000 '
            '-analyzeduration 10000000'
        ),
        'options': '-vn -filter:a "volume=0.25"'
    }



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
                data['last_time'] = time()  # Reset the time
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
                        await channel.send("Disconnected due to inactivity.")


    async def play_next(ctx):
        if queues.get(ctx.guild.id):
            next_song = queues[ctx.guild.id].pop(0)
            await play(ctx, link=next_song["link"], title=next_song.get("title"), duration=next_song.get("duration"), artist=next_song.get("artist"), album=next_song.get("album"), thumbnail=next_song.get("thumbnail"))
        else:
            # Queue is empty, clear current song data
            if ctx.guild.id in current_song_data:
                del current_song_data[ctx.guild.id]


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


    @client.command(name="search")
    async def search(ctx, *, query=None):
        try:
            if query is None:
                await ctx.send("Please provide a valid search query.")
                return
            
            await ctx.send("Searching...")

            loop = asyncio.get_event_loop()
            # 'ytsearch3:' tells yt-dlp to return top 3 search results for the query
            data = await loop.run_in_executor(None, lambda: ytdl.extract_info(f"ytsearch3:{query}", download=False))

            # Extract the entries (videos) from search results
            entries = data.get('entries', [])
            if not entries:
                await ctx.send("No results found for your query.")
                return

            # Collect titles, links, durations for top 3
            titles_and_ids = []
            for entry in entries[:3]:
                titles_and_ids.append({
                    "title": entry.get('title', 'Unknown Title'),
                    "link": entry.get('webpage_url'),
                    "duration": entry.get('duration', 0)
                })

            # Create an embed for the search results
            embed = discord.Embed(
                title="Search Results",
                color=discord.Color.dark_gray()
            )
            for i, result in enumerate(titles_and_ids, start=1):
                embed.add_field(
                    name=f"{i}️⃣",
                    value=f"`[{format_duration(result['duration'])}]` {result['title']}",
                    inline=False
                )

            # Send the embed and add reactions
            message = await ctx.send(embed=embed)
            reactions = ["1️⃣", "2️⃣", "3️⃣", "❌"]
            for reaction in reactions:
                await message.add_reaction(reaction)

            def check(reaction, user):
                return (
                    user == ctx.author
                    and str(reaction.emoji) in reactions
                    and reaction.message.id == message.id
                )

            try:
                reaction, _ = await client.wait_for("reaction_add", timeout=30.0, check=check)
                if str(reaction.emoji) == "❌":
                    await ctx.send("Search canceled.")
                    return

                # Determine which song was chosen based on the emoji
                choice_index = reactions.index(str(reaction.emoji))
                chosen_result = titles_and_ids[choice_index]

                # Add the chosen song to the queue
                if ctx.guild.id not in queues:
                    queues[ctx.guild.id] = []

                # Avoid adding the song twice
                if any(song['link'] == chosen_result['link'] for song in queues[ctx.guild.id]):
                    await ctx.send(f"**{chosen_result['title']}** is already in the queue.")
                    return

                # Play immediately if nothing is playing
                if ctx.guild.id not in voice_clients or not voice_clients[ctx.guild.id].is_playing():
                    await play(ctx, link=chosen_result['link'])
                else:
                    queues[ctx.guild.id].append({
                        "title": chosen_result['title'],
                        "link": chosen_result['link'],
                        "duration": chosen_result['duration']
                    })
                    detail_line = create_song_info_text(chosen_result['title'], chosen_result['duration'])
                    await ctx.send(f"Added to queue: **{detail_line}** (Position: {len(queues[ctx.guild.id])})")

            except asyncio.TimeoutError:
                await ctx.send("You took too long to respond! Search canceled.")

        except Exception as e:
            print(e)
            await ctx.send("An error occurred while processing your search.")


    @client.command(name="play")
    async def play(ctx, *, link=None, title=None, duration=None, artist=None, album=None, thumbnail=None):
        try:
            last_activity[ctx.guild.id] = {'last_time': time(), 'channel': ctx.channel}

            if link is None:
                if ctx.guild.id in voice_clients and voice_clients[ctx.guild.id].is_paused():
                    voice_clients[ctx.guild.id].resume()
                    await ctx.send("Resumed the music!")
                    return
                else:
                    await ctx.send("No music is currently paused to resume.")
                    return

            # Connect to voice channel if needed
            if ctx.guild.id not in voice_clients or not voice_clients[ctx.guild.id].is_connected():
                if ctx.author.voice and ctx.author.voice.channel:
                    voice_client = await ctx.author.voice.channel.connect()
                    voice_clients[ctx.guild.id] = voice_client
                else:
                    await ctx.send("You must be connected to a voice channel to play music.")
                    return
            else:
                voice_client = voice_clients[ctx.guild.id]

            # Detect Plex URL
            is_plex_link = link.startswith(PLEX_BASE_URL)
            if is_plex_link:
                song_url = link
                title = title or "Plex Track"
                duration = duration or 0
            else:
                loop = asyncio.get_event_loop()
                data = await loop.run_in_executor(None, lambda: ytdl.extract_info(link, download=False))
                if 'entries' in data:
                    data = data['entries'][0]
                if 'url' not in data:
                    await ctx.send("Could not extract a playable video URL.")
                    return

                song_url = data['url']
                title = data['title']
                duration = data.get('duration', 0)
                thumbnail = data.get('thumbnail')

            # If currently playing, queue the song
            if voice_client.is_playing():
                if ctx.guild.id not in queues:
                    queues[ctx.guild.id] = []
                if len(queues[ctx.guild.id]) >= MAX_QUEUE_SIZE:
                    await ctx.send("The queue is full. Please wait for some songs to finish before adding more.")
                    return
                queues[ctx.guild.id].append({
                    "title": title,
                    "link": link,
                    "duration": duration,
                    "artist": artist,
                    "album": album,
                    "thumbnail": thumbnail
                })
                detail_line = create_song_info_text(title, duration, artist, album)
                await ctx.send(f"Added to queue: **{detail_line}** (Position: {len(queues[ctx.guild.id])})")
            else:
                player = discord.FFmpegOpusAudio(song_url, **ffmpeg_options)
                voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), client.loop))

                current_song_data[ctx.guild.id] = {
                    "title": title,
                    "duration": duration,
                    "artist": artist,
                    "album": album,
                    "start_time": time(),
                    "url": song_url,
                    "thumbnail": thumbnail
                }
          
                next_song = None
                if ctx.guild.id in queues and queues[ctx.guild.id]:
                    next_song = queues[ctx.guild.id][0]

                await show_now_playing(ctx, current_song_data[ctx.guild.id], next_song, client)


        except Exception as e:
            print(e)
            traceback.print_exc()
            await ctx.send("An error occurred while trying to play the song.")


    @client.command(name="playing")
    async def playing(ctx):
        # Check if something is currently playing
        if ctx.guild.id not in current_song_data:
            await ctx.send("No song is currently playing.")
            return

        # Get current song info
        curr_song = current_song_data[ctx.guild.id]

        # Get the next song in the queue (if any)
        next_song = None
        if ctx.guild.id in queues and queues[ctx.guild.id]:
            next_song = queues[ctx.guild.id][0]

        # Show the now playing embed
        await show_now_playing(ctx, curr_song, next_song, client)


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
            await ctx.send(f"Removed: **{removed_song['title']}** ({format_duration(removed_song['duration'])}) from the queue.")
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

            await ctx.send(f"Grabbing **{selected_song['title']}** ({format_duration(selected_song['duration'])}) from the queue.")
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
            embed = discord.Embed(
                title="🎶 Current Queue 🎶",
                #description="Here's what's coming up:",
                color=discord.Color.blurple()
            )

            queue_items = queues[ctx.guild.id]
            for idx, item in enumerate(queue_items[:5]):
                detail_line = create_song_info_text(item['title'], item['duration'], item.get('artist'), item.get('album'))
                embed.add_field(name="\u200b", value=f"**{idx + 1}.** {detail_line}", inline=False)

            remaining = len(queue_items) - 5
            if remaining > 0:
                embed.add_field(
                    name="\u200b",
                    value=f"*...and {remaining} more song{'s' if remaining != 1 else ''} in the queue.*",
                    inline=False
                )

            await ctx.send(embed=embed)
        else:
            await ctx.send("The queue is empty!")


    @client.command(name="skip")
    async def skip(ctx):
        try:
            # Check if the bot is connected to a voice channel
            if ctx.guild.id not in voice_clients or not voice_clients[ctx.guild.id].is_connected():
                await ctx.send("I'm not connected to a voice channel.")
                return
            
            # Check if a song is currently playing
            if not voice_clients[ctx.guild.id].is_playing():
                await ctx.send("There's nothing to skip, no song is currently playing.")
                return
            
            # Check if the queue is empty
            if not queues.get(ctx.guild.id) or len(queues[ctx.guild.id]) == 0:
                voice_clients[ctx.guild.id].stop()  # Stop the current song
                await ctx.send("Song skipped. No more songs in the queue.")
            else:
                # Stop the current song and play the next one in the queue
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
            voice_client.pause()  # Pause the current playback

            ffmpeg_options_with_seek = {
                'before_options': f'-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -ss {new_position}',
                'options': '-vn -filter:a "volume=0.25"'
            }
            player = discord.FFmpegOpusAudio(song_data["url"], **ffmpeg_options_with_seek)

            # Start playback from the new position
            voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx), client.loop))
            current_song_data[ctx.guild.id]["start_time"] = time() - new_position  # Update start time
            if int(new_position) == 0:
                await ctx.send(f"Restarting song")
            else:
                await ctx.send(f"Seeked to {format_duration(int(new_position))}.")

        except Exception as e:
            print(e)
            await ctx.send("An error occurred while trying to seek.")


    @client.command(name="plex")
    async def plex(ctx, *, query=None):
        try:
            if query is None:
                await ctx.send("Please provide a song name to search on Plex.")
                return

            # Make sure we're connected to Plex
            plex_conn = get_plex_connection(PLEX_BASE_URL, PLEX_TOKEN)
            if not plex_conn:
                await ctx.send("Plex is not available or not configured correctly.")
                return

            await ctx.send(f"Searching Plex for '{query}'...")
            songs = search_plex_music(query, PLEX_BASE_URL, PLEX_TOKEN)
            if not songs:
                await ctx.send("No songs found on Plex.")
                return

            # Use the top result
            selected = songs[0]

            # ✅ Call your existing play command
            await play(
                ctx,
                link=selected['url'],
                title=selected.get('title'),
                duration=selected.get('duration'),
                artist=selected.get('artist'),
                album=selected.get('album'),
                thumbnail=selected.get('thumbnail')
            )

        except Exception as e:
            print(e)
            await ctx.send("An error occurred while trying to play the song from Plex.")


    @client.command(name="plexsearch")
    async def plexsearch(ctx, *, query=None):
        try:
            if query is None:
                await ctx.send("Please provide a search query for Plex.")
                return

            # Make sure we're connected to Plex
            if not get_plex_connection(PLEX_BASE_URL, PLEX_TOKEN):
                await ctx.send("Plex is not available or not configured correctly.")
                return

            await ctx.send("Searching Plex...")
            songs = search_plex_music(query, PLEX_BASE_URL, PLEX_TOKEN)
            if not songs:
                await ctx.send("No songs found on Plex.")
                return

            # Show the top 3 results
            embed = discord.Embed(title="Plex Search Results", color=discord.Color.dark_gold())
            top_results = songs[:3]
            for i, song in enumerate(top_results, start=1):
                embed.add_field(
                    name=f"{i}️⃣",
                    value=f"`[{format_duration(song['duration'])}]` {song['title']} - {song['artist']} (Album: {song['album']})",
                    inline=False
                )

            reactions = ["1️⃣", "2️⃣", "3️⃣"][:len(top_results)]
            reactions.append("❌")

            message = await ctx.send(embed=embed)
            for r in reactions:
                await message.add_reaction(r)

            def check(reaction, user):
                return (
                    user == ctx.author and
                    str(reaction.emoji) in reactions and
                    reaction.message.id == message.id
                )

            try:
                reaction, _ = await client.wait_for("reaction_add", timeout=30.0, check=check)
                if str(reaction.emoji) == "❌":
                    await ctx.send("Search canceled.")
                    return

                index = reactions.index(str(reaction.emoji))
                selected = top_results[index]

                # Call play() and let it handle connection and queuing
                await play(
                    ctx,
                    link=selected['url'],
                    title=selected['title'],
                    duration=selected['duration'],
                    artist=selected.get('artist'),
                    album=selected.get('album'),
                    thumbnail=selected.get('thumbnail')  # if available
                )

            except asyncio.TimeoutError:
                await ctx.send("You took too long to respond! Search canceled.")

        except Exception as e:
            print(e)
            await ctx.send("An error occurred while processing your Plex search.")

    
    @client.event
    async def on_reaction_add(reaction, user):
        if user.bot:
            return

        message = reaction.message
        ctx = await client.get_context(message)

        # Make sure the reaction is on a bot message with an embed
        if not message.embeds or not ctx.guild:
            return

        emoji = str(reaction.emoji)

        # Only allow control if the user is in a voice channel
        if not ctx.author.voice or ctx.voice_client is None:
            return

        try:
            if emoji == "⏯️":  # Play/Pause toggle
                vc = voice_clients.get(ctx.guild.id)
                if vc:
                    if vc.is_playing():
                        await ctx.invoke(client.get_command("pause"))
                    else:
                        await ctx.invoke(client.get_command("resume"))

            elif emoji == "⏭️":  # Skip
                await ctx.invoke(client.get_command("skip"))

            elif emoji == "⏮️":  # Restart
                await ctx.invoke(client.get_command("seek"), seconds=-100000)

        except Exception as e:
            print(e)



    # @client.command(name="commands")
    # async def commands_list(ctx):
    #     command_list = (
    #         "**.join** - Mopey bot joins the user's current voice channel\n"
    #         "**.play <link>** - Play a song (either YouTube link or search query, or resume if paused)\n"
    #         "**.queue** - Show the current queue\n"
    #         "**.clear** - Clear the entire queue\n"
    #         "**.remove <position>** - Remove a specific song from the queue by its position\n"
    #         "**.playqueue <position>** - Play a specific song from the queue\n"
    #         "**.pause** - Pause the currently playing song\n"
    #         "**.resume** - Resume the paused song\n"
    #         "**.stop** - Stop the song and disconnect from the voice channel\n"
    #         "**.skip** - Skip the current song and play the next one in the queue\n"
    #         "**.seek <seconds>** - Fast forward or rewind the current song by the specified number of seconds\n"
    #         "**.search <query>** - Search YouTube for a query and choose a result to add to the queue\n"
    #         "**.plex <query>** - Plays the first result returned by <query> from the plex server\n"
    #         "**.plexsearch <query>** - Search connected Plex server for a query and choose a result to add to the queue\n"
    #         "**.playing** - Show current song info"
    #          "**.commands** - Show this list of commands"
    #     )

    #     await ctx.send(f"Here are the available commands:\n{command_list}")

    @client.command(name="commands")
    async def commands_list(ctx):
        embed = discord.Embed(title="Available Commands", color=discord.Color.blurple())

        command_list = [
            ("**.clear**", "Clear the entire queue"),
            ("**.commands**", "Show this list of commands"),
            ("**.join**", "Mopey bot joins the user's current voice channel"),
            ("**.pause**", "Pause the currently playing song"),
            ("**.play <link>**", "Play a song (YouTube link, search query, or resume if paused)"),
            ("**.playqueue <position>**", "Play a specific song from the queue"),
            ("**.playing**", "Show current song info"),
            ("**.plex <query>**", "Plays the first result returned by <query> from the Plex server"),
            ("**.plexsearch <query>**", "Search connected Plex server for a query and choose a result to add to the queue"),
            ("**.queue**", "Show the current queue"),
            ("**.remove <position>**", "Remove a specific song from the queue by its position"),
            ("**.resume**", "Resume the paused song"),
            ("**.search <query>**", "Search YouTube for a query and choose a result to add to the queue"),
            ("**.seek <seconds>**", "Fast forward or rewind the current song by the specified number of seconds"),
            ("**.skip**", "Skip the current song and play the next one in the queue"),
            ("**.stop**", "Stop the song and disconnect from the voice channel")
        ]

        for name, description in command_list:
            embed.add_field(name=name, value=description, inline=False)

        await ctx.send(embed=embed)


    client.run(TOKEN)
