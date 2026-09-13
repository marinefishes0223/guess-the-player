
import os

from dotenv import load_dotenv

load_dotenv()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

REMOVEBG_API_KEYS = [
    os.getenv("REMOVEBG_API_KEY_1"),
    os.getenv("REMOVEBG_API_KEY_2"),
    os.getenv("REMOVEBG_API_KEY_3"),
]

REMOVEBG_API_KEYS = [key for key in REMOVEBG_API_KEYS if key]

COMMAND_PREFIX = os.getenv("COMMAND_PREFIX", "!")

DEFAULT_BACKGROUND = os.path.join("assets", "default_background.png")

if not DISCORD_BOT_TOKEN:
    raise RuntimeError("DISCORD_BOT_TOKEN is missing. Set it in your .env file.")

if not REMOVEBG_API_KEYS:
    raise RuntimeError("At least one REMOVEBG API key is missing. Set it in your .env file.")

