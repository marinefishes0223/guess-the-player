"""
Guess the Cricketer - Discord bot
==================================

Access control:
  EVERY command below (prefix and slash) can only be run by a server
  moderator (Administrator or Manage Server permission) or the bot owner
  (user id 1224350217336524801). Anyone else gets a polite refusal.

PREFIX Commands (unchanged mechanics):
  !plshelp                           shows the help embed (same as /help) — works
                                      even if slash commands haven't synced yet
  !newguess "Player Full Name"      (attach the player photo to the same message;
                                      optionally attach a 2nd image to use as a
                                      custom stadium background)
  !hint                              gives a letter-count hint for the active round
  !reveal                            ends the round early and reveals the answer
  !endguess                          alias for !reveal

SLASH Commands:
  /help
      Shows an embed explaining what the bot does and listing every command.
      Available to everyone (not mod-restricted) so regular players can also
      look it up.

  /picture_send picture:<attachment>
      Sends whatever image you attach straight back to the channel. Intended
      use: you already have a generated silhouette/quiz-card image and just
      want the bot to post it into chat.

  /start_quiz player_name:<str> guesses_per_user:<int = 5>
      Starts a round in the current channel. You choose how many guesses
      each participant gets. Every guess message gets ❌ if it's wrong, ✅
      only on the correct answer. A user is warned the moment they use up
      their last guess.

  /hint
      Only works while a /start_quiz round is active. Asks the invoking
      mod/owner to type the hint they want to share (e.g. "player is from
      Pakistan") in the channel within 60 seconds, then posts that hint to
      participants and resets everyone's guess count back to full.

  /timer wait_seconds:<int>
      Puts a timer on the active round in this channel (either a !newguess
      round or a /start_quiz round). After wait_seconds, posts a countdown
      (6, 5, 4, 3, 2, 1 — editing the same message each second), then ends
      the round and reveals the correct answer. Meant for calling a round
      near its end: "wrap this up in 30 seconds."

Gameplay (prefix round, !newguess):
  Once a round is posted in a channel, any message that matches the player's
  full name or surname (case-insensitive) is treated as a correct guess.
  First correct guess wins; bot announces the winner and closes the round.

Run:
  1. pip install -r requirements.txt
  2. cp .env.example .env   and fill in DISCORD_BOT_TOKEN + 3 REMOVEBG API keys
  3. drop a stadium photo at assets/default_background.png (see README)
  4. python bot.py
"""

import asyncio
import io
import os
import re
import tempfile
from itertools import cycle

import discord
from discord import app_commands
from discord.ext import commands

import config
from silhouette import compose_quiz_card, RemoveBgError

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix=config.COMMAND_PREFIX, intents=intents)

BOT_OWNER_ID = 1224350217336524801

# channel_id -> round state (for the original !newguess flow)
active_rounds: dict[int, dict] = {}

# channel_id -> round state (for the /start_quiz flow)
# {
#   "answer": str,
#   "started_by": int,
#   "max_guesses": int,
#   "guesses": {user_id: guesses_used},
# }
slash_quiz_rounds: dict[int, dict] = {}

DEFAULT_GUESSES_PER_USER = 5

# Rotate through the configured remove.bg API keys
removebg_keys = cycle(config.REMOVEBG_API_KEYS)


# ---------------------------------------------------------------------------
# Access control helpers
# ---------------------------------------------------------------------------

def _is_mod_or_owner(user: discord.abc.User, guild: discord.Guild | None) -> bool:
    if user.id == BOT_OWNER_ID:
        return True
    if guild is None:
        return False
    member = guild.get_member(user.id)
    if member is None:
        return False
    perms = member.guild_permissions
    return perms.administrator or perms.manage_guild


def mod_or_owner_check():
    async def predicate(ctx: commands.Context) -> bool:
        return _is_mod_or_owner(ctx.author, ctx.guild)
    return commands.check(predicate)


def app_mod_or_owner_check():
    def predicate(interaction: discord.Interaction) -> bool:
        return _is_mod_or_owner(interaction.user, interaction.guild)
    return app_commands.check(predicate)


