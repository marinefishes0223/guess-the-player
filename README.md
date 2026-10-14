# 🏏 Guess the Cricketer Bot

A Discord bot for playing "guess the cricketer". The bot posts a silhouette picture of a cricketer, and everyone in the channel tries to guess who it is.

---

## What does this bot do?

* Posts a silhouette card of a cricketer (the photo's background is removed and the player is placed on a stadium-style background) and starts a guessing round.
* Everyone types their guess right in the chat. No special syntax needed.
* In guess-limit rounds, the bot reacts to every guess (✅ correct, ❌ wrong) and roasts anyone who uses up their last guess with a random cricket sledge.
* Mods can give hints, put a timer on a round, end it early and reveal the answer, or blacklist troublemakers.
* Rounds survive a bot restart, so a crash doesn't wipe a quiz in progress.
* Anyone can view the commands any time with **`/help`** or **`!plshelp`**.

**Only server mods, the bot owners, and people given access with `/add_perms` can start or manage rounds.** Regular members just guess in chat.

---

## Commands

There are two kinds of rounds. Ask your mods which one they are running.

### 🎮 Old-style round (first correct guess wins)

| Command | What it does |
| --- | --- |
| `!newguess "Player Name"` | Starts a round. Attach the player's photo to the same message (optionally attach a second image to use as the background). The bot deletes your command message so the answer isn't visible (needs the **Manage Messages** permission). |
| `!hint` | Gives a letter-count hint (number of words and letters in the name). |
| `!reveal` or `!endguess` | Ends the round right away and reveals the answer. |

Everyone, mods included, can guess in this round type. There is no guess limit.

### 🎯 New-style round (everyone gets a set number of guesses)

| Command | What it does |
| --- | --- |
| `/start_quiz guesses_per_user` | Starts a round (default 5 guesses each, max 20). A private pop-up asks for the answer, so it never appears in chat. |
| `/picture_send` | Posts an image the mod attaches (for example a silhouette made with `!bulk`) into the chat. |
| `/hint` | The mod types a custom hint in the channel within 60 seconds, and the bot shares it with everyone. This also gives everyone their guesses back. |
| `/free_admin` or `!free_admin` | Toggles whether mods and authorized users can guess too. It is off by default, so mods are ignored by the quiz. It resets when the round ends. |

Correct answer: the guesser gets ✅ and the round ends. Wrong answer: ❌ and one guess used. When a player uses their last guess, the bot replies with a random sledge from the `SLEDGES` list.

### 🛠️ Tools that work with both round types

| Command | What it does |
| --- | --- |
| `/timer wait_seconds` | Ends the active round after that many seconds (0–3600). The last 6 seconds are shown as a **6, 5, 4, 3, 2, 1** countdown in one edited message, then the answer is revealed. |
| `!bulk` | Attach up to 10 player photos to one message. The bot builds a silhouette card for each and posts them together. No round is started. |
| `!hint`, `!reveal`, `!endguess` | Work for both round types. |

### 🚫 Blacklist and access

| Command | What it does |
| --- | --- |
| `!blacklist @user` or `/blacklist` | Stops that user from guessing in current and future rounds on this server. Mentions and raw user IDs both work with the prefix command. |
| `!removeblacklist @user`, `!remove_black @user` or `/removeblacklist` | Undoes it. |
| `/add_perms user [add or remove]` | Gives or removes a user's access to the mod commands without editing code. **Only the IDs in `PERMS_ADMIN_IDS` can run this.** |

### ❓ Help and troubleshooting

| Command | What it does |
| --- | --- |
| `/help` | Shows the help embed. Everyone can use it. |
| `!plshelp` or `!pls help` | Same as `/help`, and works even if slash commands haven't synced. Everyone can use it. |
| `!sync` | Force-syncs slash commands to this server (use it if a slash command is missing or shows up twice). |

### How guessing works (for players)

1. Wait for a mod to start a round with a picture.
2. Type your guess as a normal chat message: the full name or just the surname. Capitalization and punctuation don't matter.
3. In a guess-limit round, watch for ✅ or ❌ on your message. Once you've used all your guesses, wait for a mod to run `/hint`, which gives everyone their guesses back.
4. The first person to guess correctly wins the round.

### Who can use the mod commands?

* Server admins or anyone with the **Manage Server** permission
* Users listed in `AUTHORIZED_USER_IDS` in the code
* Users given access with `/add_perms`

If someone else tries a mod command, the bot politely tells them they don't have permission.

---

## 🛠️ For Developers

### Problem statement

Run a lightweight, chat-native "guess the player" trivia game inside a Discord server, where:

* Non-technical members can play using nothing but plain chat messages.
* Moderators have a small toolkit to run a round end to end: post the puzzle image, hand out hints, cap attempts per player, blacklist abusers, and end a round on a timer.
* Round state is scoped per channel, so several channels can run independent rounds at the same time.
* The command surface is restricted to trusted users, and that trusted list can be extended at runtime without a code change.

### Architecture

* **Runtime:** `discord.py` (`commands.Bot`), single process. State is kept in memory and mirrored to a small JSON file (`bot_data.json`). There is no external database.
* **Two parallel round engines**, intentionally decoupled:
  * `active_rounds: dict[channel_id -> state]` is the `!newguess` flow. The first correct guess wins and there is no per-user tracking.
  * `slash_quiz_rounds: dict[channel_id -> state]` is the `/start_quiz` flow. It has per-user guess quotas (`guesses: dict[user_id -> count]`), ❌/✅ reactions, mod-authored hints, a `/free_admin` toggle (`admins_can_guess`), and sledges.
  * A channel can only run one round of either kind at a time. `!newguess`, `/start_quiz` and its modal all check both dicts. `/timer`, `!hint`, `!reveal` and `!free_admin` also look at both, so they work whichever flow started the round.
* **Single-instance lock:** at startup the bot binds a local port (`47653`) and refuses to start if another copy is already running. Two copies on the same token cause doubled replies and "Unknown interaction" errors.

### Persistence (`bot_data.json`)

The file sits next to `bot.py` and holds:

* `extra_perms`: user IDs granted access via `/add_perms`.
* `blacklist`: per-server lists of blacklisted user IDs.
* `rounds`: a snapshot of the running `!newguess` and `/start_quiz` rounds, written after every change through `_save_rounds()`.

Writes are atomic (a temp file followed by `os.replace`). On startup the file is loaded and the rounds come back, so a restart no longer wipes an active quiz. The file contains the current answers, so keep it private. `/timer` countdowns are **not** restored after a restart.

### Image pipeline

`!newguess` and `!bulk` download the attached photos and run each through `remove.bg` (via `silhouette.compose_quiz_card`) to cut out the subject. The cutout is composited onto a background (a custom attachment, `config.DEFAULT_BACKGROUND`, or a generated default) and re-uploaded. API keys are round-robined (`itertools.cycle`) across `config.REMOVEBG_API_KEYS`, with fallthrough to the next key on failure. The blocking work runs in a worker thread (`asyncio.to_thread`) so the bot stays responsive. Temp files are cleaned up afterwards.

### Access control

A single predicate, `_is_mod_or_owner(user, guild)`, is used through `commands.check` (prefix) and `app_commands.check` (slash) decorators on every mod command. A user passes if their ID is in `AUTHORIZED_USER_IDS` or `PERMS_ADMIN_IDS`, was granted access with `/add_perms`, or has Administrator or Manage Server. `/add_perms` itself is gated separately by `PERMS_ADMIN_IDS` only. Central `on_command_error` and `bot.tree.error` handlers turn `CheckFailure` into a friendly denial message and log other errors to the console instead of failing silently.

### Blacklist

Blacklisted IDs are checked in `on_message` before any guess is scored, so they never score or use up guesses in either round type. The prefix and slash commands take `discord.User` rather than `discord.Member`, so they don't depend on the member cache or the Members intent, and they accept raw IDs.

### Interactive hinting

`/hint` asks the invoking mod for free text via `bot.wait_for`, scoped to `(author_id, channel_id)` with a 60 second timeout. It then posts that text (with mentions disabled) and clears the per-user guess counters. While a mod is typing a hint, their `(channel, user)` pair is held in `pending_hint_users` so the message is never scored as a guess, even with `/free_admin` on.

### Timer / auto-resolve

`/timer` sleeps for `wait_seconds - min(6, wait_seconds)` seconds, then runs a single edited message as a 6→1 countdown. It re-checks that the round is still the same one, by object identity rather than just channel ID, before and during the countdown. An old timer therefore can't end a newer round in the same channel. When it fires it pops whichever round is active, saves state, and announces the answer.

### Slash command syncing

Commands are registered per server, not globally. Registering both ways made every command appear twice. On startup the bot syncs to every server it is in (or to `config.DEV_GUILD_ID` if set) and then wipes the global list. It also syncs when it joins a new server. `!sync` does the same on demand.

### Configuration

`config.py` (loaded from `.env`) provides `DISCORD_BOT_TOKEN`, `COMMAND_PREFIX`, `REMOVEBG_API_KEYS`, `DEFAULT_BACKGROUND`, and optionally `DEV_GUILD_ID`. In `bot.py`, `AUTHORIZED_USER_IDS` and `PERMS_ADMIN_IDS` are the hardcoded trusted user sets, and the `SLEDGES` list holds the roast lines used on a player's last guess (`{mention}` is replaced with the player's @mention).

