import json
import logging
import time
from pathlib import Path

logger = logging.getLogger("VoiceSentinel")
VOICE_STATS_FILE = Path("voice_stats.json")

# Sesi aktif in-memory: {user_id: {"start": float, "guild_id": int, "name": str}}
active_voice_sessions: dict[int, dict] = {}


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
