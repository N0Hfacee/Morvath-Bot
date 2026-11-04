import random
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils.helpers import resolve_guild
from utils.stats import flush_voice_sessions, load_voice_stats, save_voice_stats

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


class Blackjack(commands.Cog):
    """Cog kasino Blackjack 21 kerajaan."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(
        name="blackjack",
        aliases=["bj"],
        description="Mainkan kartu Blackjack 21 melawan Morvath di kasino bawah tanah benteng.",
    )
    @app_commands.describe(taruhan="Menit waktu VC yang dipertaruhkan (opsional, maks 60m. Kosongkan untuk mode latihan)")
    async def blackjack_cmd(self, ctx: commands.Context, taruhan: Optional[int] = 0):
        guild = resolve_guild(self.bot, ctx)
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


async def setup(bot: commands.Bot):
    await bot.add_cog(Blackjack(bot))
