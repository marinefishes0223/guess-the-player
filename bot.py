"""
Guess the Cricketer - Discord bot
==================================

Access control:
  EVERY command below (prefix and slash) can only be run by a server
  moderator (Administrator or Manage Server permission), a user whose ID is
  in AUTHORIZED_USER_IDS, or a user who was given access with /add_perms.
  Exceptions: /help, !plshelp and !pls help work for everyone.

PREFIX Commands:
  !plshelp  (also: !pls help)   help embed (works even if slash commands aren't synced)
  !sync                         force-sync slash commands to this server
  !free_admin                   same as /free_admin
  !newguess "Player Name"       attach the player photo (optional 2nd image = background);
                                the bot deletes your command message to hide the answer
  !bulk                         attach up to 10 photos; one silhouette card per photo
  !hint                         letter-count hint for the active round (either round type)
  !reveal / !endguess           end the round early and reveal the answer
  !blacklist @user|ID           stop that user guessing (current + future rounds)
  !removeblacklist @user|ID     undo it (alias: !remove_black)

SLASH Commands:
  /help, /add_perms, /blacklist, /removeblacklist, /picture_send,
  /start_quiz, /free_admin, /hint, /timer

Persistence:
  bot_data.json stores extra permissions, the per-server blacklist AND the
  currently running rounds, so a restart/crash no longer wipes an active quiz.
  (/timer countdowns are not restored after a restart - run /timer again.)

Run:
  1. pip install -r requirements.txt
  2. cp .env.example .env   and fill in DISCORD_BOT_TOKEN + REMOVEBG API keys
  3. python bot.py
"""

import asyncio
import io
import json
import os
import random
import re
import socket
import tempfile
import traceback
from itertools import cycle
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

import config
from silhouette import compose_quiz_card, RemoveBgError

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix=config.COMMAND_PREFIX, intents=intents)

# Users who can always run every command, regardless of server permissions.
AUTHORIZED_USER_IDS = {
    1224350217336524801,  # bot owner
    1392430185244131338,
    814033666850029579,
    534338705872257024,
}

# The ONLY users who can run /add_perms. They also count as authorized everywhere.
PERMS_ADMIN_IDS = {
    1224350217336524801,
    1020302545471680542,
    1392430185244131338,
}

MAX_BULK_IMAGES = 10

# channel_id -> round state (the !newguess flow)
active_rounds: dict[int, dict] = {}

# channel_id -> round state (the /start_quiz flow)
# {"answer", "started_by", "max_guesses", "guesses": {user_id: used}, "admins_can_guess"}
slash_quiz_rounds: dict[int, dict] = {}

# (channel_id, user_id) pairs currently typing a /hint (never scored as a guess)
pending_hint_users: set[tuple[int, int]] = set()

DEFAULT_GUESSES_PER_USER = 5

# Playful cricket sledges, one picked at random when a player uses their LAST guess.
# {mention} is replaced with the player's @mention. Add or edit lines freely.
SLEDGES = [
    "{mention} has used up every guess. such a pathetic nigga. ",
    "{mention} is out for a golden duck — no guesses left, no runs on the board. may gg give u c drama to cure u🦆",
    "{mention} swung at everything and connected with nothing. Nets session needed. ysr is a dumbass🥲",
    "{mention}'s DRS review is over - kya hua tukka nhi lga??",
    "{mention} is all out! Hand your lower bat inside someone else.  🏏",
    "Stumps drawn for {mention}. No tukka left in your mind. 🎒",
    "{mention} dropped the catch on every single guess. PotaNota is not impressed. 🙈",
    "{mention} got bowled out by a name they couldn't read.Nota is so useless. ",
    "{mention} ran out of guesses. Now u can cry about low ratings.  Nota is disasterous🏃",
    "{mention} faced those guesses like a tailender facing a yorker. Lots of flapping, no contact. gift nota some brain 😬",
    "{mention} has been dismissed — caught behind by their own overconfidence. Loki can loot more now 🧤",
    "That's {mention} walking off, bat under arm. Quiet pavilion. now cry about gtp is ass🤫",
    "{mention} is on the long walk back with no guesses left. Somebody get the drinks trolley. u can cry about others stealing your players 🥤",
    "{mention} guessed like it was the last over and needed 30 off 1. Bold, but no. u shall be touched by ysr now 💥",
    "{mention} got a peach of a delivery and edged it straight to slip. No guesses left. may megazord kiss u🍑",
    "{mention} is LBW — Leaving Brain Whenever. All guesses gone. may nithin touch u🧠",
]

# Rotate through the configured remove.bg API keys
removebg_keys = cycle(config.REMOVEBG_API_KEYS) if config.REMOVEBG_API_KEYS else None


