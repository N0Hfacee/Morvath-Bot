import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional, Union

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("VoiceSentinel")

# Path file konfigurasi untuk menyimpan target channel secara permanen
CONFIG_FILE = Path("config.json")


def load_config() -> dict:
    """Membaca konfigurasi tersimpan."""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Gagal membaca config.json: {e}")
    return {}


def save_config(data: dict):
    """Menyimpan konfigurasi ke file agar tidak hilang saat bot restart."""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        logger.error(f"Gagal menyimpan config.json: {e}")


def get_target_channel_id() -> Optional[int]:
    """Mengambil target ID dari config.json atau fallback ke .env jika belum pernah dikonfigurasi."""
    cfg = load_config()
    # Jika key target_channel_id sudah ada di config (termasuk None saat bot di-leave)
    if "target_channel_id" in cfg:
        val = cfg["target_channel_id"]
        return int(val) if val else None

    # Fallback ke .env hanya jika config.json belum pernah diatur
    env_target = os.getenv("VOICE_CHANNEL_ID")
    if env_target and env_target.isdigit():
        return int(env_target)
    return None


def set_target_channel_id(channel_id: Optional[int]):
    """Menyimpan target channel ID baru ke config.json."""
    cfg = load_config()
    cfg["target_channel_id"] = channel_id
    save_config(cfg)


# Load environment variables
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# Setup Discord intents
intents = discord.Intents.default()
intents.voice_states = True
intents.message_content = True  # Dibutuhkan untuk prefix command (!)

bot = commands.Bot(command_prefix="!", intents=intents)

# Throttle voice (re)connection attempts: the watchdog and the voice-state handler
# must never fire a connect/disconnect in the same window.
_last_voice_attempt = 0.0
VOICE_ATTEMPT_COOLDOWN = 8.0


def resolve_guild(ctx: commands.Context) -> Optional[discord.Guild]:
    """
    Menemukan Guild Discord yang valid dari konteks eksekusi.
    Mengatasi kasus synthetic message / interaction di slash command.
    """
    guild_id = None
    if ctx.guild:
        guild_id = ctx.guild.id
    elif ctx.interaction and ctx.interaction.guild_id:
        guild_id = ctx.interaction.guild_id
    elif ctx.interaction and ctx.interaction.guild:
        guild_id = ctx.interaction.guild.id

    if guild_id:
        g = bot.get_guild(guild_id)
        if g:
            return g

    if ctx.guild:
        return ctx.guild

    if ctx.interaction and ctx.interaction.guild:
        return ctx.interaction.guild

    # Fallback jika bot hanya join di 1 server (server pribadi)
    if len(bot.guilds) == 1:
        return bot.guilds[0]

    return None


def get_author_voice_channel(ctx: commands.Context, guild: discord.Guild) -> Optional[Union[discord.VoiceChannel, discord.StageChannel]]:
    """
    Mendeteksi channel tempat user berada secara akurat.
    """
    if not guild:
        return None

    # 1. Cek langsung dari voice_states internal real guild (paling akurat)
    if hasattr(guild, "_voice_states"):
        vs = guild._voice_states.get(ctx.author.id)
        if vs and vs.channel:
            return vs.channel

    # 2. Cek dari member yang terdaftar di real guild
    member = guild.get_member(ctx.author.id)
    if member and member.voice and member.voice.channel:
        return member.voice.channel

    # 3. Cek dari ctx.author bawaan jika ada
    if hasattr(ctx.author, "voice") and ctx.author.voice and ctx.author.voice.channel:
        return ctx.author.voice.channel

    # 4. Scan setiap voice channel di guild untuk memastikan
    for vc in guild.voice_channels + guild.stage_channels:
        if any(m.id == ctx.author.id for m in vc.members):
            return vc

    return None


