import datetime
import logging
import time
from typing import Optional

from activity import ActivityDetector, ActivityState, AwayMode
from database import Database

logger = logging.getLogger(__name__)


def format_elapsed_time(timestamp: Optional[float]) -> str:
    if timestamp is None:
        return "Chưa ghi nhận"
    elapsed = int(time.time() - timestamp)
    if elapsed < 60:
        return f"{elapsed} giây trước"
    if elapsed < 3600:
        return f"{elapsed // 60} phút trước"
    return f"{elapsed // 3600} giờ trước"


class CommandHandler:
    def __init__(self, my_id: int):
        self.my_id = my_id

    async def handle_command(
        self,
        command_text: str,
        sender_id: int,
        detector: ActivityDetector,
        db: Database,
    ) -> Optional[str]:
        """
        Processes Saved Messages control commands.
        Strictly authenticated: only sender_id == my_id is allowed.
        """
        # Strict user ID authentication
        if sender_id != self.my_id:
            logger.warning(
                "Unauthorized command attempt from sender_id=%s (expected %s)",
                sender_id,
                self.my_id,
            )
            return None

        cmd = command_text.strip().lower()

        if cmd == "/away on":
            await detector.set_mode(AwayMode.MANUAL_ON, db)
            return (
                "✅ **Chế độ Vắng mặt đã BẬT (MANUAL_ON)**\n\n"
                "• Trạng thái hiện tại: `AWAY`\n"
                "• AI sẽ tự động hỗ trợ trả lời tin nhắn riêng."
            )

        if cmd == "/away off":
            await detector.set_mode(AwayMode.MANUAL_OFF, db)
            return (
                "🛑 **Chế độ Vắng mặt đã TẮT (MANUAL_OFF)**\n\n"
                "• Trạng thái hiện tại: `ACTIVE`\n"
                "• AI đã ngừng trả lời hoàn toàn."
            )

        if cmd == "/away auto":
            await detector.set_mode(AwayMode.AUTO, db)
            return (
                "🔄 **Chế độ Vắng mặt TỰ ĐỘNG (AUTO)**\n\n"
                f"• Grace period: `{detector.grace_period} giây`\n"
                "• AI sẽ tự động kích hoạt sau 120s khi bạn ngừng tương tác và tự dừng khi bạn quay lại."
            )

        if cmd == "/away status":
            state = detector.get_state()
            elapsed_str = format_elapsed_time(detector.last_activity_time)
            pending_count = len(detector._pending_tasks)

            state_desc = {
                ActivityState.ACTIVE: "🟢 Đang hoạt động (ACTIVE - AI không trả lời)",
                ActivityState.AWAY: "🟡 Vắng mặt (AWAY - AI sẵn sàng trả lời)",
                ActivityState.UNKNOWN: "⚪ Chưa xác định (UNKNOWN - Fail-safe, AI không trả lời)",
            }.get(state, state.value)

            return (
                "📊 **Trạng thái Hệ thống Auto Reply**\n\n"
                f"• Chế độ cấu hình: `{detector.mode.value}`\n"
                f"• Trạng thái hiện tại: {state_desc}\n"
                f"• Tín hiệu gần nhất: `{detector.last_activity_type}` ({elapsed_str})\n"
                f"• Grace period: `{detector.grace_period}s`\n"
                f"• Tin nhắn đang đếm ngược chờ trả lời: `{pending_count}`"
            )

        if cmd == "/away stats":
            stats = await db.get_stats()
            return (
                "📈 **Thống kê AI Auto Chat**\n\n"
                f"• Tin nhắn riêng đã nhận: `{stats['total_incoming']}`\n"
                f"• Phản hồi AI đã gửi: `{stats['total_ai_replies']}`\n"
                f"• Prompt tokens: `{stats['prompt_tokens']}`\n"
                f"• Completion tokens: `{stats['completion_tokens']}`\n"
                f"• Lỗi API: `{stats['api_errors']}`\n"
                f"• Hội thoại đang tạm dừng (chủ tài khoản vừa nhắn): `{stats['paused_chats']}`"
            )

        if cmd.startswith("/away"):
            return (
                "ℹ️ **Danh sách lệnh điều khiển:**\n\n"
                "• `/away auto` - Chế độ tự động phát hiện vắng mặt\n"
                "• `/away on` - Bật vắng mặt thủ công\n"
                "• `/away off` - Tắt vắng mặt thủ công\n"
                "• `/away status` - Xem trạng thái chi tiết\n"
                "• `/away stats` - Xem thống kê hoạt động"
            )

        return None