# ---------------------------------------------------------------------------
# Persistent data (extra permissions + blacklist + running rounds)
# ---------------------------------------------------------------------------

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_data.json")

# {"extra_perms": [...], "blacklist": {"<guild_id>": [...]}, "rounds": {...}}
_data: dict = {"extra_perms": [], "blacklist": {}, "rounds": {"slash": {}, "prefix": {}}}


def _load_data() -> None:
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            loaded = json.load(f)
    except FileNotFoundError:
        return
    except Exception as e:
        print(f"Couldn't read {DATA_FILE}, starting fresh: {e}")
        return

    try:
        _data["extra_perms"] = [int(x) for x in loaded.get("extra_perms", [])]
        _data["blacklist"] = {
            str(g): [int(u) for u in users]
            for g, users in loaded.get("blacklist", {}).items()
        }
        rounds = loaded.get("rounds", {})
        for cid, st in rounds.get("slash", {}).items():
            st["guesses"] = {int(u): int(n) for u, n in st.get("guesses", {}).items()}
            st.setdefault("admins_can_guess", False)
            slash_quiz_rounds[int(cid)] = st
        for cid, st in rounds.get("prefix", {}).items():
            active_rounds[int(cid)] = st
    except Exception as e:
        print(f"Saved data was malformed, ignoring part of it: {e}")


def _save_data() -> None:
    try:
        tmp_path = DATA_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(_data, f, indent=2)
        os.replace(tmp_path, DATA_FILE)
    except Exception as e:
        print(f"Couldn't save {DATA_FILE}: {e}")


def _save_rounds() -> None:
    """Snapshot the running rounds to disk. Call after ANY change to them."""
    _data["rounds"] = {
        "slash": {
            str(cid): {**st, "guesses": {str(u): n for u, n in st["guesses"].items()}}
            for cid, st in slash_quiz_rounds.items()
        },
        "prefix": {str(cid): dict(st) for cid, st in active_rounds.items()},
    }
    _save_data()


_load_data()


def has_extra_perms(user_id: int) -> bool:
    return user_id in _data["extra_perms"]


def is_blacklisted(guild_id: int | None, user_id: int) -> bool:
    if guild_id is None:
        return False
    return user_id in _data["blacklist"].get(str(guild_id), [])


def add_to_blacklist(guild_id: int, user_id: int) -> bool:
    """Returns False if the user was already blacklisted."""
    users = _data["blacklist"].setdefault(str(guild_id), [])
    if user_id in users:
        return False
    users.append(user_id)
    _save_data()
    return True


def remove_from_blacklist(guild_id: int, user_id: int) -> bool:
    """Returns False if the user wasn't blacklisted."""
    users = _data["blacklist"].get(str(guild_id), [])
    if user_id not in users:
        return False
    users.remove(user_id)
    _save_data()
    return True


# ---------------------------------------------------------------------------
# Access control helpers
# ---------------------------------------------------------------------------

def _is_mod_or_owner(user: discord.abc.User, guild: discord.Guild | None) -> bool:
    if user.id in AUTHORIZED_USER_IDS or user.id in PERMS_ADMIN_IDS or has_extra_perms(user.id):
        return True
    if guild is None:
        return False
    member = user if isinstance(user, discord.Member) else guild.get_member(user.id)
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


def app_perms_admin_check():
    """Only the IDs in PERMS_ADMIN_IDS pass (server permissions don't matter)."""
    def predicate(interaction: discord.Interaction) -> bool:
        return interaction.user.id in PERMS_ADMIN_IDS
    return app_commands.check(predicate)


NOT_AUTHORIZED_MSG = "You need to be a server mod or the bot owner to use this command."
PERMS_NOT_AUTHORIZED_MSG = "Only specific bot owners can use this command."


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.CheckFailure):
        await ctx.reply(NOT_AUTHORIZED_MSG)
    elif isinstance(error, (commands.UserNotFound, commands.MemberNotFound,
                            commands.BadArgument, commands.MissingRequiredArgument)):
        await ctx.reply("Couldn't understand that — mention a user or paste their ID, "
                        "e.g. `!blacklist @username`.")
    else:
        traceback.print_exception(type(error), error, error.__traceback__)
        try:
            await ctx.reply("Something went wrong running that command (details are in the bot console).")
        except discord.HTTPException:
            pass


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        is_perms_cmd = interaction.command is not None and interaction.command.name == "add_perms"
        msg = PERMS_NOT_AUTHORIZED_MSG if is_perms_cmd else NOT_AUTHORIZED_MSG
    else:
        traceback.print_exception(type(error), error, error.__traceback__)
        msg = "Something went wrong running that command (details are in the bot console)."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except discord.HTTPException:
        pass


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
    if not g or not a:
        return False
    if g == a:
        return True
    # allow matching just the surname (last word of the answer)
    return g == a.split()[-1]


