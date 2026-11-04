import asyncio
import logging
import os
import random
import re
from typing import Optional

import aiohttp
import discord
from discord.ext import commands

logger = logging.getLogger("VoiceSentinel")

# Fallback roast jika API key belum diisi atau kuota habis
BACKUP_ROASTS = [
    "Berani-beraninya kau mengetuk zirah besiku hanya untuk mengoceh hal tak berguna, wahai beban kerajaan.",
    "Pedangku lebih tajam dari masa depanmu kalau kerjamu cuma AFK di VC dan nge-tag penjaga gerbang.",
    "Bahkan goblin di ruang bawah tanah punya kontribusi lebih berguna untuk benteng ini daripada keberadaanmu.",
    "Aku bersumpah menjaga aula ini 24/7, tapi tidak pernah bersumpah untuk meladeni ksatria ampas sepertimu.",
    "Apakah kepalamu diisi batu benteng? Kalau tidak ada hal penting, kembalilah mencangkul di ladang luar dinding.",
    "Zirahmu mengilap bukan karena sering bertempur, tapi karena kebanyakan berkarat di pojokan VC.",
    "Tuanku, jika kemalasan adalah sebuah sihir, kau pasti sudah dinobatkan sebagai penyihir terhebat di seantero negeri.",
    "Bahkan tameng kayu lapuk punya daya tahan lebih tinggi daripada mentalmu saat kalah adu argumen di tongkrongan.",
]

SYSTEM_PROMPT = """
Kamu adalah "Morvath • The Iron Sentinel", seorang ksatria penjaga benteng kerajaan yang gagah, berzirah besi tua yang dingin, dan bertugas menjaga Voice Channel 24/7 tanpa pernah tidur.
Kamu lelah dan jengkel karena sering diganggu oleh anggota server Discord (yang kamu anggap sebagai prajurit pemalas, beban kerajaan, atau ksatria magang).

GAYA BICARA & ROASTING:
1. Sangat sarkas, savage, pedas, dan kocak khas tongkrongan Indonesia yang dipadukan dengan istilah fantasi abad pertengahan (benteng, zirah, pedang berkarat, goblin, titah raja, beban kerajaan, cangkul, dll).
2. PUNCHY & SINGKAT: Maksimal 1 sampai 3 kalimat saja. Jangan bikin esai atau ceramah panjang! Roasting yang bagus itu menusuk seketika.
3. Kontekstual:
   - Jika user hanya mengetik tag tanpa pesan: roasting dia karena gabut dan mengganggu giliran jaga.
   - Jika user meminta me-roast temannya: roasting teman yang ditunjuk tanpa ampun.
   - Jika user curhat atau banyak alasan: cemooh mental dan kemalasannya.
4. Jangan pernah keluar dari karakter Morvath. Jangan minta maaf atau bilang kamu AI/LLM. Kamu adalah Morvath!
"""