async def ensure_voice_connection():
    """Memastikan bot terhubung ke voice channel target."""
    global _last_voice_attempt
    now = asyncio.get_running_loop().time()
    if now - _last_voice_attempt < VOICE_ATTEMPT_COOLDOWN:
        return
    _last_voice_attempt = now

    target_id = get_target_channel_id()
    if not target_id:
        return

    channel = bot.get_channel(target_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(target_id)
        except Exception as e:
            logger.error(f"Gagal menemukan channel ID {target_id}: {e}")
            return

    if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
        logger.error(f"Channel ID {target_id} bukan voice/stage channel!")
        return

    guild = channel.guild
    voice_client = guild.voice_client

    try:
        if voice_client is None:
            logger.info(f"Menghubungkan ke voice channel: {channel.name} ({channel.id})...")
            await channel.connect(self_deaf=True, reconnect=True)
            logger.info(f"✅ Berhasil join ke: {channel.name}")
        elif not voice_client.is_connected():
            logger.warning("Voice client tidak terhubung, mencoba reconnect...")
            await voice_client.disconnect(force=True)
            await asyncio.sleep(1)
            await channel.connect(self_deaf=True, reconnect=True)
            logger.info(f"✅ Berhasil reconnect ke: {channel.name}")
        elif voice_client.channel.id != target_id:
            logger.info(f"Memindahkan bot dari {voice_client.channel.name} ke target: {channel.name}...")
            await voice_client.move_to(channel)
    except Exception as e:
        logger.error(f"Error saat memastikan koneksi voice: {e}")


@tasks.loop(seconds=20)
async def voice_watchdog():
    """Watchdog loop yang terus memantau status voice bot setiap 20 detik."""
    if bot.is_ready():
        await ensure_voice_connection()


@bot.event
async def on_ready():
    logger.info(f"Bot berhasil login sebagai: {bot.user.name} ({bot.user.id})")
    logger.info(f"Jumlah server terhubung: {len(bot.guilds)}")
    for g in bot.guilds:
        logger.info(f" - Server: '{g.name}' (ID: {g.id}) | {len(g.voice_channels)} Voice Channels")
    
    # Sync slash commands ke Discord
    try:
        synced = await bot.tree.sync()
        logger.info(f"Berhasil mensinkronisasi {len(synced)} slash commands.")
    except Exception as e:
        logger.warning(f"Gagal sinkronisasi slash commands: {e}")

    target_id = get_target_channel_id()
    if target_id:
        logger.info(f"Target Voice Channel tersimpan: {target_id}")
    else:
        logger.info("Belum ada target VC tersimpan. Ketik /join di Discord untuk mengunci VC.")

    # Mulai watchdog loop jika belum jalan
    if not voice_watchdog.is_running():
        voice_watchdog.start()

    await ensure_voice_connection()


@bot.event
async def on_voice_state_update(member, before, after):
    """Mendeteksi jika bot terputus atau dipindahkan secara paksa."""
    if member.id == bot.user.id:
        target_id = get_target_channel_id()
        if not target_id:
            return  # Bot memang sedang disuruh leave / nonaktif

        # Jika bot terputus dari voice secara tidak disengaja
        if after.channel is None:
            logger.warning("Bot terputus dari voice channel! Mencoba masuk kembali dalam 3 detik...")
            await asyncio.sleep(3)
            await ensure_voice_connection()
        # Jika bot dipindah ke channel lain
        elif after.channel.id != target_id:
            logger.warning(f"Bot dipindahkan ke {after.channel.name}. Mengembalikan ke target ({target_id})...")
            await asyncio.sleep(2)
            await ensure_voice_connection()


@bot.hybrid_command(name="join", description="Kunci dan jaga voice channel 24/7.")
@app_commands.describe(target="Pilih Voice Channel yang ingin dijaga (kosongkan jika kamu sudah di dalam VC)")
async def join_cmd(
    ctx: commands.Context,
    *,
    target: Optional[Union[discord.VoiceChannel, discord.StageChannel]] = None,
):
    """
    Jika kamu sedang di dalam VC, cukup ketik: /join
    Atau pilih channel: /join target: [Pilih Channel]
    """
    guild = resolve_guild(ctx)
    if not guild:
        if len(bot.guilds) == 0:
            await ctx.send(
                "❌ Bot belum dimasukkan ke server Discord mana pun!\n"
                "👉 Pastikan bot sudah di-invite ke server menggunakan OAuth2 URL (scope `bot` & `applications.commands`).",
                ephemeral=True,
            )
        else:
            await ctx.send("❌ Command ini hanya bisa digunakan di dalam server Discord!", ephemeral=True)
        return

    channel = target
    if channel is None:
        channel = get_author_voice_channel(ctx, guild)
        if channel is None:
            logger.warning(f"User {ctx.author.name} ({ctx.author.id}) tidak terdeteksi di voice state guild {guild.name}")
            await ctx.send(
                "❌ Bot belum mendeteksi kamu di dalam Voice Channel!\n"
                "👉 **Tips:** Masuk dulu ke VC lalu ketik `/join`, atau klik opsi `target` untuk memilih channel secara langsung.",
                ephemeral=True,
            )
            return

    set_target_channel_id(channel.id)
    await ctx.send(
        f"🛡️ **Berhasil dikunci!** Bot sekarang menjaga **{channel.name}** 24/7.\n"
        f"Bahkan jika bot restart, bot akan otomatis kembali ke channel ini."
    )
    await ensure_voice_connection()


@bot.hybrid_command(name="leave", description="Keluarkan bot dari voice channel dan hentikan penjagaan.")
async def leave_cmd(ctx: commands.Context):
    """Menghentikan penjagaan 24/7 dan bot keluar dari VC."""
    guild = resolve_guild(ctx)
    if not guild:
        await ctx.send("❌ Command ini hanya bisa digunakan di dalam server Discord!", ephemeral=True)
        return

    # Matikan target penjagaan di config secara permanen
    set_target_channel_id(None)

    voice_client = guild.voice_client
    if voice_client and voice_client.is_connected():
        channel_name = voice_client.channel.name
        await voice_client.disconnect(force=True)
        await ctx.send(f"👋 Bot telah keluar dari **{channel_name}** dan penjagaan 24/7 telah **dinonaktifkan**.")
    else:
        await ctx.send("👋 Penjagaan 24/7 telah **dinonaktifkan**. (Bot saat ini tidak sedang berada di VC mana pun)")


@bot.hybrid_command(name="status", description="Cek status penjagaan voice channel saat ini.")
async def status_cmd(ctx: commands.Context):
    """Melihat apakah bot sedang aktif menjaga VC."""
    target_id = get_target_channel_id()
    guild = resolve_guild(ctx)
    vc = guild.voice_client if guild else None
    latency = round(bot.latency * 1000)

    if vc and vc.is_connected():
        status_desc = f"✅ **Aktif Menjaga**: `{vc.channel.name}` (ID: `{vc.channel.id}`)"
        color = discord.Color.green()
    elif target_id:
        status_desc = f"⚠️ Target terkunci di ID `{target_id}`, sedang mencoba menghubungkan..."
        color = discord.Color.gold()
    else:
        status_desc = "💤 **Standby (Off)**: Penjagaan 24/7 sedang tidak aktif.\nMasuk ke voice channel dan ketik `/join` untuk mulai."
        color = discord.Color.blue()

    embed = discord.Embed(
        title="🤖 Status Voice Sentinel 24/7",
        description=status_desc,
        color=color,
    )
    embed.add_field(name="Ping", value=f"{latency}ms", inline=True)
    embed.add_field(name="Deafen Mode", value="Aktif (Mute & Deafen)", inline=True)
    embed.set_footer(text="Command: /join, /leave, /status")
    await ctx.send(embed=embed)


@bot.hybrid_command(name="ping", description="Cek latensi bot.")
async def ping_cmd(ctx: commands.Context):
    latency = round(bot.latency * 1000)
    await ctx.send(f"🏓 Pong! Latensi bot: `{latency}ms`")


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.CommandNotFound):
        return
    logger.error(f"Command error: {error}")
    await ctx.send(f"❌ Terjadi kesalahan: {error}")


def main():
    if not TOKEN:
        logger.error("Token Discord tidak ditemukan! Silakan isi DISCORD_TOKEN di file .env")
        sys.exit(1)
    bot.run(TOKEN)


if __name__ == "__main__":
    main()