def build_letter_count_hint(answer: str) -> str:
    letters = len(normalize(answer).replace(" ", ""))
    words = len(normalize(answer).split())
    return f"Hint: the name has **{words}** word(s) and **{letters}** letters total."


def toggle_free_admin(channel_id: int) -> str:
    """Flip 'free admin' mode for the active /start_quiz round. Always returns a message."""
    quiz_state = slash_quiz_rounds.get(channel_id)
    if not quiz_state:
        if channel_id in active_rounds:
            return ("Free admin only applies to `/start_quiz` rounds. In a `!newguess` round "
                    "everyone (mods included) can already guess.")
        return "No active quiz in this channel. Start one with `/start_quiz` first."
    quiz_state["admins_can_guess"] = not quiz_state.get("admins_can_guess", False)
    _save_rounds()
    if quiz_state["admins_can_guess"]:
        return (
            "🔓 **Free admin mode ON** — mods and authorized users can now guess and "
            f"participate too (**{quiz_state['max_guesses']}** guesses each)."
        )
    return "🔒 **Free admin mode OFF** — mods and authorized users are back to spectating."


def render_card(player_bytes: bytes, bg_path, out_path: str) -> None:
    """Build one silhouette card, rotating through remove.bg keys on failure.

    Blocking (network + image work) — call it via asyncio.to_thread.
    """
    if removebg_keys is None:
        raise RemoveBgError("No remove.bg API keys are configured.")
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
            return
        except RemoveBgError as e:
            last_error = e
    if last_error:
        raise last_error


def _safe_remove(path: str | None) -> None:
    if not path:
        return
    try:
        os.remove(path)
    except OSError:
        pass


def _blacklist_user(guild: discord.Guild | None, target: discord.abc.User) -> str:
    if guild is None:
        return "Run this inside a server."
    if target.bot:
        return "You can't blacklist a bot."
    if add_to_blacklist(guild.id, target.id):
        return (
            f"🚫 {target.mention} is now blacklisted — they can't guess in current or future rounds "
            "until removed with `!removeblacklist` / `/removeblacklist`."
        )
    return f"{target.mention} is already blacklisted."


def _unblacklist_user(guild: discord.Guild | None, target: discord.abc.User) -> str:
    if guild is None:
        return "Run this inside a server."
    if remove_from_blacklist(guild.id, target.id):
        return f"✅ {target.mention} has been removed from the blacklist and can guess again."
    return f"{target.mention} isn't blacklisted."


async def sync_commands_to_guild(guild: discord.abc.Snowflake) -> list:
    """Push the bot's slash commands to ONE server (shows up instantly)."""
    bot.tree.clear_commands(guild=guild)
    bot.tree.copy_global_to(guild=guild)
    return await bot.tree.sync(guild=guild)


async def wipe_global_commands() -> None:
    """Empty Discord's global command list (the source of duplicate commands)."""
    await bot.http.bulk_upsert_global_commands(bot.application_id, [])


_commands_synced_on_start = False


@bot.event
async def on_ready():
    global _commands_synced_on_start
    print(f"Logged in as {bot.user} (id={bot.user.id})")
    if _commands_synced_on_start:
        return  # on_ready can fire again after a reconnect; sync only once
    _commands_synced_on_start = True

    try:
        dev_guild_id = getattr(config, "DEV_GUILD_ID", None)
        guilds = [discord.Object(id=dev_guild_id)] if dev_guild_id else list(bot.guilds)
        for guild in guilds:
            synced = await sync_commands_to_guild(guild)
            print(f"Synced {len(synced)} slash command(s) to guild {guild.id}: "
                  f"{', '.join(c.name for c in synced)}")
        await wipe_global_commands()
    except Exception as e:
        print(f"Slash command sync failed: {e}")


@bot.event
async def on_guild_join(guild: discord.Guild):
    try:
        await sync_commands_to_guild(guild)
    except Exception as e:
        print(f"Couldn't sync commands to new guild {guild.id}: {e}")


# ---------------------------------------------------------------------------
# Prefix commands
# ---------------------------------------------------------------------------

@bot.command(name="plshelp")
async def plshelp(ctx: commands.Context):
    """Prefix version of /help, in case slash commands haven't synced yet."""
    await ctx.reply(embed=build_help_embed())


@bot.command(name="pls")
async def pls(ctx: commands.Context, *, arg: str = ""):
    """`!pls help` — same as !plshelp."""
    if arg.strip().lower() == "help":
        await ctx.reply(embed=build_help_embed())