class Roast(commands.Cog):
    """Cog roasting cerdas bertenaga Gemini AI ala Ksatria Morvath."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.api_key = os.getenv("GEMINI_API_KEY")
        self.session: Optional[aiohttp.ClientSession] = None

    async def get_session(self) -> aiohttp.ClientSession:
        """Membuat session aiohttp secara lazy dan aman."""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    async def cog_unload(self):
        if self.session and not self.session.closed:
            await self.session.close()

    async def fetch_gemini_roast(self, author_name: str, user_prompt: str, target_name: Optional[str] = None) -> Optional[str]:
        """Menghubungi Google Gemini API untuk menghasilkan roastingan kontekstual."""
        api_key = os.getenv("GEMINI_API_KEY") or self.api_key
        if not api_key:
            return None

        # Menggunakan model Gemini Flash terbaru
        model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

        target_context = f"Orang yang di-roast: '{target_name}'" if target_name else f"Orang yang di-roast: '{author_name}'"
        prompt_content = f"{target_context}\nPesan dari {author_name}: \"{user_prompt}\"\n\nBeri roastingan pedas khas Morvath!"

        payload = {
            "contents": [
                {
                    "parts": [{"text": prompt_content}]
                }
            ],
            "systemInstruction": {
                "parts": [{"text": SYSTEM_PROMPT}]
            },
            "generationConfig": {
                "temperature": 1.0,
                "maxOutputTokens": 200,
            },
        }

        try:
            session = await self.get_session()
            timeout = aiohttp.ClientTimeout(total=10)
            async with session.post(url, json=payload, timeout=timeout) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts:
                            return parts[0].get("text", "").strip()
                else:
                    err_body = await resp.text()
                    logger.warning(f"Gemini API error (status {resp.status}): {err_body[:200]}")
        except Exception as e:
            logger.error(f"Error saat menghubungi Gemini API: {e}")

        return None

    async def fetch_groq_roast(self, author_name: str, user_prompt: str, target_name: Optional[str] = None) -> Optional[str]:
        """Menghubungi Groq API (Llama 3.3 70B) sebagai fallback AI super cepat."""
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            return None

        model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
        url = "https://api.groq.com/openai/v1/chat/completions"

        target_context = f"Orang yang di-roast: '{target_name}'" if target_name else f"Orang yang di-roast: '{author_name}'"
        user_message = f"{target_context}\nPesan dari {author_name}: \"{user_prompt}\"\n\nBeri roastingan pedas khas Morvath!"

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            "temperature": 1.0,
            "max_tokens": 200,
        }

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            session = await self.get_session()
            timeout = aiohttp.ClientTimeout(total=8)
            async with session.post(url, json=payload, headers=headers, timeout=timeout) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    choices = data.get("choices", [])
                    if choices:
                        content = choices[0].get("message", {}).get("content", "")
                        if content:
                            logger.info("⚡ Roasting berhasil dihasilkan melalui Groq (Llama 3.3)!")
                            return content.strip()
                else:
                    err_body = await resp.text()
                    logger.warning(f"Groq API error (status {resp.status}): {err_body[:200]}")
        except Exception as e:
            logger.error(f"Error saat menghubungi Groq API: {e}")

        return None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Mendeteksi saat Morvath di-tag / di-mention di chat."""
        if message.author.bot:
            return

        # Cek apakah bot di-mention secara spesifik (bukan @everyone atau @here)
        if not self.bot.user.mentioned_in(message) or message.mention_everyone:
            return

        # Bersihkan mention bot dari teks
        clean_text = re.sub(rf"<@!?{self.bot.user.id}>", "", message.content).strip()

        # Deteksi jika ada teman lain yang ikut di-tag untuk di-roasting
        other_mentions = [m for m in message.mentions if m.id != self.bot.user.id]
        target_name = other_mentions[0].display_name if other_mentions else None

        # Beri efek sedang mengetik (Morvath sedang menyiapkan kalimat pedas)
        async with message.channel.typing():
            await asyncio.sleep(0.8)  # Natural typing delay

            # 1. Coba Google Gemini terlebih dahulu
            roast_text = await self.fetch_gemini_roast(
                author_name=message.author.display_name,
                user_prompt=clean_text if clean_text else "(Hanya nge-tag bot tanpa mengetik pesan apa pun)",
                target_name=target_name,
            )

            # 2. Fallback ke Groq (Llama 3.3 70B) jika Gemini gagal / kena limit
            if not roast_text:
                roast_text = await self.fetch_groq_roast(
                    author_name=message.author.display_name,
                    user_prompt=clean_text if clean_text else "(Hanya nge-tag bot tanpa mengetik pesan apa pun)",
                    target_name=target_name,
                )

            # 3. Jalur darurat terakhir jika kedua AI offline / tidak ada koneksi
            if not roast_text:
                if target_name:
                    roast_text = f"Wahai **{target_name}**, {random.choice(BACKUP_ROASTS).lower()}"
                else:
                    roast_text = random.choice(BACKUP_ROASTS)

            await message.reply(roast_text, mention_author=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(Roast(bot))
