import asyncio
import json
import logging
import os
import random
import sys
import time
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

# Path file konfigurasi dan statistik VC
CONFIG_FILE = Path("config.json")
VOICE_STATS_FILE = Path("voice_stats.json")

# Sesi aktif in-memory: {user_id: {"start": float, "guild_id": int, "name": str}}
active_voice_sessions: dict[int, dict] = {}


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


def load_voice_stats() -> dict:
    """Membaca statistik waktu pengabdian VC dari voice_stats.json."""
    if VOICE_STATS_FILE.exists():
        try:
            with open(VOICE_STATS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Gagal membaca voice_stats.json: {e}")
    return {}


def save_voice_stats(data: dict):
    """Menyimpan statistik waktu VC ke file agar aman permanen."""
    try:
        with open(VOICE_STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        logger.error(f"Gagal menyimpan voice_stats.json: {e}")


def flush_voice_sessions():
    """
    Menyinkronkan sesi aktif yang sedang berjalan ke voice_stats.json.
    Menjamin data tersimpan ke disk dan leaderboard selalu 100% up-to-date.
    """
    now = time.time()
    if not active_voice_sessions:
        return

    stats = load_voice_stats()
    updated = False

    for user_id, session in active_voice_sessions.items():
        elapsed = int(now - session["start"])
        if elapsed <= 0:
            continue

        guild_id_str = str(session["guild_id"])
        user_id_str = str(user_id)

        if guild_id_str not in stats:
            stats[guild_id_str] = {}

        if user_id_str not in stats[guild_id_str]:
            stats[guild_id_str][user_id_str] = {
                "name": session["name"],
                "total_seconds": 0,
            }

        stats[guild_id_str][user_id_str]["name"] = session["name"]
        stats[guild_id_str][user_id_str]["total_seconds"] += elapsed
        session["start"] = now
        updated = True

    if updated:
        save_voice_stats(stats)


def format_duration(seconds: int) -> str:
    """Mengubah detik ke format jam dan menit kerajaan."""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours > 0:
        return f"{hours}j {minutes}m"
    return f"{minutes}m"


def get_knight_title(seconds: int) -> str:
    """Memberikan gelar kehormatan berdasarkan lama waktu bertugas di VC."""
    hours = seconds / 3600
    if hours >= 200:
        return "👑 Kaisar Penjaga Tahta"
    elif hours >= 100:
        return "⚔️ Panglima Benteng Abadi"
    elif hours >= 50:
        return "🛡️ Ksatria Kehormatan"
    elif hours >= 24:
        return "🗡️ Garda Terdepan"
    elif hours >= 10:
        return "🏹 Prajurit Tangguh"
    else:
        return "📜 Ksatria Magang"


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


async def update_morvath_presence(channel_name: Optional[str] = None):
    """Memperbarui status aktivitas Morvath di profil Discord."""
    try:
        if channel_name:
            activity = discord.Activity(
                type=discord.ActivityType.watching,
                name=f"over {channel_name} 🛡️",
            )
        else:
            activity = discord.Activity(
                type=discord.ActivityType.watching,
                name="over the Castle Gates 🏰 | /join",
            )
        await bot.change_presence(activity=activity)
    except Exception as e:
        logger.debug(f"Gagal memperbarui presence: {e}")


async def ensure_voice_connection():
    """Memastikan bot terhubung ke voice channel target."""
    global _last_voice_attempt
    now = asyncio.get_running_loop().time()
    if now - _last_voice_attempt < VOICE_ATTEMPT_COOLDOWN:
        return
    _last_voice_attempt = now

    target_id = get_target_channel_id()
    if not target_id:
        await update_morvath_presence(None)
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
            await update_morvath_presence(channel.name)
        elif not voice_client.is_connected():
            logger.warning("Voice client tidak terhubung, mencoba reconnect...")
            await voice_client.disconnect(force=True)
            await asyncio.sleep(1)
            await channel.connect(self_deaf=True, reconnect=True)
            logger.info(f"✅ Berhasil reconnect ke: {channel.name}")
            await update_morvath_presence(channel.name)
        elif voice_client.channel.id != target_id:
            logger.info(f"Memindahkan bot dari {voice_client.channel.name} ke target: {channel.name}...")
            await voice_client.move_to(channel)
            await update_morvath_presence(channel.name)
        else:
            await update_morvath_presence(channel.name)
    except Exception as e:
        logger.error(f"Error saat memastikan koneksi voice: {e}")


@tasks.loop(seconds=20)
async def voice_watchdog():
    """Watchdog loop yang terus memantau status voice bot dan menyinkronkan data VC."""
    if bot.is_ready():
        flush_voice_sessions()
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

    # Daftarkan anggota yang sudah berada di VC saat bot baru online
    now = time.time()
    for g in bot.guilds:
        for vc in g.voice_channels + g.stage_channels:
            if g.afk_channel and vc.id == g.afk_channel.id:
                continue
            for m in vc.members:
                if not m.bot and m.id not in active_voice_sessions:
                    active_voice_sessions[m.id] = {
                        "start": now,
                        "guild_id": g.id,
                        "name": m.display_name,
                    }

    # Mulai watchdog loop jika belum jalan
    if not voice_watchdog.is_running():
        voice_watchdog.start()

    await ensure_voice_connection()


@bot.event
async def on_voice_state_update(member, before, after):
    """Mendeteksi perpindahan voice dan mencatat waktu pengabdian ksatria."""
    # 1. Khusus penjaga bot (Morvath)
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
        return

    # 2. Pelacak waktu ksatria (member asli, bukan bot)
    if member.bot:
        return

    guild = member.guild
    is_valid_vc = after.channel is not None and (not guild.afk_channel or after.channel.id != guild.afk_channel.id)
    was_in_valid_vc = before.channel is not None and (not guild.afk_channel or before.channel.id != guild.afk_channel.id)

    # Member masuk ke VC
    if is_valid_vc and not was_in_valid_vc:
        active_voice_sessions[member.id] = {
            "start": time.time(),
            "guild_id": guild.id,
            "name": member.display_name,
        }
    # Member keluar dari VC atau dipindah ke AFK channel
    elif not is_valid_vc and was_in_valid_vc:
        if member.id in active_voice_sessions:
            session = active_voice_sessions.pop(member.id)
            elapsed = int(time.time() - session["start"])
            if elapsed > 0:
                stats = load_voice_stats()
                gid = str(session["guild_id"])
                uid = str(member.id)
                if gid not in stats:
                    stats[gid] = {}
                if uid not in stats[gid]:
                    stats[gid][uid] = {"name": session["name"], "total_seconds": 0}
                stats[gid][uid]["name"] = member.display_name
                stats[gid][uid]["total_seconds"] += elapsed
                save_voice_stats(stats)
    # Member berpindah antar VC normal
    elif is_valid_vc and was_in_valid_vc:
        if member.id in active_voice_sessions:
            active_voice_sessions[member.id]["name"] = member.display_name


@bot.hybrid_command(name="join", description="Tempatkan Morvath untuk menjaga Voice Channel 24/7.")
@app_commands.describe(target="Pilih aula kerajaan yang ingin dijaga (kosongkan jika kamu sudah di dalam VC)")
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
                "❌ **Gerbang Tertutup!** Bot belum diundang ke server Discord mana pun.\n"
                "👉 Pastikan bot sudah di-invite menggunakan OAuth2 URL (scope `bot` & `applications.commands`).",
                ephemeral=True,
            )
        else:
            await ctx.send("❌ Titah ini hanya dapat diserukan di dalam wilayah benteng (server Discord)!", ephemeral=True)
        return

    channel = target
    if channel is None:
        channel = get_author_voice_channel(ctx, guild)
        if channel is None:
            logger.warning(f"User {ctx.author.name} ({ctx.author.id}) tidak terdeteksi di voice state guild {guild.name}")
            await ctx.send(
                "🛡️ **Aula Masih Lengang, Tuanku.**\n"
                "Morvath tidak mendeteksi kehadiran Tuan di ruang suara mana pun.\n"
                "👉 *Silakan masuk ke salah satu aula terlebih dahulu lalu ketik `/join`, atau tentukan pilihan di opsi `target`.*",
                ephemeral=True,
            )
            return

    set_target_channel_id(channel.id)
    await ctx.send(
        f"⚔️ **Sumpah Terpatri!**\n"
        f"Morvath kini berdiri tegak menjaga aula **{channel.name}** tanpa henti.\n"
        f"*Bahkan bila badai melanda atau gerbang ditutup, penjagaan ini tidak akan pernah padam.* 🛡️"
    )
    await ensure_voice_connection()


@bot.hybrid_command(name="leave", description="Bebastugaskan Morvath dari pos penjagaan.")
async def leave_cmd(ctx: commands.Context):
    """Menghentikan penjagaan 24/7 dan bot keluar dari VC."""
    guild = resolve_guild(ctx)
    if not guild:
        await ctx.send("❌ Titah ini hanya dapat diserukan di dalam server Discord!", ephemeral=True)
        return

    # Matikan target penjagaan di config secara permanen
    set_target_channel_id(None)
    await update_morvath_presence(None)

    voice_client = guild.voice_client
    if voice_client and voice_client.is_connected():
        channel_name = voice_client.channel.name
        await voice_client.disconnect(force=True)
        await ctx.send(
            f"🗡️ **Pedang Disarungkan.**\n"
            f"Sesuai titah Tuan, Morvath pamit undur diri dari aula **{channel_name}**.\n"
            f"*Penjagaan 24/7 telah dinonaktifkan. Sampai jumpa di giliran jaga berikutnya.*"
        )
    else:
        await ctx.send(
            "🛡️ **Laporan:** Morvath saat ini memang tidak sedang bertugas di aula mana pun, Tuanku. Penjagaan tetap dalam keadaan nonaktif."
        )


@bot.hybrid_command(name="status", description="Laporan inspeksi pos penjagaan benteng.")
async def status_cmd(ctx: commands.Context):
    """Melihat apakah bot sedang aktif menjaga VC."""
    target_id = get_target_channel_id()
    guild = resolve_guild(ctx)
    vc = guild.voice_client if guild else None
    latency = round(bot.latency * 1000)

    if vc and vc.is_connected():
        status_desc = (
            f"🛡️ **Pos Penjagaan:** Berjaga teguh di aula **{vc.channel.name}** (`{vc.channel.id}`)\n"
            f"🔥 *Api obor menyala abadi. Selama Morvath berjaga, waktu kerajaan tidak akan pudar.*"
        )
        color = discord.Color.gold()
    elif target_id:
        status_desc = f"⚠️ Pos terpatri pada aula `{target_id}`. Gerbang sedang dipersiapkan untuk kembali berjaga..."
        color = discord.Color.orange()
    else:
        status_desc = (
            "💤 **Status: Siaga di Barak (Standby)**\n"
            "Belum ada titah penjagaan yang diberikan.\n"
            "👉 *Masuklah ke salah satu aula lalu serukan `/join` untuk menempatkan penjaga.*"
        )
        color = discord.Color.dark_grey()

    embed = discord.Embed(
        title="🏰 Laporan Penjagaan Benteng — Morvath",
        description=status_desc,
        color=color,
    )
    embed.add_field(name="⚔️ Refleks Senjata", value=f"`{latency}ms`", inline=True)
    embed.add_field(name="🤫 Sumpah Keheningan", value="`Aktif` (Deafen & Mute)", inline=True)
    embed.set_footer(text="⚔️ Morvath • The Iron Sentinel | Penjaga Tak Pernah Tidur")
    await ctx.send(embed=embed)


@bot.hybrid_command(name="ping", description="Uji kesiapan dan ketajaman refleks Morvath.")
async def ping_cmd(ctx: commands.Context):
    latency = round(bot.latency * 1000)
    await ctx.send(f"⚔️ *Menghunus pedang dalam {latency}ms!* Morvath selalu siap sedia menerima titah, Tuanku.")


@bot.hybrid_command(name="vctop", description="Lihat klasemen dewan ksatria dengan waktu pengabdian VC tertinggi.")
async def vctop_cmd(ctx: commands.Context):
    """Menampilkan 10 ksatria dengan waktu Voice Channel terbanyak."""
    guild = resolve_guild(ctx)
    if not guild:
        await ctx.send("❌ Titah ini hanya dapat diserukan di dalam server Discord!", ephemeral=True)
        return

    flush_voice_sessions()
    stats = load_voice_stats()
    guild_stats = stats.get(str(guild.id), {})

    if not guild_stats:
        await ctx.send(
            "📜 **Belum ada catatan pengabdian.**\n"
            "Belum ada ksatria yang menghabiskan waktu di aula suara kerajaan ini. Masuklah ke Voice Channel untuk mulai mencatat!",
            ephemeral=True,
        )
        return

    sorted_users = sorted(guild_stats.items(), key=lambda x: x[1].get("total_seconds", 0), reverse=True)
    top_10 = sorted_users[:10]

    embed = discord.Embed(
        title=f"🏆 Dewan Kehormatan Benteng — {guild.name}",
        description="*Daftar ksatria dengan dedikasi pengabdian tertinggi di aula kerajaan.*\n\n",
        color=discord.Color.gold(),
    )

    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for rank, (uid, data) in enumerate(top_10, 1):
        name = data.get("name", "Ksatria Misterius")
        sec = data.get("total_seconds", 0)
        time_str = format_duration(sec)
        title = get_knight_title(sec)

        medal = medals[rank - 1] if rank <= 3 else f"`#{rank}`"
        lines.append(f"{medal} **{name}** — `{time_str}`\n    └ *{title}*")

    embed.description += "\n\n".join(lines)
    embed.set_footer(text="⚔️ Morvath Voice Sentinel • Diperbarui secara real-time")
    await ctx.send(embed=embed)


@bot.hybrid_command(name="vctime", description="Cek catatan waktu pengabdian dan gelar ksatria.")
@app_commands.describe(user="Pilih anggota yang ingin dicek (kosongkan untuk melihat dirimu)")
async def vctime_cmd(ctx: commands.Context, user: Optional[discord.Member] = None):
    """Melihat statistik VC personal seorang ksatria."""
    guild = resolve_guild(ctx)
    if not guild:
        await ctx.send("❌ Titah ini hanya dapat diserukan di dalam server Discord!", ephemeral=True)
        return

    target_user = user or ctx.author
    if target_user.bot:
        await ctx.send("🤖 Bangsa golem/bot tidak memiliki catatan pengabdian ksatria!", ephemeral=True)
        return

    flush_voice_sessions()
    stats = load_voice_stats()
    guild_stats = stats.get(str(guild.id), {})

    sorted_users = sorted(guild_stats.items(), key=lambda x: x[1].get("total_seconds", 0), reverse=True)
    user_data = guild_stats.get(str(target_user.id))

    total_sec = user_data.get("total_seconds", 0) if user_data else 0
    time_str = format_duration(total_sec)
    title = get_knight_title(total_sec)

    # Menghitung ranking di server
    rank = next((i for i, (uid, _) in enumerate(sorted_users, 1) if uid == str(target_user.id)), None)
    rank_str = f"#{rank} dari {len(sorted_users)} Ksatria" if rank else "Belum Masuk Peringkat"

    # Status kehadiran di VC
    if target_user.id in active_voice_sessions:
        current_ch = target_user.voice.channel.name if target_user.voice and target_user.voice.channel else "Aula Kerajaan"
        status_text = f"🟢 Sedang bertugas di **{current_ch}**"
    else:
        status_text = "💤 Sedang beristirahat di luar benteng"

    embed = discord.Embed(
        title=f"🛡️ Catatan Pengabdian — {target_user.display_name}",
        color=discord.Color.green() if user_data else discord.Color.blue(),
    )
    if target_user.display_avatar:
        embed.set_thumbnail(url=target_user.display_avatar.url)

    embed.add_field(name="⏱️ Total Waktu", value=f"`{time_str}`", inline=True)
    embed.add_field(name="🎖️ Peringkat", value=f"`{rank_str}`", inline=True)
    embed.add_field(name="📜 Gelar Kehormatan", value=title, inline=False)
    embed.add_field(name="📍 Status Saat Ini", value=status_text, inline=False)
    embed.set_footer(text="⚔️ Morvath Voice Sentinel • Dedikasi Tanpa Henti")
    await ctx.send(embed=embed)


# ==========================================
# MINIGAME: BLACKJACK 21 KASINO BENTENG
# ==========================================

SUITS = ["♠️", "♥️", "♦️", "♣️"]
RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]