@bot.command(name="free_admin")
@mod_or_owner_check()
async def free_admin_prefix(ctx: commands.Context):
    """Prefix version of /free_admin."""
    await ctx.send(toggle_free_admin(ctx.channel.id))


@bot.command(name="sync")
@mod_or_owner_check()
async def sync_here(ctx: commands.Context):
    """Instantly sync slash commands to this server."""
    if ctx.guild is None:
        await ctx.reply("Run this inside a server.")
        return
    synced = await sync_commands_to_guild(ctx.guild)
    await wipe_global_commands()
    await ctx.reply(
        f"Synced {len(synced)} slash command(s) here: " + ", ".join(f"`/{c.name}`" for c in synced)
        + "\nDuplicates were cleared. If Discord still shows doubles, refresh it (Ctrl+R / restart the app)."
    )


@bot.command(name="newguess")
@mod_or_owner_check()
async def newguess(ctx: commands.Context, *, player_name: str = None):
    """Start a new round. Usage: !newguess Virat Kohli  (with photo attached)"""
    if player_name:
        player_name = player_name.strip().strip('"\u201c\u201d\'').strip()
    if not player_name:
        await ctx.reply("Usage: `!newguess <Player Name>` and attach the player's photo.")
        return

    if ctx.channel.id in active_rounds or ctx.channel.id in slash_quiz_rounds:
        await ctx.reply("A round is already active in this channel. Use `!reveal` to end it first.")
        return

    if not ctx.message.attachments:
        await ctx.reply("Please attach the player's photo to your message.")
        return

    player_attachment = ctx.message.attachments[0]
    background_attachment = (
        ctx.message.attachments[1] if len(ctx.message.attachments) > 1 else None
    )

    # Read attachments BEFORE deleting the command message (URLs may die with it).
    try:
        player_bytes = await player_attachment.read()
        bg_bytes = await background_attachment.read() if background_attachment else None
    except Exception as e:
        await ctx.send(f"Couldn't read the attached image(s): {e}")
        return

    try:
        await ctx.message.delete()
    except discord.Forbidden:
        await ctx.send(
            "⚠️ I couldn't delete your command message (missing **Manage Messages** permission), "
            "so the answer is visible above. Give me that permission to hide it."
        )
    except discord.HTTPException:
        pass

    status_msg = await ctx.send("Processing image (removing background)...")

    tmp_bg_path = None
    out_path = os.path.join(tempfile.gettempdir(), f"quiz_{ctx.message.id}.png")
    try:
        if bg_bytes:
            tmp_bg = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            tmp_bg.write(bg_bytes)
            tmp_bg.close()
            tmp_bg_path = tmp_bg.name
            bg_path = tmp_bg_path
        else:
            bg_path = config.DEFAULT_BACKGROUND

        # Blocking work runs in a thread so the bot stays responsive.
        await asyncio.to_thread(render_card, player_bytes, bg_path, out_path)
    except RemoveBgError as e:
        await status_msg.edit(content=f"remove.bg failed: {e}")
        _safe_remove(tmp_bg_path)
        _safe_remove(out_path)
        return
    except Exception as e:
        await status_msg.edit(content=f"Something went wrong building the card: {e}")
        _safe_remove(tmp_bg_path)
        _safe_remove(out_path)
        return

    active_rounds[ctx.channel.id] = {
        "answer": player_name,
        "started_by": ctx.author.id,
        "winner": None,
    }
    _save_rounds()

    try:
        await status_msg.delete()
    except discord.HTTPException:
        pass
    try:
        await ctx.send(
            content="🏏 **GUESS THE CRICKETER!** Type your guess in the chat. First correct answer wins!",
            file=discord.File(out_path, filename="guess_the_cricketer.png"),
        )
    except Exception as e:
        # Posting failed, so don't leave a hidden round running.
        active_rounds.pop(ctx.channel.id, None)
        _save_rounds()
        await ctx.send(f"Couldn't post the card, round cancelled: {e}")
    finally:
        _safe_remove(tmp_bg_path)
        _safe_remove(out_path)


