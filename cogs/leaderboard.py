from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils.helpers import resolve_guild
from utils.stats import (
    active_voice_sessions,
    flush_voice_sessions,
    format_duration,
    get_knight_title,
    load_voice_stats,
)


class Leaderboard(commands.Cog):
    """Cog papan peringkat waktu pengabdian Voice Channel kerajaan."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(
        name="vctop",
        description="Lihat klasemen dewan ksatria dengan waktu pengabdian VC tertinggi.",
    )
    async def vctop_cmd(self, ctx: commands.Context):
        """Menampilkan 10 ksatria dengan waktu Voice Channel terbanyak."""
        guild = resolve_guild(self.bot, ctx)
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

    @commands.hybrid_command(
        name="vctime",
        description="Cek catatan waktu pengabdian dan gelar ksatria.",
    )
    @app_commands.describe(user="Pilih anggota yang ingin dicek (kosongkan untuk melihat dirimu)")
    async def vctime_cmd(self, ctx: commands.Context, user: Optional[discord.Member] = None):
        """Melihat statistik VC personal seorang ksatria."""
        guild = resolve_guild(self.bot, ctx)
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
            current_ch = (
                target_user.voice.channel.name
                if target_user.voice and target_user.voice.channel
                else "Aula Kerajaan"
            )
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


async def setup(bot: commands.Bot):
    await bot.add_cog(Leaderboard(bot))