def create_blackjack_deck() -> list[dict]:
    deck = [{"rank": r, "suit": s} for r in RANKS for s in SUITS]
    random.shuffle(deck)
    return deck


def card_display(c: dict) -> str:
    return f"`{c['rank']}{c['suit']}`"


def calculate_blackjack_hand(hand: list[dict]) -> int:
    val = 0
    aces = 0
    for c in hand:
        r = c["rank"]
        if r in ["J", "Q", "K"]:
            val += 10
        elif r == "A":
            aces += 1
            val += 11
        else:
            val += int(r)
    while val > 21 and aces:
        val -= 10
        aces -= 1
    return val


class BlackjackView(discord.ui.View):
    def __init__(
        self,
        author: discord.Member,
        guild_id: int,
        bet_minutes: int,
        deck: list[dict],
        player_hand: list[dict],
        dealer_hand: list[dict],
    ):
        super().__init__(timeout=60.0)
        self.author = author
        self.guild_id = guild_id
        self.bet_minutes = bet_minutes
        self.deck = deck
        self.player_hand = player_hand
        self.dealer_hand = dealer_hand
        self.game_over = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                "❌ Ini adalah meja judi milik ksatria lain! Gunakan `/blackjack` untuk membuka mejamu sendiri.",
                ephemeral=True,
            )
            return False
        return True

    def build_embed(
        self,
        reveal_dealer: bool = False,
        outcome_text: Optional[str] = None,
        color: Optional[discord.Color] = None,
    ) -> discord.Embed:
        player_score = calculate_blackjack_hand(self.player_hand)
        player_cards = " ".join(card_display(c) for c in self.player_hand)

        if reveal_dealer:
            dealer_score = calculate_blackjack_hand(self.dealer_hand)
            dealer_cards = " ".join(card_display(c) for c in self.dealer_hand)
            dealer_val_str = f"Skor: **{dealer_score}**"
        else:
            dealer_cards = f"{card_display(self.dealer_hand[0])} `🂠 Tersembunyi`"
            dealer_val_str = "Skor: **?**"

        if color is None:
            color = discord.Color.gold()

        embed = discord.Embed(
            title="🎰 Kasino Bawah Tanah Benteng — Blackjack 21",
            color=color,
        )

        taruhan_str = f"`{self.bet_minutes} Menit Waktu VC`" if self.bet_minutes > 0 else "`Mode Latihan (Bebas)`"
        embed.add_field(name="💰 Taruhan", value=taruhan_str, inline=False)
        embed.add_field(name=f"🛡️ Morvath (Bandar Istana) [{dealer_val_str}]", value=dealer_cards, inline=False)
        embed.add_field(name=f"⚔️ {self.author.display_name} [Skor: **{player_score}**]", value=player_cards, inline=False)

        if outcome_text:
            embed.add_field(name="📜 Hasil Pertaruhan", value=outcome_text, inline=False)

        embed.set_footer(text="⚔️ Morvath • The Iron Sentinel | Kasino Kerajaan")
        return embed

    def apply_bet_result(self, delta_minutes: int):
        """Menambah atau mengurangi waktu VC berdasarkan hasil permainan."""
        if delta_minutes == 0:
            return
        stats = load_voice_stats()
        gid = str(self.guild_id)
        uid = str(self.author.id)
        if gid not in stats:
            stats[gid] = {}
        if uid not in stats[gid]:
            stats[gid][uid] = {"name": self.author.display_name, "total_seconds": 0}

        current = stats[gid][uid].get("total_seconds", 0)
        new_total = max(0, current + (delta_minutes * 60))
        stats[gid][uid]["total_seconds"] = new_total
        stats[gid][uid]["name"] = self.author.display_name
        save_voice_stats(stats)

    @discord.ui.button(label="Tarik Kartu (Hit)", style=discord.ButtonStyle.primary, emoji="🃏")
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.game_over:
            return

        self.player_hand.append(self.deck.pop())
        player_score = calculate_blackjack_hand(self.player_hand)

        if player_score > 21:
            self.game_over = True
            for item in self.children:
                item.disabled = True

            delta = -self.bet_minutes if self.bet_minutes > 0 else 0
            if delta != 0:
                self.apply_bet_result(delta)

            punishment = f"\n*Kamu kehilangan {self.bet_minutes} menit waktu pengabdian VC!*" if self.bet_minutes > 0 else ""
            msg = f"💥 **BUST!** Skormu ({player_score}) melebihi 21! Morvath menyapu taruhan dari meja.{punishment}"
            embed = self.build_embed(reveal_dealer=True, outcome_text=msg, color=discord.Color.red())
            await interaction.response.edit_message(embed=embed, view=self)
        elif player_score == 21:
            await self.stand_logic(interaction)
        else:
            embed = self.build_embed()
            await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Cukup (Stand)", style=discord.ButtonStyle.secondary, emoji="🛑")
    async def stand(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.game_over:
            return
        await self.stand_logic(interaction)

    async def stand_logic(self, interaction: discord.Interaction):
        self.game_over = True
        for item in self.children:
            item.disabled = True

        player_score = calculate_blackjack_hand(self.player_hand)

        # Bandar menarik kartu sampai minimal 17
        while calculate_blackjack_hand(self.dealer_hand) < 17:
            self.dealer_hand.append(self.deck.pop())

        dealer_score = calculate_blackjack_hand(self.dealer_hand)

        if dealer_score > 21:
            delta = self.bet_minutes
            self.apply_bet_result(delta)
            reward = f"\n*Kamu memenangkan +{self.bet_minutes} menit waktu pengabdian VC!*" if self.bet_minutes > 0 else ""
            msg = f"🎉 **Morvath BUST ({dealer_score})!** Bandar melebihi 21, kemenangan untukmu!{reward}"
            color = discord.Color.green()
        elif player_score > dealer_score:
            delta = self.bet_minutes
            self.apply_bet_result(delta)
            reward = f"\n*Kamu memenangkan +{self.bet_minutes} menit waktu pengabdian VC!*" if self.bet_minutes > 0 else ""
            msg = f"🏆 **Kamu Menang!** Skormu ({player_score}) mengalahkan Morvath ({dealer_score})!{reward}"
            color = discord.Color.green()
        elif player_score < dealer_score:
            delta = -self.bet_minutes
            self.apply_bet_result(delta)
            punishment = f"\n*Kamu kehilangan {self.bet_minutes} menit waktu pengabdian VC!*" if self.bet_minutes > 0 else ""
            msg = f"💀 **Morvath Menang!** Skor bandar ({dealer_score}) melampaui skormu ({player_score}).{punishment}"
            color = discord.Color.red()
        else:
            msg = f"🤝 **SERI (Push)!** Skor kalian sama kuat ({player_score}). Tidak ada waktu yang berpindah tangan."
            color = discord.Color.gold()

        embed = self.build_embed(reveal_dealer=True, outcome_text=msg, color=color)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self):
        if not self.game_over:
            self.game_over = True
            for item in self.children:
                item.disabled = True