@bot.command(name="bulk")
@mod_or_owner_check()
async def bulk(ctx: commands.Context):
    """Make silhouette cards for many photos at once (attach up to 10)."""
    attachments = [
        a for a in ctx.message.attachments
        if (a.content_type or "").startswith("image/")
    ]
    if not attachments:
        await ctx.reply("Usage: `!bulk` and attach the player photos (up to "
                        f"{MAX_BULK_IMAGES}) to the same message.")
        return
    if len(attachments) > MAX_BULK_IMAGES:
        await ctx.reply(f"Too many images — max **{MAX_BULK_IMAGES}** per `!bulk`. "
                        "Split them into two messages.")
        return

    total = len(attachments)
    status_msg = await ctx.reply(f"Processing **{total}** image(s)... (0/{total})")

    photos: list[tuple[str, bytes]] = []
    failed: list[str] = []
    for att in attachments:
        try:
            photos.append((att.filename, await att.read()))
        except Exception as e:
            failed.append(f"`{att.filename}` (couldn't read: {e})")

    out_paths: list[str] = []
    for i, (filename, data) in enumerate(photos, start=1):
        out_path = os.path.join(tempfile.gettempdir(), f"bulk_{ctx.message.id}_{i}.png")
        try:
            await asyncio.to_thread(render_card, data, config.DEFAULT_BACKGROUND, out_path)
            out_paths.append(out_path)
        except RemoveBgError as e:
            failed.append(f"`{filename}` (remove.bg failed: {e})")
            _safe_remove(out_path)
        except Exception as e:
            failed.append(f"`{filename}` ({e})")
            _safe_remove(out_path)

        try:
            await status_msg.edit(content=f"Processing **{total}** image(s)... ({i}/{len(photos)})")
        except discord.HTTPException:
            pass

    try:
        for start in range(0, len(out_paths), 10):
            chunk = out_paths[start:start + 10]
            files = [
                discord.File(p, filename=f"silhouette_{start + n}.png")
                for n, p in enumerate(chunk, start=1)
            ]
            await ctx.send(
                content=f"🏏 Silhouettes {start + 1}–{start + len(chunk)} of {len(out_paths)}:",
                files=files,
            )
    finally:
        for p in out_paths:
            _safe_remove(p)

    try:
        await status_msg.delete()
    except discord.HTTPException:
        pass

    if failed:
        await ctx.send("⚠️ These images failed:\n" + "\n".join(f"• {f}" for f in failed))


@bot.command(name="hint")
@mod_or_owner_check()
async def hint(ctx: commands.Context):
    """Letter-count hint for whichever round is running here."""
    state = slash_quiz_rounds.get(ctx.channel.id) or active_rounds.get(ctx.channel.id)
    if not state:
        await ctx.reply("No active round in this channel.")
        return
    await ctx.reply(build_letter_count_hint(state["answer"]))


@bot.command(name="reveal", aliases=["endguess"])
@mod_or_owner_check()
async def reveal(ctx: commands.Context):
    """Ends whichever round type is running here (!newguess OR /start_quiz)."""
    round_state = active_rounds.pop(ctx.channel.id, None)
    quiz_state = slash_quiz_rounds.pop(ctx.channel.id, None)
    state = quiz_state or round_state
    if not state:
        await ctx.reply("No active round in this channel.")
        return
    _save_rounds()
    await ctx.send(f"The answer was **{state['answer']}**! Round ended.")


# Using discord.User (not Member) so mentions AND raw IDs work, and it doesn't
# depend on the member cache / Members intent (that was breaking !blacklist).
@bot.command(name="blacklist")
@mod_or_owner_check()
async def blacklist_prefix(ctx: commands.Context, user: discord.User):
    """!blacklist @user — stop them guessing (current + future rounds)."""
    await ctx.reply(_blacklist_user(ctx.guild, user), allowed_mentions=discord.AllowedMentions.none())


@bot.command(name="removeblacklist", aliases=["remove_black"])
@mod_or_owner_check()
async def removeblacklist_prefix(ctx: commands.Context, user: discord.User):
    """!removeblacklist @user (or !remove_black @user) — let them guess again."""
    await ctx.reply(_unblacklist_user(ctx.guild, user), allowed_mentions=discord.AllowedMentions.none())


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------

@bot.tree.command(
    name="add_perms",
    description="Give or remove a user's access to the bot's mod commands.",
)
@app_commands.describe(
    user="The user to give (or take away) bot-command access",
    action="add (default) or remove",
)
@app_perms_admin_check()
async def add_perms(
    interaction: discord.Interaction,
    user: discord.User,
    action: Literal["add", "remove"] = "add",
):
    no_ping = discord.AllowedMentions.none()
    if user.bot:
        await interaction.response.send_message("Bots can't be given permissions.", ephemeral=True)
        return

    if action == "add":
        if user.id in AUTHORIZED_USER_IDS or user.id in PERMS_ADMIN_IDS:
            await interaction.response.send_message(
                f"{user.mention} already has full access.", ephemeral=True, allowed_mentions=no_ping
            )
            return
        if has_extra_perms(user.id):
            await interaction.response.send_message(
                f"{user.mention} already has bot-command access.", ephemeral=True, allowed_mentions=no_ping
            )
            return
        _data["extra_perms"].append(user.id)
        _save_data()
        await interaction.response.send_message(
            f"✅ {user.mention} can now use the bot's commands.", allowed_mentions=no_ping
        )
    else:
        if not has_extra_perms(user.id):
            await interaction.response.send_message(
                f"{user.mention} doesn't have extra access granted via `/add_perms`.",
                ephemeral=True, allowed_mentions=no_ping,
            )
            return
        _data["extra_perms"].remove(user.id)
        _save_data()
        await interaction.response.send_message(
            f"🔒 {user.mention}'s bot-command access was removed.", allowed_mentions=no_ping
        )