NOT_AUTHORIZED_MSG = "You need to be a server mod or the bot owner to use this command."


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.CheckFailure):
        await ctx.reply(NOT_AUTHORIZED_MSG)
    else:
        raise error


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        if interaction.response.is_done():
            await interaction.followup.send(NOT_AUTHORIZED_MSG, ephemeral=True)
        else:
            await interaction.response.send_message(NOT_AUTHORIZED_MSG, ephemeral=True)
    else:
        raise error


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def normalize(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def is_correct_guess(guess: str, answer: str) -> bool:
    g = normalize(guess)
    a = normalize(answer)
    if not g:
        return False
    if g == a:
        return True
    # allow matching just the surname (last word of the answer)
    surname = a.split()[-1] if a.split() else a
    if g == surname:
        return True
    return False


def build_letter_count_hint(answer: str) -> str:
    letters = len(normalize(answer).replace(" ", ""))
    words = len(answer.split())
    return f"Hint: the name has **{words}** word(s) and **{letters}** letters total."


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (id={bot.user.id})")
    try:
        dev_guild_id = getattr(config, "DEV_GUILD_ID", None)
        if dev_guild_id:
            # Guild-scoped commands show up instantly — use this while developing.
            guild_obj = discord.Object(id=dev_guild_id)
            bot.tree.copy_global_to(guild=guild_obj)
            synced = await bot.tree.sync(guild=guild_obj)
            print(f"Synced {len(synced)} slash command(s) to dev guild {dev_guild_id} (instant).")
        else:
            synced = await bot.tree.sync()
            print(f"Synced {len(synced)} global slash command(s) (can take up to ~1 hour to appear).")
    except Exception as e:
        print(f"Slash command sync failed: {e}")


# ---------------------------------------------------------------------------
# Existing prefix commands (mechanics unchanged, now mod/owner-only)
# ---------------------------------------------------------------------------

@bot.command(name="plshelp")
async def plshelp(ctx: commands.Context):
    """Prefix version of /help, in case slash commands haven't synced yet."""
    embed = discord.Embed(
        title="🏏 Guess the Cricketer — Help",
        description=(
            "The bot posts a cricketer's silhouette photo and everyone guesses who it is "
            "right in the chat. Mods/owner run the commands below; everyone else just types guesses."
        ),
        color=discord.Color.green(),
    )
    embed.add_field(
        name="🎮 Old-style round (first correct guess wins)",
        value=(
            "`!newguess \"Player Name\"` — start a round (attach player photo, optional background)\n"
            "`!hint` — letter-count hint for the active round\n"
            "`!reveal` / `!endguess` — end the round early and reveal the answer"
        ),
        inline=False,
    )
    embed.add_field(
        name="🎯 New-style round (set number of guesses each)",
        value=(
            "`/start_quiz` — start a round, choose guesses per participant (default 5)\n"
            "`/picture_send` — post an attached image to the channel\n"
            "`/hint` — mod types a custom hint, refreshes everyone's guesses\n"
            "`/timer` — countdown then auto-end the round and reveal the answer"
        ),
        inline=False,
    )
    embed.add_field(
        name="🔒 Who can use these?",
        value="Server mods (Administrator / Manage Server) and the bot owner only.",
        inline=False,
    )
    embed.set_footer(text="Just type your guess as a normal chat message — no command needed to play!")

    await ctx.reply(embed=embed)


@bot.command(name="newguess")
@mod_or_owner_check()
async def newguess(ctx: commands.Context, *, player_name: str = None):
    """Start a new round. Usage: !newguess Virat Kohli  (with photo attached)"""
    if not player_name:
        await ctx.reply("Usage: `!newguess <Player Name>` and attach the player's photo.")
        return

    if ctx.channel.id in active_rounds:
        await ctx.reply("A round is already active in this channel. Use `!reveal` to end it first.")
        return

    if not ctx.message.attachments:
        await ctx.reply("Please attach the player's photo to your message.")
        return

    player_attachment = ctx.message.attachments[0]
    background_attachment = (
        ctx.message.attachments[1] if len(ctx.message.attachments) > 1 else None
    )

    status_msg = await ctx.reply("Processing image (removing background)...")

    try:
        player_bytes = await player_attachment.read()

        if background_attachment:
            bg_bytes = await background_attachment.read()
            tmp_bg = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            tmp_bg.write(bg_bytes)
            tmp_bg.close()
            bg_path = tmp_bg.name
        else:
            # No custom background attached, and no file at DEFAULT_BACKGROUND
            # (or it doesn't exist yet) -> compose_quiz_card will generate a
            # stadium-style background automatically, no file needed.
            bg_path = config.DEFAULT_BACKGROUND

        out_path = os.path.join(tempfile.gettempdir(), f"quiz_{ctx.message.id}.png")

        last_error = None

        for _ in range(len(config.REMOVEBG_API_KEYS)):
            api_key = next(removebg_keys)

            try:
                compose_quiz_card(
                    background_path=bg_path,
                    cutout_bytes=player_bytes,
                    api_key=api_key,
                    output_path=out_path,
                )
                last_error = None
                break

            except RemoveBgError as e:
                last_error = e

        if last_error:
            raise last_error

    except RemoveBgError as e:
        await status_msg.edit(content=f"remove.bg failed: {e}")
        return
    except Exception as e:
        await status_msg.edit(content=f"Something went wrong building the card: {e}")
        return

    active_rounds[ctx.channel.id] = {
        "answer": player_name,
        "started_by": ctx.author.id,
        "winner": None,
    }

    await status_msg.delete()
    await ctx.send(
        content="🏏 **GUESS THE CRICKETER!** Type your guess in the chat. First correct answer wins!",
        file=discord.File(out_path, filename="guess_the_cricketer.png"),
    )


@bot.command(name="hint")
@mod_or_owner_check()
async def hint(ctx: commands.Context):
    round_state = active_rounds.get(ctx.channel.id)
    if not round_state:
        await ctx.reply("No active round in this channel.")
        return
    await ctx.reply(build_letter_count_hint(round_state["answer"]))


@bot.command(name="reveal", aliases=["endguess"])
@mod_or_owner_check()
async def reveal(ctx: commands.Context):
    round_state = active_rounds.pop(ctx.channel.id, None)
    if not round_state:
        await ctx.reply("No active round in this channel.")
        return
    await ctx.send(f"The answer was **{round_state['answer']}**! Round ended.")


# ---------------------------------------------------------------------------
# Slash commands (all mod/owner-only)
# ---------------------------------------------------------------------------

@bot.tree.command(name="picture_send", description="Post an image you attach straight into this channel.")
@app_commands.describe(picture="The (already generated) silhouette/quiz image to post")
@app_mod_or_owner_check()
async def picture_send(interaction: discord.Interaction, picture: discord.Attachment):
    await interaction.response.defer(thinking=True)
    try:
        image_bytes = await picture.read()
        discord_file = discord.File(io.BytesIO(image_bytes), filename=picture.filename)
        await interaction.channel.send(file=discord_file)
        await interaction.followup.send("Image posted!", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"Couldn't post that image: {e}", ephemeral=True)


@bot.tree.command(name="start_quiz", description="Start a round with a set number of guesses per participant.")
@app_commands.describe(
    player_name="The answer for this round (kept secret from players)",
    guesses_per_user="How many guesses each participant gets (default 5)",
)
@app_mod_or_owner_check()
async def start_quiz(
    interaction: discord.Interaction,
    player_name: str,
    guesses_per_user: app_commands.Range[int, 1, 20] = DEFAULT_GUESSES_PER_USER,
):
    channel_id = interaction.channel_id

    if channel_id in slash_quiz_rounds:
        await interaction.response.send_message(
            "A `/start_quiz` round is already active in this channel.", ephemeral=True
        )
        return

    slash_quiz_rounds[channel_id] = {
        "answer": player_name,
        "started_by": interaction.user.id,
        "max_guesses": guesses_per_user,
        "guesses": {},
    }

    await interaction.response.send_message(
        f"🏏 **Quiz started!** Everyone gets **{guesses_per_user} guesses**. "
        "Type your guesses in the chat — ❌ means it didn't count as correct, ✅ means you got it!"
    )


@bot.tree.command(
    name="timer",
    description="After a set delay, count down 6-to-1 and end the active round, revealing the answer.",
)
@app_commands.describe(wait_seconds="How long to wait (in seconds) before the countdown/end-of-round starts")
@app_mod_or_owner_check()
async def timer(interaction: discord.Interaction, wait_seconds: app_commands.Range[int, 0, 3600]):
    channel_id = interaction.channel_id

    if channel_id not in slash_quiz_rounds and channel_id not in active_rounds:
        await interaction.response.send_message(
            "No active round in this channel to put a timer on.", ephemeral=True
        )
        return

    await interaction.response.send_message(
        f"⏳ Round will end in {wait_seconds} second(s)."
    )

    if wait_seconds > 0:
        await asyncio.sleep(wait_seconds)

    # The round may have already been won/ended while we were waiting.
    if channel_id not in slash_quiz_rounds and channel_id not in active_rounds:
        await interaction.channel.send("That round already ended before the timer went off.")
        return

    countdown_msg = await interaction.channel.send("6")
    for n in (5, 4, 3, 2, 1):
        await asyncio.sleep(1)
        await countdown_msg.edit(content=str(n))
    await asyncio.sleep(1)

    # Pull the answer from whichever round is (still) active and end it.
    quiz_state = slash_quiz_rounds.pop(channel_id, None)
    round_state = active_rounds.pop(channel_id, None)
    answer = quiz_state["answer"] if quiz_state else (round_state["answer"] if round_state else None)

    if answer:
        await interaction.channel.send(f"⏰ **Time's up!** The answer was **{answer}**. Round ended.")
    else:
        await interaction.channel.send("That round already ended before the timer went off.")


@bot.tree.command(name="help", description="Show what this bot does and list all its commands.")
async def help_slash(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🏏 Guess the Cricketer — Help",
        description=(
            "The bot posts a cricketer's silhouette photo and everyone guesses who it is "
            "right in the chat. Mods/owner run the commands below; everyone else just types guesses."
        ),
        color=discord.Color.green(),
    )
    embed.add_field(
        name="🎮 Old-style round (first correct guess wins)",
        value=(
            "`!newguess \"Player Name\"` — start a round (attach player photo, optional background)\n"
            "`!hint` — letter-count hint for the active round\n"
            "`!reveal` / `!endguess` — end the round early and reveal the answer"
        ),
        inline=False,
    )
    embed.add_field(
        name="🎯 New-style round (set number of guesses each)",
        value=(
            "`/start_quiz` — start a round, choose guesses per participant (default 5)\n"
            "`/picture_send` — post an attached image to the channel\n"
            "`/hint` — mod types a custom hint, refreshes everyone's guesses\n"
            "`/timer` — countdown then auto-end the round and reveal the answer"
        ),
        inline=False,
    )
    embed.add_field(
        name="🔒 Who can use these?",
        value="Server mods (Administrator / Manage Server) and the bot owner only.",
        inline=False,
    )
    embed.set_footer(text="Just type your guess as a normal chat message — no command needed to play!")

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="hint", description="Ask a mod/owner for a hint and refresh everyone's guesses.")
@app_mod_or_owner_check()
async def hint_slash(interaction: discord.Interaction):
    round_state = slash_quiz_rounds.get(interaction.channel_id)
    if not round_state:
        await interaction.response.send_message(
            "No active `/start_quiz` round in this channel.", ephemeral=True
        )
        return

    await interaction.response.send_message(
        "What hint would you like to give participants? Type it in this channel within 60 seconds "
        "(e.g. \"player is from Pakistan\"). Only your message counts, and it won't be scored as a guess.",
        ephemeral=True,
    )

    def check(m: discord.Message) -> bool:
        return m.author.id == interaction.user.id and m.channel.id == interaction.channel_id

    try:
        hint_msg = await bot.wait_for("message", check=check, timeout=60)
    except asyncio.TimeoutError:
        await interaction.followup.send(
            "Timed out waiting for the hint text. Run `/hint` again when you're ready.", ephemeral=True
        )
        return

    # Round may have ended (someone guessed correctly) while we were waiting.
    round_state = slash_quiz_rounds.get(interaction.channel_id)
    if not round_state:
        await interaction.followup.send(
            "That round already ended before the hint could be sent.", ephemeral=True
        )
        return

    round_state["guesses"] = {}  # refresh everyone's guess count

    await interaction.channel.send(
        f"💡 **Hint:** {hint_msg.content}\n"
        f"Everyone's guesses have been reset to **{round_state['max_guesses']}**!"
    )


