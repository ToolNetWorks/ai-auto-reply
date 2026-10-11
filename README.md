# Telegram AI Auto Reply (Production-Ready)

Hệ thống AI Auto Chat hỗ trợ trả lời tự động tin nhắn riêng tư Telegram khi chủ tài khoản vắng mặt, tích hợp mô hình ngôn ngữ lớn (OpenAI-compatible) qua ToolNet API, chạy 24/7 trên VPS Ubuntu (systemd) hoặc Docker / Northflank.

---

## 🌟 Tính Năng Nổi Bật

* **Activity Detection Engine (Phát hiện vắng mặt an toàn):**
  - Tự động nhận diện hoạt động gửi tin nhắn hoặc đọc tin nhắn từ điện thoại iPhone/Desktop của chính bạn.
  - Grace Period 120 giây: Khi có tin nhắn đến, chờ 120 giây. Nếu bạn không đọc và không nhắn -> AI mới phản hồi.
  - Tự động hủy lệnh trả lời ngay lập tức nếu bạn quay lại hoạt động hoặc đọc tin nhắn.
  - Tạm ngừng AI trong 30 phút đối với người mà bạn vừa chủ động trả lời thủ công.
  - Fail-safe: Không bao giờ tự động trả lời khi trạng thái hoạt động chưa rõ ràng (`UNKNOWN`).
* **ToolNet AI Integration:**
  - Hỗ trợ endpoint OpenAI-compatible (`https://api.toolnet.tech/v1`).
  - Async HTTP client, cơ chế retry với exponential backoff & jitter, xử lý 429 / 5xx, giới hạn concurrency.
  - Hiệu ứng gõ phím (`typing...`) trước khi phản hồi.
* **AI Conversation Engine:**
  - Ghi nhớ lịch sử hội thoại lên đến 20 tin nhắn gần nhất riêng cho từng người dùng.
  - Trả lời tự nhiên, ngắn gọn, lịch sự bằng tiếng Việt.
  - Phòng chống prompt injection: bảo vệ system prompt và tuyệt đối không giả danh chủ tài khoản.
* **Database Abstraction Layer:**
  - Hỗ trợ SQLite (WAL mode, busy timeout 5000ms) cho môi trường VPS / Local.
  - Hỗ trợ Turso (libSQL Cloud Database) cho môi trường Cloud Container (Northflank).
* **Điều khiển qua Saved Messages:**
  - `/away auto` - Chế độ tự động phát hiện vắng mặt.
  - `/away on` - Bật vắng mặt thủ công.
  - `/away off` - Tắt vắng mặt thủ công (AI không trả lời).
  - `/away status` - Xem trạng thái chi tiết, thời gian tương tác gần nhất.
  - `/away stats` - Thống kê tin nhắn, token và số cuộc trò chuyện tạm dừng.
  - Xác thực nghiêm ngặt bằng chính Telegram User ID của bạn.

---

## 📁 Cấu Trúc Dự Án

```
├── app.py                      # Main application với Telethon event loop
├── config.py                   # Quản lý cấu hình & validate môi trường
├── activity.py                 # Activity Detection Engine (ACTIVE, AWAY, UNKNOWN)
├── ai_client.py                # Async client gọi OpenAI-compatible API
├── conversation.py             # Conversation Engine với ngữ cảnh 20 tin nhắn
├── database.py                 # SQLite Repository & Turso Repository
├── commands.py                 # Bộ xử lý lệnh điều khiển qua Saved Messages
├── safety.py                   # Anti-spam, rate limiting, per-chat locks
├── requirements.txt            # Danh sách dependencies đã pin phiên bản
├── Dockerfile                  # Multi-stage non-root container image
├── docker-compose.yml          # Triển khai container local/VPS
├── northflank.json             # Khai báo dịch vụ cho Northflank Worker
├── .github/workflows/ci.yml    # CI kiểm thử cú pháp, unit tests & build Docker
├── docs/
│   └── MIGRATE_TO_NORTHFLANK.md # Hướng dẫn di chuyển từ VPS sang Northflank
├── scripts/
│   ├── install.sh              # Cài đặt venv & kích hoạt systemd
│   ├── healthcheck.sh          # Kiểm tra service, db, process trên VPS
│   ├── healthcheck.py          # Healthcheck độc lập cho Docker / Northflank
│   ├── backup.sh               # Sao lưu an toàn mã nguồn và database
│   └── rollback.sh             # Khôi phục phiên bản cũ khi cần
└── tests/                      # Bộ kiểm thử 16 unit & integration tests
```

---

## 🚀 Hướng Dẫn Cài Đặt & Vận Hành

### 1. Chạy trên VPS Ubuntu (systemd)

1. Clone mã nguồn và cấu hình file `.env`:
   ```bash
   cp .env.example .env
   chmod 600 .env
   # Chỉnh sửa TELEGRAM_API_ID, TELEGRAM_API_HASH, AI_API_KEY trong .env
   ```
2. Đưa file session vào thư mục gốc: `telegram_auto_reply.session` (`chmod 600`).
3. Chạy cài đặt và kích hoạt dịch vụ:
   ```bash
   chmod +x scripts/*.sh
   ./scripts/install.sh
   systemctl start telegram-auto-reply.service
   ```
4. Kiểm tra trạng thái hoạt động:
   ```bash
   ./scripts/healthcheck.sh
   journalctl -u telegram-auto-reply.service -f
   ```

### 2. Chạy với Docker / docker-compose

```bash
docker compose up -d --build
docker compose logs -f
```

### 3. Triển khai lên Northflank

Xem chi tiết từng bước tại [docs/MIGRATE_TO_NORTHFLANK.md](docs/MIGRATE_TO_NORTHFLANK.md).

---

## 🧪 Kiểm Thử (Testing)

Chạy bộ kiểm thử 16 bài test độc lập:

```bash
pytest -v tests
```

---

## 🔒 Bảo Mật (Security)

* Tuyệt đối không commit file `.env`, file `*.session` hoặc file cơ sở dữ liệu `*.db` lên Git.
* Luôn thiết lập quyền truy cập `chmod 600` cho `.env`, `.session` và file SQLite trên VPS.
* Đảm bảo chỉ có một instance hoạt động trên một Telegram session tại một thời điểm.