@bot.tree.command(name="blacklist", description="Stop a user from guessing in current and future rounds.")
@app_commands.describe(user="The user to blacklist")
@app_mod_or_owner_check()
async def blacklist_slash(interaction: discord.Interaction, user: discord.User):
    await interaction.response.send_message(
        _blacklist_user(interaction.guild, user),
        allowed_mentions=discord.AllowedMentions.none(),
    )


@bot.tree.command(name="removeblacklist", description="Let a blacklisted user guess again.")
@app_commands.describe(user="The user to remove from the blacklist")
@app_mod_or_owner_check()
async def removeblacklist_slash(interaction: discord.Interaction, user: discord.User):
    await interaction.response.send_message(
        _unblacklist_user(interaction.guild, user),
        allowed_mentions=discord.AllowedMentions.none(),
    )


@bot.tree.command(name="picture_send", description="Post an image you attach straight into this channel.")
@app_commands.describe(picture="The (already generated) silhouette/quiz image to post")
@app_mod_or_owner_check()
async def picture_send(interaction: discord.Interaction, picture: discord.Attachment):
    await interaction.response.defer(thinking=True, ephemeral=True)
    try:
        image_bytes = await picture.read()
        discord_file = discord.File(io.BytesIO(image_bytes), filename=picture.filename)
        await interaction.channel.send(file=discord_file)
        await interaction.followup.send("Image posted!", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"Couldn't post that image: {e}", ephemeral=True)


