from __future__ import annotations

# Bot metadata
VERSION = "0.5.0"
BOT_COLOR = 0xFAB384
DEFAULT_PALETTE = ((255, 218, 174), (250, 179, 132), (207, 112, 91))

# IDs
ADMIN_USER_ID = 401442600931950592
TEST_SERVER_ID = 905495146890666005
TEST_CHANNEL_ID = 1046466554310701116

# Data channel IDs (Discord channels used as a database)
DATA_CHANNELS = {
    "profile": 977316868253708359,
    "channel_messages": 913524223870398534,
    "song_history": 922592622248341505,
    "server_config": 985597229022724136,
    "playlists": 999117002323001354,
}

# Command prefixes (case-insensitive matching handled in bot setup)
PREFIXES = ["!s ", "hey snute, ", "hey snute ", "snute, ", "snute "]

# Default server settings
DEFAULT_SETTINGS = {"lang_set": "English", "votes": True, "downvote": False}
SETTINGS_META = {
    "votes": {"dev": False},
    "downvote": {"dev": False},
    "lang_set": {"dev": True},
}

# Emojis
EMOJIS = {
    "upvote": "<:Like:1493307031246082320>",
    "downvote": "<:Dislike:1493307109033508874>",
    "numbers": ["1\ufe0f\u20e3", "2\ufe0f\u20e3", "3\ufe0f\u20e3", "4\ufe0f\u20e3", "5\ufe0f\u20e3",
                "6\ufe0f\u20e3", "7\ufe0f\u20e3", "8\ufe0f\u20e3", "9\ufe0f\u20e3", "\U0001f51f"],
    "cross": "<:cross:905498493840400475>",
    "check": "<:check:905498494222098543>",
    "on": "<:on1:1124488396337860638><:on2:1124488399005433886>",
    "off": "<:off1:1124489547150000241><:off2:1124489549586903060>",
    "poll": [
        "<:A_:908477372397920316>", "<:B_:908477372637011968>",
        "<:C_:908477373090000916>", "<:D_:908477372607639572>",
        "<:E_:908477372561506324>", "<:F_:908477372511170620>",
        "<:G_:908477372829949952>",
    ],
    "playbar": {
        "fills_and_caps": [
            "<:CapR:1124495141927911494>", "<:CapL:1124495139864334377>",
            "<:BodyR:1124498384619843615>", "<:BodyL:1124498381474107442>",
        ],
        "focus_bars": {
            "cap_r": [
                "<:CapR1:1124498394614878330>", "<:CapR1:1124498394614878330>",
                "<:CapR2:1124498395575369728>", "<:CapR3:1124498687708631060>",
                "<:CapR4:1124731484947882026><:Center0:1124731486025814136>",
            ],
            "body": [
                "<:Center4:1124731487086981252><:Center0:1124731486025814136>",
                "<:Center1:1124498784605458463>", "<:Center2:1124498786115387512>",
                "<:Center3:1124498787893780582>",
                "<:Center4:1124731487086981252><:Center0:1124731486025814136>",
            ],
            "cap_l": [
                "<:Center4:1124731487086981252><:CapL0:1124731482947203154>",
                "<:CapL1:1124498388503777360>", "<:CapL2:1124498389887881216>",
                "<:CapL3:1124498390982594650>", "<:CapL3:1124498390982594650>",
            ],
        },
    },
    "like_off": "<:Like:1493307031246082320>",
    "like_on": "<:LikeOn:1493307070785650888>",
    "dislike_off": "<:Dislike:1493307109033508874>",
    "dislike_on": "<:DislikeOn:1493307139396079636>",
    "loop_off": "<:LoopOff:1493306893068800020>",
    "loop_on": "<:LoopOn:1493306912769572938>",
    "shuffle_off": "<:ShuffleOff:1493306950740475965>",
    "shuffle_on": "<:ShuffleOn:1493306964141412522>",
    "autoplay_off": "<:AutoplayOff:1493306994835198098>",
    "autoplay_on": "<:AutoplayOn:1493306981480398848>",
    "delete": "<:Trash:1125151486062628894>",
    "extend": "<:MenuUp:1125147518087483392>",
    "collapse": "<:MenuDown:1125147515835142215>",
    "back": "<:Back:1125143134976880740>",
    "skip": "<:Skip:1125143137355051068>",
    "pause": "<:Pause:1125136351063453756>",
    "play": "<:Play:1125136354028814366>",
}

# Icon URLs
LOADING_ICON = "<a:Loading2:1124429339778351155>"
ICONS = {
    "poll": "https://cdn.discordapp.com/attachments/908157040155832350/1124407360471961700/Poll.png",
    "music": "https://cdn.discordapp.com/attachments/908157040155832350/1124407819916025947/Music.png",
    "profile": "https://cdn.discordapp.com/attachments/908157040155832350/1124408205032833034/Profile.png",
    "settings": "https://cdn.discordapp.com/attachments/908157040155832350/1124406499230367914/Settings.png",
}
THUMBNAIL_ERROR = "https://cdn.discordapp.com/attachments/908157040155832350/1137151667826073731/Snoo_Thumbnail_Error.png"

# Playlist template
NEW_PLAYLIST = {
    "title": "new playlist",
    "desc": "",
    "cover": "https://cdn.discordapp.com/attachments/908157040155832350/999112912352333854/snoo_cover.png",
    "songs": [],
}

# Video info cache TTL in seconds (4 hours)
VIDEO_CACHE_TTL = 4 * 60 * 60

# Playbar length in segments
PLAYBAR_LENGTH = 18

# Now-playing full refresh interval (in update ticks, 1 tick = 1 second)
NP_REFRESH_INTERVAL = 300

# Auto-save interval in seconds (6 minutes)
AUTO_SAVE_INTERVAL = 360

# FFmpeg options
FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}
