import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))
LOGS_DIR = Path(os.getenv("LOGS_DIR", str(BASE_DIR / "logs")))

# Database Provider Configuration
DATABASE_PROVIDER = os.getenv("DATABASE_PROVIDER", "sqlite").lower().strip()
SQLITE_DB_PATH = Path(os.getenv("SQLITE_DB_PATH", str(DATA_DIR / "auto_reply.db")))
TURSO_DATABASE_URL = os.getenv("TURSO_DATABASE_URL", "").strip()
TURSO_AUTH_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "").strip()
# Legacy alias for backward compatibility
DB_PATH = SQLITE_DB_PATH

# Telegram credentials
TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

# Session path resolution (supports VPS root file and Northflank volume /app/data/telegram_auto_reply)
_raw_session_path = os.getenv("TELEGRAM_SESSION_PATH", "").strip()
if _raw_session_path:
    if _raw_session_path.endswith(".session"):
        _raw_session_path = _raw_session_path[:-8]
    TELEGRAM_SESSION_PATH = _raw_session_path
else:
    TELEGRAM_SESSION_PATH = str(BASE_DIR / "telegram_auto_reply")

TELEGRAM_SESSION_NAME = TELEGRAM_SESSION_PATH

# AI Provider Configuration (ToolNet OpenAI-compatible)
AI_BASE_URL = os.getenv("AI_BASE_URL", "https://api.toolnet.tech/v1").rstrip("/")
AI_API_KEY = os.getenv("AI_API_KEY", "").strip()
AI_MODEL = os.getenv("AI_MODEL", "groq/qwen/qwen3.8-27b").strip()
AI_TIMEOUT = float(os.getenv("AI_TIMEOUT", "30.0"))
AI_MAX_TOKENS = int(os.getenv("AI_MAX_TOKENS", "300"))
AI_TEMPERATURE = float(os.getenv("AI_TEMPERATURE", "0.7"))
AI_CONCURRENCY_LIMIT = int(os.getenv("AI_CONCURRENCY_LIMIT", "3"))
AI_MAX_RETRIES = int(os.getenv("AI_MAX_RETRIES", "3"))

# Activity Detection & Timing
ACTIVITY_GRACE_PERIOD = int(os.getenv("ACTIVITY_GRACE_PERIOD", "120"))
MANUAL_REPLY_PAUSE_SECONDS = int(os.getenv("MANUAL_REPLY_PAUSE_SECONDS", "1800"))
USER_COOLDOWN_SECONDS = int(os.getenv("USER_COOLDOWN_SECONDS", "600"))
MAX_REPLIES_PER_HOUR = int(os.getenv("MAX_REPLIES_PER_HOUR", "10"))
HISTORY_MESSAGE_LIMIT = int(os.getenv("HISTORY_MESSAGE_LIMIT", "20"))

DEFAULT_SYSTEM_PROMPT = (
    "Bạn là trợ lý AI hỗ trợ trả lời tin nhắn Telegram khi chủ tài khoản đang vắng mặt. "
    "Hãy trả lời tự nhiên, ngắn gọn, lịch sự bằng tiếng Việt. "
    "Không giả danh chủ tài khoản. "
    "Nếu người nhắn cần trao đổi trực tiếp, hãy cho biết chủ tài khoản sẽ phản hồi khi quay lại."
)
SYSTEM_PROMPT = os.getenv("AI_SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT).strip()


class ConfigError(Exception):
    """Raised when critical configuration is missing or invalid."""
    pass


def get_session_file_path() -> Path:
    return Path(f"{TELEGRAM_SESSION_PATH}.session")


def validate_session_file() -> None:
    session_file = get_session_file_path()
    if not session_file.exists() or session_file.stat().st_size == 0:
        raise ConfigError(
            f"Telegram session file not found or empty at '{session_file}'. "
            "To prevent unauthorized interactive prompts, an existing authorized session must be mounted."
        )


def validate_config(require_ai: bool = True, require_session: bool = False) -> None:
    """Validate critical environment variables without logging sensitive data."""
    if not TELEGRAM_API_ID or not TELEGRAM_API_HASH:
        raise ConfigError("Missing TELEGRAM_API_ID or TELEGRAM_API_HASH in configuration.")

    if require_ai and not AI_API_KEY:
        raise ConfigError("Missing AI_API_KEY in configuration. Please set AI_API_KEY in .env.")

    if DATABASE_PROVIDER == "turso":
        if not TURSO_DATABASE_URL:
            raise ConfigError("DATABASE_PROVIDER is 'turso' but TURSO_DATABASE_URL is not set.")
    else:
        # SQLite
        SQLITE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    if require_session:
        validate_session_file()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