class StartQuizModal(discord.ui.Modal, title="Start Quiz"):
    """Private pop-up so the answer never appears in the channel."""

    player_name = discord.ui.TextInput(
        label="Player name (secret answer)",
        placeholder="e.g. Virat Kohli",
        min_length=1,
        max_length=100,
        required=True,
    )

    def __init__(self, guesses_per_user: int):
        super().__init__()
        self.guesses_per_user = guesses_per_user

    async def on_submit(self, interaction: discord.Interaction):
        channel_id = interaction.channel_id

        # Re-check: another round may have started while the modal was open.
        if channel_id in slash_quiz_rounds or channel_id in active_rounds:
            await interaction.response.send_message(
                "A round is already active in this channel.", ephemeral=True
            )
            return

        slash_quiz_rounds[channel_id] = {
            "answer": self.player_name.value.strip(),
            "started_by": interaction.user.id,
            "max_guesses": self.guesses_per_user,
            "guesses": {},
            "admins_can_guess": False,
        }
        _save_rounds()

        await interaction.response.send_message(
            f"🏏 **Quiz started!** Everyone gets **{self.guesses_per_user} guesses**. "
            "Type your guesses in the chat — ❌ means it didn't count as correct, ✅ means you got it!"
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        traceback.print_exception(type(error), error, error.__traceback__)
        if not interaction.response.is_done():
            await interaction.response.send_message(f"Something went wrong: {error}", ephemeral=True)


@bot.tree.command(name="start_quiz", description="Start a round with a set number of guesses per participant.")
@app_commands.describe(
    guesses_per_user="How many guesses each participant gets (default 5)",
)
@app_mod_or_owner_check()
async def start_quiz(
    interaction: discord.Interaction,
    guesses_per_user: app_commands.Range[int, 1, 20] = DEFAULT_GUESSES_PER_USER,
):
    if interaction.channel_id in slash_quiz_rounds or interaction.channel_id in active_rounds:
        await interaction.response.send_message(
            "A round is already active in this channel.", ephemeral=True
        )
        return

    await interaction.response.send_modal(StartQuizModal(guesses_per_user))


@bot.tree.command(
    name="free_admin",
    description="Toggle whether mods/authorized users can also guess in the active /start_quiz round.",
)
@app_mod_or_owner_check()
async def free_admin(interaction: discord.Interaction):
    msg = toggle_free_admin(interaction.channel_id)
    # Failure messages stay private; the ON/OFF announcement is public.
    ephemeral = msg.startswith("No active") or msg.startswith("Free admin only")
    await interaction.response.send_message(msg, ephemeral=ephemeral)


@bot.tree.command(
    name="timer",
    description="After a set delay, count down 6-to-1 and end the active round, revealing the answer.",
)
@app_commands.describe(wait_seconds="Total seconds until the round ends (last 6 are a countdown)")
@app_mod_or_owner_check()
async def timer(interaction: discord.Interaction, wait_seconds: app_commands.Range[int, 0, 3600]):
    channel_id = interaction.channel_id
    channel = interaction.channel

    target_quiz = slash_quiz_rounds.get(channel_id)
    target_round = active_rounds.get(channel_id)
    if target_quiz is None and target_round is None:
        await interaction.response.send_message(
            "No active round in this channel to put a timer on.", ephemeral=True
        )
        return

    def still_active() -> bool:
        # Identity check: a NEW round started later must not be ended by an old timer.
        return (
            (target_quiz is not None and slash_quiz_rounds.get(channel_id) is target_quiz)
            or (target_round is not None and active_rounds.get(channel_id) is target_round)
        )

    await interaction.response.send_message(f"⏳ Round will end in {wait_seconds} second(s).")

    countdown_len = min(6, wait_seconds)
    if wait_seconds > countdown_len:
        await asyncio.sleep(wait_seconds - countdown_len)

    if not still_active():
        await channel.send("That round already ended before the timer went off.")
        return

    if countdown_len > 0:
        countdown_msg = await channel.send(str(countdown_len))
        for n in range(countdown_len - 1, 0, -1):
            await asyncio.sleep(1)
            if not still_active():
                await channel.send("That round already ended before the timer went off.")
                return
            try:
                await countdown_msg.edit(content=str(n))
            except discord.HTTPException:
                pass
        await asyncio.sleep(1)

    answer = None
    if target_quiz is not None and slash_quiz_rounds.get(channel_id) is target_quiz:
        slash_quiz_rounds.pop(channel_id, None)
        answer = target_quiz["answer"]
    if target_round is not None and active_rounds.get(channel_id) is target_round:
        active_rounds.pop(channel_id, None)
        answer = answer or target_round["answer"]
    _save_rounds()

    if answer:
        await channel.send(f"⏰ **Time's up!** The answer was **{answer}**. Round ended.")
    else:
        await channel.send("That round already ended before the timer went off.")


def build_help_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🏏 Guess the Cricketer — Help",
        description=(
            "The bot posts a cricketer's silhouette photo and everyone guesses who it is "
            "right in the chat. Mods/authorized users run the commands below; "
            "everyone else just types guesses."
        ),
        color=discord.Color.green(),
    )
    embed.add_field(
        name="🎮 Old-style round (first correct guess wins)",
        value=(
            "`!newguess \"Player Name\"` — start a round (attach player photo, optional 2nd image "
            "as background). Your command message is auto-deleted to hide the answer.\n"
            "`!hint` — letter-count hint for the active round\n"
            "`!reveal` / `!endguess` — end the round early and reveal the answer"
        ),
        inline=False,
    )
    embed.add_field(
        name="🎯 New-style round (set number of guesses each)",
        value=(
            "`/start_quiz guesses_per_user` — start a round (default 5 guesses each); "
            "the player name is entered in a private pop-up. ❌ = wrong, ✅ = correct\n"
            "`/hint` — mod types a custom hint in chat (within 60s); everyone's guesses reset\n"
            "`/free_admin` (or `!free_admin`) — toggle: let mods/authorized users guess too "
            "in the active round. Resets when the round ends"
        ),
        inline=False,
    )
    embed.add_field(
        name="🛠️ Tools (work in both round types)",
        value=(
            "`/timer wait_seconds` — round ends after that many seconds; the last up-to-6 seconds "
            "show a countdown, then the answer is revealed\n"
            "`/picture_send` — post an attached image to the channel\n"
            f"`!bulk` — attach up to {MAX_BULK_IMAGES} photos; get a silhouette card for each"
        ),
        inline=False,
    )
    embed.add_field(
        name="🚫 Blacklist & access",
        value=(
            "`!blacklist @user` / `/blacklist` — user can't guess in current or future rounds\n"
            "`!removeblacklist @user` / `!remove_black @user` / `/removeblacklist` — undo it\n"
            "`/add_perms user` — give someone access to these commands (bot owners only)"
        ),
        inline=False,
    )
    embed.add_field(
        name="❓ Help & troubleshooting",
        value=(
            "`/help` — this message (everyone can use it)\n"
            "`!plshelp` / `!pls help` — same, works even if slash commands haven't synced\n"
            "`!sync` — force-sync slash commands to this server if one is missing"
        ),
        inline=False,
    )
    embed.add_field(
        name="🔒 Who can use these?",
        value=(
            "Server mods (Administrator / Manage Server), authorized users, and anyone given access "
            "with `/add_perms`. Everyone can use `/help` and `!plshelp`."
        ),
        inline=False,
    )
    embed.set_footer(text="Just type your guess as a normal chat message — no command needed to play!")
    return embed


