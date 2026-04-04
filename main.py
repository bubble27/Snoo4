"""Snoo5 Discord Bot - Entry point."""

import logging
import os
import shutil
import sys

import discord
from dotenv import load_dotenv
from discord.ext import commands

from bot.config import TEST_CHANNEL_ID, VERSION
from bot.data import DataManager
from bot.music.youtube import YouTubeService

# --- Logging ---
os.makedirs("Cache", exist_ok=True)
os.makedirs("Data Files", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("Cache/discord.log", encoding="utf-8", mode="w"),
    ],
)
log = logging.getLogger("snoo")

# --- FFmpeg check / auto-install ---
def _find_ffmpeg_in_winget() -> str | None:
    """Search common winget install locations for ffmpeg.exe."""
    import glob
    patterns = [
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\*FFmpeg*\**\bin"),
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links"),
    ]
    for pattern in patterns:
        for d in glob.glob(pattern, recursive=True):
            candidate = os.path.join(d, "ffmpeg.exe")
            if os.path.isfile(candidate):
                return d
    return None


def _ensure_ffmpeg() -> None:
    if shutil.which("ffmpeg"):
        log.info("FFmpeg found at: %s", shutil.which("ffmpeg"))
        return

    # Check if winget already installed it but PATH wasn't refreshed
    found_dir = _find_ffmpeg_in_winget()
    if found_dir:
        os.environ["PATH"] = found_dir + os.pathsep + os.environ.get("PATH", "")
        log.info("FFmpeg found at %s (added to PATH for this session)", found_dir)
        return

    log.warning("ffmpeg not found. Installing via winget in the background...")
    import subprocess
    import threading

    def _install() -> None:
        try:
            result = subprocess.run(
                ["winget", "install", "Gyan.FFmpeg",
                 "--accept-package-agreements", "--accept-source-agreements"],
                capture_output=True, text=True, timeout=300,
            )
            if result.returncode == 0:
                # Add to PATH for this session immediately
                found = _find_ffmpeg_in_winget()
                if found:
                    os.environ["PATH"] = found + os.pathsep + os.environ.get("PATH", "")
                    log.info("FFmpeg installed and added to PATH. Music is ready.")
                else:
                    log.info("FFmpeg installed. Restart the bot for music to work.")
            else:
                log.error("FFmpeg install failed (exit %d): %s", result.returncode, result.stderr.strip())
        except FileNotFoundError:
            log.error("winget not available. Install ffmpeg manually: https://ffmpeg.org/download.html")
        except subprocess.TimeoutExpired:
            log.error("FFmpeg install timed out.")
        except Exception as e:
            log.error("FFmpeg install error: %s", e)

    threading.Thread(target=_install, daemon=True).start()

_ensure_ffmpeg()


# --- Bot setup ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
intents.reactions = True
intents.voice_states = True

bot = commands.Bot(command_prefix=[], intents=intents)

# Attach shared services to the bot instance so cogs can access them
bot.data = DataManager()  # type: ignore[attr-defined]
bot.youtube = YouTubeService()  # type: ignore[attr-defined]

COGS = [
    "bot.cogs.music",
    "bot.cogs.profile",
    "bot.cogs.settings",
    "bot.cogs.admin",
]


@bot.event
async def on_ready() -> None:
    log.info("Logged in as %s", bot.user)

    # Initialize data BEFORE loading cogs (cogs depend on language/config being loaded)
    await bot.data.initialize(bot)  # type: ignore[attr-defined]
    log.info("Data initialized")

    # Load cogs
    for cog in COGS:
        try:
            await bot.load_extension(cog)
            log.info("Loaded cog: %s", cog)
        except commands.ExtensionAlreadyLoaded:
            pass
        except Exception:
            log.error("Failed to load cog: %s", cog, exc_info=True)

    # Sync slash commands (replaces all registered commands on Discord's side)
    synced = await bot.tree.sync()
    log.info("Synced %d commands", len(synced))

    # Set presence
    await bot.change_presence(
        activity=discord.Activity(type=discord.ActivityType.playing, name="music (possibly)")
    )

    # Announce startup
    channel = bot.get_channel(TEST_CHANNEL_ID)
    if channel and hasattr(channel, "send"):
        from socket import gethostname
        await channel.send(f"Running version: {VERSION} on {gethostname()}")


# --- Run ---
def main() -> None:
    load_dotenv()
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        log.critical("DISCORD_TOKEN not set. Add it to .env or your environment.")
        sys.exit(1)
    bot.run(token)


if __name__ == "__main__":
    main()
