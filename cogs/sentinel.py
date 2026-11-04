import asyncio
import logging
import time
from typing import Optional, Union

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils.config import get_target_channel_id, set_target_channel_id
from utils.helpers import get_author_voice_channel, resolve_guild
from utils.stats import (
    active_voice_sessions,
    flush_voice_sessions,
    load_voice_stats,
    save_voice_stats,
)

logger = logging.getLogger("VoiceSentinel")

_last_voice_attempt = 0.0
VOICE_ATTEMPT_COOLDOWN = 8.0


class Sentinel(commands.Cog):
    """Cog penjaga Voice Channel 24/7 dan status kerajaan Morvath."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        # Mulai loop watchdog ketika Cog dimuat
        if not self.voice_watchdog.is_running():
            self.voice_watchdog.start()

    async def cog_unload(self):
        if self.voice_watchdog.is_running():
            self.voice_watchdog.cancel()

    async def update_morvath_presence(self, channel_name: Optional[str] = None):
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
            await self.bot.change_presence(activity=activity)
        except Exception as e:
            logger.debug(f"Gagal memperbarui presence: {e}")

    async def ensure_voice_connection(self):
        """Memastikan bot terhubung ke voice channel target."""
        global _last_voice_attempt
        now = asyncio.get_running_loop().time()
        if now - _last_voice_attempt < VOICE_ATTEMPT_COOLDOWN:
            return
        _last_voice_attempt = now

        target_id = get_target_channel_id()
        if not target_id:
            await self.update_morvath_presence(None)
            return

        channel = self.bot.get_channel(target_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(target_id)
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
                await self.update_morvath_presence(channel.name)
            elif not voice_client.is_connected():
                logger.warning("Voice client tidak terhubung, mencoba reconnect...")
                await voice_client.disconnect(force=True)
                await asyncio.sleep(1)
                await channel.connect(self_deaf=True, reconnect=True)
                logger.info(f"✅ Berhasil reconnect ke: {channel.name}")
                await self.update_morvath_presence(channel.name)
            elif voice_client.channel.id != target_id:
                logger.info(f"Memindahkan bot dari {voice_client.channel.name} ke target: {channel.name}...")
                await voice_client.move_to(channel)
                await self.update_morvath_presence(channel.name)
            else:
                await self.update_morvath_presence(channel.name)
        except Exception as e:
            logger.error(f"Error saat memastikan koneksi voice: {e}")

    @tasks.loop(seconds=20)
    async def voice_watchdog(self):
        """Watchdog loop yang terus memantau status voice bot dan menyinkronkan data VC."""
        if self.bot.is_ready():
            flush_voice_sessions()
            await self.ensure_voice_connection()

    @commands.Cog.listener()
    async def on_ready(self):
        logger.info(f"Bot berhasil login sebagai: {self.bot.user.name} ({self.bot.user.id})")
        logger.info(f"Jumlah server terhubung: {len(self.bot.guilds)}")
        for g in self.bot.guilds:
            logger.info(f" - Server: '{g.name}' (ID: {g.id}) | {len(g.voice_channels)} Voice Channels")

        # Sync slash commands ke Discord
        try:
            synced = await self.bot.tree.sync()
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
        for g in self.bot.guilds:
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

        if not self.voice_watchdog.is_running():
            self.voice_watchdog.start()

        await self.ensure_voice_connection()

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """Mendeteksi perpindahan voice dan mencatat waktu pengabdian ksatria."""
        # 1. Khusus penjaga bot (Morvath)
        if member.id == self.bot.user.id:
            target_id = get_target_channel_id()
            if not target_id:
                return  # Bot memang sedang disuruh leave / nonaktif

            # Jika bot terputus dari voice secara tidak disengaja
            if after.channel is None:
                logger.warning("Bot terputus dari voice channel! Mencoba masuk kembali dalam 3 detik...")
                await asyncio.sleep(3)
                await self.ensure_voice_connection()
            # Jika bot dipindah ke channel lain
            elif after.channel.id != target_id:
                logger.warning(f"Bot dipindahkan ke {after.channel.name}. Mengembalikan ke target ({target_id})...")
                await asyncio.sleep(2)
                await self.ensure_voice_connection()
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

    @commands.hybrid_command(name="join", description="Tempatkan Morvath untuk menjaga Voice Channel 24/7.")
    @app_commands.describe(target="Pilih aula kerajaan yang ingin dijaga (kosongkan jika kamu sudah di dalam VC)")
    async def join_cmd(
        self,
        ctx: commands.Context,
        *,
        target: Optional[Union[discord.VoiceChannel, discord.StageChannel]] = None,
    ):
        """
        Jika kamu sedang di dalam VC, cukup ketik: /join
        Atau pilih channel: /join target: [Pilih Channel]
        """
        guild = resolve_guild(self.bot, ctx)
        if not guild:
            if len(self.bot.guilds) == 0:
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
        await self.ensure_voice_connection()

    @commands.hybrid_command(name="leave", description="Bebastugaskan Morvath dari pos penjagaan.")
    async def leave_cmd(self, ctx: commands.Context):
        """Menghentikan penjagaan 24/7 dan bot keluar dari VC."""
        guild = resolve_guild(self.bot, ctx)
        if not guild:
            await ctx.send("❌ Titah ini hanya dapat diserukan di dalam server Discord!", ephemeral=True)
            return

        set_target_channel_id(None)
        await self.update_morvath_presence(None)

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

    @commands.hybrid_command(name="status", description="Laporan inspeksi pos penjagaan benteng.")
    async def status_cmd(self, ctx: commands.Context):
        """Melihat apakah bot sedang aktif menjaga VC."""
        target_id = get_target_channel_id()
        guild = resolve_guild(self.bot, ctx)
        vc = guild.voice_client if guild else None
        latency = round(self.bot.latency * 1000)

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

    @commands.hybrid_command(name="ping", description="Uji kesiapan dan ketajaman refleks Morvath.")
    async def ping_cmd(self, ctx: commands.Context):
        latency = round(self.bot.latency * 1000)
        await ctx.send(f"⚔️ *Menghunus pedang dalam {latency}ms!* Morvath selalu siap sedia menerima titah, Tuanku.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Sentinel(bot))