@bot.tree.command(name="help", description="Show what this bot does and list all its commands.")
async def help_slash(interaction: discord.Interaction):
    await interaction.response.send_message(embed=build_help_embed())


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

    pending_key = (interaction.channel_id, interaction.user.id)
    pending_hint_users.add(pending_key)
    try:
        hint_msg = await bot.wait_for("message", check=check, timeout=60)
    except asyncio.TimeoutError:
        await interaction.followup.send(
            "Timed out waiting for the hint text. Run `/hint` again when you're ready.", ephemeral=True
        )
        return
    finally:
        # Small delay so on_message (which sees the same message) skips it first.
        await asyncio.sleep(1)
        pending_hint_users.discard(pending_key)

    # Round may have ended (someone guessed correctly) while we were waiting.
    round_state = slash_quiz_rounds.get(interaction.channel_id)
    if not round_state:
        await interaction.followup.send(
            "That round already ended before the hint could be sent.", ephemeral=True
        )
        return

    round_state["guesses"] = {}  # refresh everyone's guess count
    _save_rounds()

    await interaction.channel.send(
        f"💡 **Hint:** {hint_msg.content}\n"
        f"Everyone's guesses have been reset to **{round_state['max_guesses']}**!",
        allowed_mentions=discord.AllowedMentions.none(),
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

    # Blacklisted users can never guess (current or future rounds).
    if message.guild and is_blacklisted(message.guild.id, message.author.id):
        return

    # --- Original !newguess round: first correct guess wins ---
    round_state = active_rounds.get(message.channel.id)
    if round_state and not round_state.get("winner"):
        if is_correct_guess(message.content, round_state["answer"]):
            round_state["winner"] = message.author.id
            active_rounds.pop(message.channel.id, None)
            _save_rounds()
            await message.reply(
                f"✅ Correct! It was **{round_state['answer']}**. 🎉 Well played, {message.author.mention}!"
            )
        return

    # --- /start_quiz round: N guesses per user, ❌/✅ reactions ---
    quiz_state = slash_quiz_rounds.get(message.channel.id)
    if not quiz_state:
        return

    # A mod typing a hint is never a guess.
    if (message.channel.id, message.author.id) in pending_hint_users:
        return

    # Mods/owner/authorized users only guess when /free_admin mode is on.
    if not quiz_state.get("admins_can_guess") and _is_mod_or_owner(message.author, message.guild):
        return

    user_id = message.author.id
    guesses_used = quiz_state["guesses"].get(user_id, 0)

    if guesses_used >= quiz_state["max_guesses"]:
        return  # out of guesses; ignore silently so chat doesn't get spammy

    if is_correct_guess(message.content, quiz_state["answer"]):
        # End the round FIRST so a failed reaction can't leave it stuck.
        slash_quiz_rounds.pop(message.channel.id, None)
        _save_rounds()
        try:
            await message.add_reaction("✅")
        except discord.HTTPException:
            pass
        await message.reply(
            f"🎉 Correct! It was **{quiz_state['answer']}**. Well played,gud tukka {message.author.mention}! Round ended."
        )
        return

    # Wrong guess: count it and mark it with a cross.
    quiz_state["guesses"][user_id] = guesses_used + 1
    _save_rounds()
    try:
        await message.add_reaction("❌")
    except discord.HTTPException:
        pass  # missing permission / message deleted — the guess still counts

    remaining = quiz_state["max_guesses"] - quiz_state["guesses"][user_id]
    if remaining == 0:
        sledge = random.choice(SLEDGES).replace("{mention}", message.author.mention)
        await message.reply(
            f"{sledge}\nThat was your last guess — wait for a mod to run `/hint` for more.",
            allowed_mentions=discord.AllowedMentions(users=[message.author]),
        )


SINGLE_INSTANCE_PORT = 47653


def _acquire_single_instance_lock() -> socket.socket:
    """Refuse to start if another copy of the bot is already running on this computer.

    Two copies on the same token both answer every command, which shows up as
    doubled replies, different guess counts, and 'Unknown interaction' errors.
    """
    lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock.bind(("127.0.0.1", SINGLE_INSTANCE_PORT))
    except OSError:
        raise SystemExit(
            "Another copy of this bot is already running on this computer. "
            "Close it (or end the extra python.exe in Task Manager) and start again."
        )
    return lock


if __name__ == "__main__":
    _instance_lock = _acquire_single_instance_lock()  # keep a reference so it stays held
    bot.run(config.DISCORD_BOT_TOKEN)