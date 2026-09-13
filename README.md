# 🏏 Guess the Cricketer Bot

A Discord bot for playing "guess the cricketer" — the bot posts a silhouette
picture of a cricketer, and everyone in the channel tries to guess who it is.

---

## What does this bot do?

- Posts a picture of a cricketer (with the background/face blurred out as a
  silhouette) and starts a guessing round.
- Everyone types their guess right in the chat — no special syntax needed.
- The bot reacts to guesses (✅ correct, ❌ wrong) so you always know where
  you stand.
- Mods can give hints, put a timer on a round, or end it early and reveal
  the answer.

**Only server mods and the bot owner can start/manage rounds.** Regular
members just guess in chat — no commands needed for them.

---

## Commands

There are two ways rounds work on this bot — an "old style" round and a
newer "guess limit" round. Ask your server mods which one they're using.

### 🎮 Old-style round (first correct guess wins)

| Command | What it does |
|---|---|
| `!newguess "Player Name"` | Starts a round. The mod attaches the player's photo (and optionally a background photo) to this message. |
| `!hint` | Gives a hint: how many words and letters are in the name. |
| `!reveal` or `!endguess` | Ends the round right away and reveals the answer. |

### 🎯 New-style round (everyone gets a set number of guesses)

| Command | What it does |
|---|---|
| `/start_quiz` | Starts a round. The mod sets the answer and how many guesses each person gets (default: 5). |
| `/picture_send` | Posts a picture the mod attaches (e.g. the silhouette image) into the chat. |
| `/hint` | A mod types a hint (like "this player is from Pakistan") and the bot shares it with everyone. This also gives everyone their guesses back. |
| `/timer` | Mod sets a countdown (in seconds). When time's up, the bot counts down **6, 5, 4, 3, 2, 1** in the chat, then ends the round and reveals the answer. Handy for wrapping up a round that's dragging on. |

### How guessing works (for players)

1. Wait for a mod to start a round with a picture.
2. Type your guess as a normal chat message — just the player's name or
   last name (spelling/capitalization doesn't need to be perfect).
3. If you're playing a "new-style" round, watch for the ✅/❌ reaction on
   your message — that tells you if it counted and whether it was right.
   You only get a limited number of guesses, so make them count!
4. First person to guess correctly wins the round.

### Who can use the mod commands?

Only:
- Server admins / anyone with "Manage Server" permission, or
- The bot's owner

If someone else tries a mod command, the bot will politely tell them they
don't have permission.

---
---

## 🛠️ For Developers

### Problem statement

Run a lightweight, chat-native "guess the player" trivia game inside a
Discord server, where:

- Non-technical members should be able to play using nothing but plain
  chat messages (no slash-command literacy required).
- Moderators need a small, low-friction toolkit to run a round end-to-end:
  post the puzzle image, dispense hints on demand, cap how many attempts
  each player gets, and forcibly end/resolve a round on a timer.
- Round state must be scoped per-channel so multiple channels can run
  independent, concurrent rounds without interfering with each other.
- Command surface should be restricted to trusted users (server
  mods / a hardcoded bot owner) to prevent players from self-serving hints
  or answers.

### Architecture

- **Runtime**: `discord.py` (`commands.Bot`), single process, in-memory
  state — no external database.
- **Two parallel round engines**, intentionally decoupled:
  - `active_rounds: dict[channel_id -> state]` — the original prefix-command
    ("`!newguess`") flow: first correct guess in chat wins. No per-user
    attempt tracking.
  - `slash_quiz_rounds: dict[channel_id -> state]` — the newer slash-command
    flow: per-user guess quotas (`guesses: dict[user_id -> count]`),
    ❌/✅ reactions, mod-authored hints, and timer-based resolution.
  - Both are keyed by `channel_id`, so a channel can only run one of each
    kind at a time; `/timer` and reveal logic check both dicts so it works
    regardless of which flow started the round.
- **Image pipeline** (`!newguess`): downloads the attached photo, runs it
  through `remove.bg` (via `silhouette.compose_quiz_card`) to cut out the
  subject, composites it onto a background (custom attachment or a
  generated default), and re-uploads the result. API keys are round-robined
  (`itertools.cycle`) across `config.REMOVEBG_API_KEYS` with fallthrough on
  failure.
- **Access control**: a single predicate,
  `_is_mod_or_owner(user, guild) -> bool`, checked via
  `commands.check` (prefix) / `app_commands.check` (slash) decorators on
  every command. Centralized `on_command_error` / `bot.tree.error` handlers
  convert `CheckFailure` into a friendly denial message instead of a raw
  traceback.
- **Interactive hinting** (`/hint`): rather than auto-generating a hint,
  the bot solicits free-text from the invoking mod via `bot.wait_for`
  scoped to `(author_id, channel_id)` with a 60s timeout, then relays that
  text to the channel and clears the per-user guess counters. The
  mod/owner's own messages are excluded from guess-scoring in `on_message`
  so a hint isn't mistaken for (and reacted to as) a guess attempt.
- **Timer/auto-resolve** (`/timer`): `asyncio.sleep`-based delay (bounded
  0–3600s so a channel can't be locked indefinitely), re-checks round
  liveness before and after the wait (a round may resolve naturally while
  waiting), then runs a single edited message as a 6→1 countdown before
  popping whichever round dict is active and announcing the answer.

### Known limitations / possible follow-ups

- All state is in-memory — a bot restart wipes any in-progress round.
- No persistence/leaderboard across rounds (winners aren't tracked
  long-term).
- `bot.wait_for` in `/hint` ties up that command's execution per-invocation;
  fine at current scale, would want a more event-driven approach at higher
  concurrency.
- No rate limiting on guesses beyond the per-user quota — a determined user
  could still spam wrong guesses quickly (Discord's own rate limits are the
  only backstop).
- `remove.bg` dependency means `!newguess` image quality/availability is
  tied to a third-party API and its quota.

### Setup

```bash
pip install -r requirements.txt (if u see any import errors, import using uv or !pip)
cp .env.example .env   # fill in DISCORD_BOT_TOKEN + REMOVEBG_API_KEYS
# drop a stadium photo at assets/default_background.png
python bot.py
```