import asyncio
from pathlib import Path
import sys

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from config import (
    AI_API_KEY,
    AI_BASE_URL,
    DATABASE_PROVIDER,
    get_session_file_path,
)
from database import get_database


async def check_health() -> int:
    # 1. Check Session File
    session_file = get_session_file_path()
    if not session_file.exists() or session_file.stat().st_size == 0:
        sys.stderr.write(f"Healthcheck FAIL: Session file missing or empty at {session_file}\n")
        return 1

    # 2. Check Database
    try:
        db = get_database()
        await db.init_db()
        await db.get_setting("away_mode", "auto")
        await db.close()
    except Exception as e:
        sys.stderr.write(f"Healthcheck FAIL: Database ({DATABASE_PROVIDER}) error: {e}\n")
        return 1

    # 3. Check AI Provider Connectivity
    if AI_API_KEY:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.get(
                    f"{AI_BASE_URL}/models",
                    headers={"Authorization": f"Bearer {AI_API_KEY}"},
                )
                if res.status_code not in (200, 401, 403):
                    sys.stderr.write(f"Healthcheck FAIL: AI API returned {res.status_code}\n")
                    return 1
        except Exception as e:
            sys.stderr.write(f"Healthcheck FAIL: AI connection failed: {e}\n")
            return 1

    sys.stdout.write("Healthcheck OK: All components operational\n")
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(check_health())
    sys.exit(exit_code)
