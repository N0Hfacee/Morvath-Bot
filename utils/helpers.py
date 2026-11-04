from typing import Optional, Union
import discord
from discord.ext import commands


def resolve_guild(bot: commands.Bot, ctx: commands.Context) -> Optional[discord.Guild]:
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


def get_author_voice_channel(
    ctx: commands.Context, guild: discord.Guild
) -> Optional[Union[discord.VoiceChannel, discord.StageChannel]]:
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


def find_voice_channel(
    guild: discord.Guild, query: str
) -> Optional[Union[discord.VoiceChannel, discord.StageChannel]]:
    """Mencari voice channel berdasarkan ID, mention, atau nama (case-insensitive)."""
    if not guild:
        return None
    clean = query.strip("<#> ").strip()
    if clean.isdigit():
        ch = guild.get_channel(int(clean))
        if isinstance(ch, (discord.VoiceChannel, discord.StageChannel)):
            return ch

    query_lower = query.strip().lower()
    for vc in guild.voice_channels + guild.stage_channels:
        if vc.name.lower() == query_lower:
            return vc
    for vc in guild.voice_channels + guild.stage_channels:
        if query_lower in vc.name.lower():
            return vc
    return None
