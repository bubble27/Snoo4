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
log = logging.getLogger("snute")

# --- Dependency check / auto-fix ---
def _parse_version(v: str) -> tuple[int, ...]:
    import re
    # Strip pre-release/build suffixes like "2.8.0a5403+g5d74ed3e" -> "2.8.0"
    parts = re.split(r"[^0-9.]", v, maxsplit=1)[0].split(".")[:3]
    try:
        return tuple(int(x) for x in parts if x)
    except ValueError:
        return (0,)


def _ensure_dependencies() -> None:
    """Detect known missing/incompatible packages and offer to auto-fix before startup."""
    from importlib.metadata import version as pkg_version, PackageNotFoundError
    import subprocess

    issues: list[str] = []
    fixes: list[str] = []

    # discord.py >=2.7.1 — required for Discord's DAVE voice E2EE protocol (close code 4017)
    try:
        dpy = _parse_version(pkg_version("discord.py"))
        if dpy < (2, 7, 1):
            issues.append(
                f"discord.py {'.'.join(str(x) for x in dpy)} is too old "
                "(need >=2.7.1 for voice/DAVE support — fixes WebSocket close code 4017)"
            )
            fixes.append("discord.py>=2.7.1")
    except PackageNotFoundError:
        issues.append("discord.py is not installed")
        fixes.append("discord.py>=2.7.1")

    # davey — required for Discord's DAVE E2EE voice handshake
    try:
        pkg_version("davey")
    except PackageNotFoundError:
        issues.append(
            "davey is not installed "
            "(required for Discord voice E2EE/DAVE protocol — fixes WebSocket close code 4017)"
        )
        fixes.append("davey")

    if not issues:
        return

    print("\nDependency issues detected:")
    for issue in issues:
        print(f"  - {issue}")

    try:
        answer = input("\nFix automatically? [Y/n]: ").strip().lower()
    except EOFError:
        answer = "y"

    if answer in ("", "y", "yes"):
        print(f"Running: pip install {' '.join(fixes)}")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install"] + fixes,
            text=True,
        )
        if result.returncode == 0:
            print("Done. Please restart the bot.\n")
            sys.exit(0)
        else:
            print(f"pip install failed. Fix manually:\n  pip install {' '.join(fixes)}")
            sys.exit(1)
    else:
        print("Skipping. Voice connections may fail.\n")


_ensure_dependencies()

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


def _test_ffmpeg(ffmpeg_path: str) -> bool:
    """Verify FFmpeg can decode audio. Returns True if working."""
    import subprocess
    try:
        result = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-loglevel", "quiet",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=0.1",
             "-f", "null", "-"],
            capture_output=True, timeout=15,
        )
        return result.returncode == 0
    except Exception:
        return False


def _install_ffmpeg_winget() -> None:
    """Install (or reinstall) FFmpeg via winget in a background thread."""
    import subprocess
    import threading

    def _install() -> None:
        log.info("Installing FFmpeg via winget...")
        try:
            # Uninstall first so winget doesn't skip a "already installed" check
            subprocess.run(
                ["winget", "uninstall", "Gyan.FFmpeg",
                 "--accept-source-agreements"],
                capture_output=True, text=True, timeout=120,
            )
            result = subprocess.run(
                ["winget", "install", "Gyan.FFmpeg",
                 "--accept-package-agreements", "--accept-source-agreements"],
                capture_output=True, text=True, timeout=300,
            )
            if result.returncode == 0:
                found = _find_ffmpeg_in_winget()
                if found:
                    os.environ["PATH"] = found + os.pathsep + os.environ.get("PATH", "")
                    ffmpeg = shutil.which("ffmpeg")
                    if ffmpeg and _test_ffmpeg(ffmpeg):
                        log.info("FFmpeg reinstalled and verified. Music is ready.")
                    else:
                        log.warning("FFmpeg installed but audio test still failed. Try restarting.")
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


def _ensure_ffmpeg() -> None:
    # Locate FFmpeg (check PATH first, then winget install dirs)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        found_dir = _find_ffmpeg_in_winget()
        if found_dir:
            os.environ["PATH"] = found_dir + os.pathsep + os.environ.get("PATH", "")
            ffmpeg = shutil.which("ffmpeg")

    if ffmpeg:
        if _test_ffmpeg(ffmpeg):
            log.info("FFmpeg found and verified at: %s", ffmpeg)
            return
        # Found but audio test failed — broken install
        log.warning("FFmpeg at %s failed audio test (broken install or missing codecs).", ffmpeg)
        print(f"\nFFmpeg was found at {ffmpeg} but failed the audio capability test.")
        print("This usually means the binary is corrupted or missing codec support.")
    else:
        print("\nFFmpeg was not found.")

    print("Music will not work without a working FFmpeg.")
    try:
        answer = input("Reinstall automatically via winget? [Y/n]: ").strip().lower()
    except EOFError:
        answer = "y"

    if answer in ("", "y", "yes"):
        _install_ffmpeg_winget()
        print("Reinstalling FFmpeg in the background. Music may not work until complete.\n")
    else:
        print("Skipping FFmpeg install. Music commands will not work.\n")


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

    # Clear stale guild-level commands, then sync globally
    for guild in bot.guilds:
        try:
            bot.tree.clear_commands(guild=guild)
            await bot.tree.sync(guild=guild)
        except Exception:
            pass
    synced = await bot.tree.sync()
    log.info("Synced %d global commands", len(synced))

    # Set presence
    await bot.change_presence(
        activity=discord.Activity(type=discord.ActivityType.playing, name="music (possibly)")
    )

    # Announce startup
    channel = bot.get_channel(TEST_CHANNEL_ID)
    if channel and hasattr(channel, "send"):
        from socket import gethostname
        await channel.send(f"Running version: {VERSION} on {gethostname()}")


@bot.event
async def on_close() -> None:
    """Clean up resources on shutdown."""
    log.info("Shutting down...")

    # Flush VC time for anyone still connected
    profile_cog = bot.cogs.get("ProfileCog")
    if profile_cog:
        profile_cog.flush_all()

    # Close database connections
    bot.data.close()  # type: ignore[attr-defined]

    # Shut down YouTube thread pool
    from bot.music.youtube import _pool
    _pool.shutdown(wait=False)

    # Clean up temp files
    import glob
    for f in glob.glob("Cache/img_*.png") + glob.glob("Cache/graph.png"):
        try:
            os.remove(f)
        except OSError:
            pass

    log.info("Cleanup complete")


# --- Run ---
RESTART_EXIT_CODE = 42
_restart_requested = False


def request_restart() -> None:
    """Call this to make the bot exit with a restart code."""
    global _restart_requested
    _restart_requested = True


def main() -> None:
    load_dotenv()
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        log.critical("DISCORD_TOKEN not set. Add it to .env or your environment.")
        sys.exit(1)
    bot.run(token)
    if _restart_requested:
        sys.exit(RESTART_EXIT_CODE)


if __name__ == "__main__":
    main()
