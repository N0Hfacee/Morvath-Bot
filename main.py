import asyncio
import logging
import os
import sys
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("VoiceSentinel")

# Load environment variables
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# Setup Discord intents
intents = discord.Intents.default()
intents.voice_states = True
intents.message_content = True  # Dibutuhkan untuk prefix command (!)


class MorvathBot(commands.Bot):
    """Bot utama Morvath • The Iron Sentinel."""

    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        """Memuat seluruh modul Cogs dan Minigames secara dinamis dan otomatis."""
        cogs_dir = Path("cogs")
        for file in sorted(cogs_dir.rglob("*.py")):
            if file.name.startswith("__"):
                continue
            # Ubah path: cogs/games/blackjack.py -> cogs.games.blackjack
            extension = ".".join(file.with_suffix("").parts)
            try:
                await self.load_extension(extension)
                logger.info(f"⚔️ Modul berhasil dimuat: {extension}")
            except Exception as e:
                logger.error(f"❌ Gagal memuat modul {extension}: {e}")

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.CommandNotFound):
            return
        logger.error(f"Command error: {error}")
        await ctx.send(f"⚠️ **Gangguan Sihir:** *{error}*")


def main():
    if not TOKEN:
        logger.error("Token Discord tidak ditemukan! Silakan isi DISCORD_TOKEN di file .env")
        sys.exit(1)

    bot = MorvathBot()
    bot.run(TOKEN)


if __name__ == "__main__":
    main()
