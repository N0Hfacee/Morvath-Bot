import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger("VoiceSentinel")
CONFIG_FILE = Path("config.json")


def load_config() -> dict:
    """Membaca konfigurasi tersimpan dari config.json."""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Gagal membaca config.json: {e}")
    return {}


def save_config(data: dict):
    """Menyimpan konfigurasi ke config.json agar tidak hilang saat bot restart."""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        logger.error(f"Gagal menyimpan config.json: {e}")


def get_target_channel_id() -> Optional[int]:
    """Mengambil target channel ID dari config.json atau fallback ke .env."""
    cfg = load_config()
    if "target_channel_id" in cfg:
        val = cfg["target_channel_id"]
        return int(val) if val else None

    env_target = os.getenv("VOICE_CHANNEL_ID")
    if env_target and env_target.isdigit():
        return int(env_target)
    return None


def set_target_channel_id(channel_id: Optional[int]):
    """Menyimpan target channel ID baru ke config.json."""
    cfg = load_config()
    cfg["target_channel_id"] = channel_id
    save_config(cfg)