@bot.hybrid_command(
    name="blackjack",
    aliases=["bj"],
    description="Mainkan kartu Blackjack 21 melawan Morvath di kasino bawah tanah benteng.",
)
@app_commands.describe(taruhan="Menit waktu VC yang dipertaruhkan (opsional, maks 60m. Kosongkan untuk mode latihan)")
async def blackjack_cmd(ctx: commands.Context, taruhan: Optional[int] = 0):
    guild = resolve_guild(ctx)
    if not guild:
        await ctx.send("❌ Titah ini hanya dapat diserukan di dalam server Discord!", ephemeral=True)
        return

    bet = taruhan or 0
    if bet < 0:
        await ctx.send("❌ Taruhan tidak boleh bernilai negatif!", ephemeral=True)
        return
    if bet > 60:
        await ctx.send("⚠️ Batas taruhan kasino maksimal adalah **60 menit** per putaran demi menjaga stabilitas kerajaan!", ephemeral=True)
        return

    flush_voice_sessions()
    stats = load_voice_stats()
    guild_stats = stats.get(str(guild.id), {})
    user_data = guild_stats.get(str(ctx.author.id), {})
    user_seconds = user_data.get("total_seconds", 0)

    if bet > 0:
        required_seconds = bet * 60
        if user_seconds < required_seconds:
            user_min = user_seconds // 60
            await ctx.send(
                f"🛡️ **Waktu pengabdianmu belum mencukupi!**\n"
                f"Kamu mempertaruhkan **{bet} menit**, tetapi catatan waktumu baru **{user_min} menit**.\n"
                f"👉 *Kumpulkan waktu di VC terlebih dahulu, atau mainkan mode latihan dengan mengetik `/blackjack` tanpa taruhan.*",
                ephemeral=True,
            )
            return

    deck = create_blackjack_deck()
    player_hand = [deck.pop(), deck.pop()]
    dealer_hand = [deck.pop(), deck.pop()]

    view = BlackjackView(
        author=ctx.author,
        guild_id=guild.id,
        bet_minutes=bet,
        deck=deck,
        player_hand=player_hand,
        dealer_hand=dealer_hand,
    )

    player_score = calculate_blackjack_hand(player_hand)
    dealer_score = calculate_blackjack_hand(dealer_hand)

    # Cek Natural Blackjack (21 instan pada pembagian kartu awal)
    if player_score == 21:
        view.game_over = True
        for item in view.children:
            item.disabled = True

        if dealer_score == 21:
            msg = "🤝 **DUAL BLACKJACK!** Kalian berdua sama-sama mendapat 21 alami! Hasil seri (Push)."
            color = discord.Color.gold()
        else:
            win_payout = int(bet * 1.5) if bet > 0 else 0
            if win_payout > 0:
                view.apply_bet_result(win_payout)
            bonus = f"\n*Bonus Blackjack 1.5x: Kamu memenangkan +{win_payout} menit waktu VC!*" if win_payout > 0 else ""
            msg = f"🔥 **NATURAL BLACKJACK!** 21 sempurna di pembagian awal! Morvath takjub.{bonus}"
            color = discord.Color.green()

        embed = view.build_embed(reveal_dealer=True, outcome_text=msg, color=color)
        await ctx.send(embed=embed)
        return

    embed = view.build_embed()
    await ctx.send(embed=embed, view=view)


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.CommandNotFound):
        return
    logger.error(f"Command error: {error}")
    await ctx.send(f"⚠️ **Gangguan Sihir:** *{error}*")


def main():
    if not TOKEN:
        logger.error("Token Discord tidak ditemukan! Silakan isi DISCORD_TOKEN di file .env")
        sys.exit(1)
    bot.run(TOKEN)


if __name__ == "__main__":
    main()