# ---------------------------------------------------------------------------
# Message handling: prefix round + slash-quiz round
# ---------------------------------------------------------------------------

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    await bot.process_commands(message)

    if message.content.startswith(config.COMMAND_PREFIX):
        return

    # --- Original !newguess round: first correct guess wins (unchanged) ---
    round_state = active_rounds.get(message.channel.id)
    if round_state and not round_state.get("winner"):
        if is_correct_guess(message.content, round_state["answer"]):
            round_state["winner"] = message.author.id
            active_rounds.pop(message.channel.id, None)
            await message.reply(
                f"✅ Correct! It was **{round_state['answer']}**. 🎉 Well played, {message.author.mention}!"
            )
            return

    # --- /start_quiz round: N guesses per user, ❌/✅ reactions ---
    quiz_state = slash_quiz_rounds.get(message.channel.id)
    if quiz_state:
        # Don't treat a mod/owner's messages (e.g. typing a hint) as guesses.
        if _is_mod_or_owner(message.author, message.guild):
            return

        user_id = message.author.id
        guesses_used = quiz_state["guesses"].get(user_id, 0)

        if guesses_used >= quiz_state["max_guesses"]:
            # Out of guesses; ignore silently so chat doesn't get spammy.
            return

        if is_correct_guess(message.content, quiz_state["answer"]):
            await message.add_reaction("✅")
            slash_quiz_rounds.pop(message.channel.id, None)
            await message.reply(
                f"🎉 Correct! It was **{quiz_state['answer']}**. Well played, {message.author.mention}! Round ended."
            )
            return

        # Wrong guess: count it and mark it with a cross.
        quiz_state["guesses"][user_id] = guesses_used + 1
        await message.add_reaction("❌")

        remaining = quiz_state["max_guesses"] - quiz_state["guesses"][user_id]
        if remaining == 0:
            await message.reply(
                f"{message.author.mention} that was your last guess! Wait for a mod to run `/hint` for more."
            )


if __name__ == "__main__":
    bot.run(config.DISCORD_BOT_TOKEN)