### Known limitations / possible follow-ups

* No leaderboard across rounds. Winners aren't tracked long term.
* `/timer` countdowns and any in-progress `/hint` prompt don't survive a restart.
* State lives in a local JSON file. That is fine for one process, but it won't scale to multiple instances or shards.
* `bot.wait_for` in `/hint` ties up that command's execution for up to a minute. That is fine at current scale, but an event-driven approach would suit higher concurrency better.
* There is no rate limiting on guesses beyond the per-user quota. Discord's own rate limits are the only backstop in old-style rounds.
* The `remove.bg` dependency ties image quality and availability to a third-party API and its quota.
* The two permission tiers (`AUTHORIZED_USER_IDS` and `PERMS_ADMIN_IDS`) are hardcoded. Adding a new owner means editing the file.

### Setup

```bash
pip install -r requirements.txt   # if you see import errors, install the missing package with pip or uv

cp .env.example .env              # fill in DISCORD_BOT_TOKEN + REMOVEBG_API_KEYS

# optional: drop a stadium photo at assets/default_background.png
# (if there is none, a stadium-style background is generated automatically)

python bot.py
```

Invite the bot with the **Manage Messages** permission (so `!newguess` can hide the answer) and **Add Reactions** (for ✅/❌). Make sure the **Message Content** intent is enabled in the Discord developer portal. If a slash command is missing after the first start, run `!sync` in the server.