# Hướng Dẫn Di Chuyển Telegram AI Auto Reply từ VPS sang Northflank

Tài liệu này cung cấp quy trình chi tiết từng bước để chuyển đổi ứng dụng **Telegram AI Auto Reply** từ môi trường Ubuntu VPS (chạy bằng systemd) sang nền tảng cloud container **Northflank**, đồng thời kết nối cơ sở dữ liệu **Turso** và lưu trữ session bền vững qua **Persistent Volume**.

---

## Nguyên Tắc An Toàn Bắt Buộc

> [!CAUTION]
> **QUY TẮC MỘT WORKER DUY NHẤT:**
> Tuyệt đối **KHÔNG** chạy song song hai worker (VPS và Northflank) sử dụng chung một Telegram Session. Chạy đồng thời sẽ dẫn đến xung đột phiên MTProto và nguy cơ bị Telegram tạm khóa tài khoản.

---

## Bước 1: Sao Lưu Dữ Liệu Hiện Tại trên VPS

Trước khi thao tác, thực hiện sao lưu toàn bộ mã nguồn, cấu hình và cơ sở dữ liệu SQLite:

```bash
cd /root/telegram-auto-reply
./scripts/backup.sh
```

Bản sao lưu sẽ được lưu tại thư mục `/root/telegram-auto-reply/backups/latest`.

---

## Bước 2: Dừng Hoàn Toàn Worker trên VPS

Dừng và vô hiệu hóa service systemd để giải phóng Telegram session:

```bash
# 1. Dừng service
systemctl stop telegram-auto-reply.service

# 2. Vô hiệu hóa tự khởi động (để tránh tự bật lại khi reboot VPS)
systemctl disable telegram-auto-reply.service

# 3. Kiểm tra đảm bảo không còn process nào chạy ngầm
pgrep -af "app.py" || echo "Worker đã dừng hoàn toàn an toàn."
```

---

## Bước 3: Thiết Lập Cơ Sở Dữ Liệu Turso (Tùy Chọn)

Nếu bạn chọn sử dụng `DATABASE_PROVIDER=turso` thay vì SQLite trên volume:

1. **Cài đặt Turso CLI và đăng nhập:**
   ```bash
   curl -sSfL https://get.tur.so/install.sh | bash
   turso auth login
   ```

2. **Tạo database mới:**
   ```bash
   turso db create telegram-auto-reply
   ```

3. **Lấy Connection URL và Auth Token:**
   ```bash
   turso db show --url telegram-auto-reply
   # Kết quả có dạng: libsql://telegram-auto-reply-<org>.turso.io
   
   turso db tokens create telegram-auto-reply
   # Lưu lại Token bảo mật này
   ```

4. **Chuyển dữ liệu từ SQLite sang Turso (nếu muốn giữ lịch sử):**
   ```bash
   # Xuất schema và dữ liệu từ SQLite
   sqlite3 /root/telegram-auto-reply/data/auto_reply.db .dump > /tmp/dump.sql
   
   # Nạp vào Turso database
   turso db shell telegram-auto-reply < /tmp/dump.sql
   ```

> [!NOTE]
> Nếu bạn muốn giữ lại SQLite trên Northflank mà không dùng Turso, chỉ cần đặt `DATABASE_PROVIDER=sqlite` và dữ liệu sẽ được lưu trực tiếp vào Persistent Volume `/app/data/auto_reply.db`.

---

## Bước 4: Chuẩn Bị Persistent Volume trên Northflank

1. Truy cập trang quản trị [Northflank](https://app.northflank.com).
2. Vào Project của bạn -> chọn **Volumes** -> Tạo Volume mới:
   - **Volume Name:** `telegram-data-storage`
   - **Mount Path:** `/app/data`
   - **Storage Size:** 1 GB (loại SSD tiêu chuẩn).

---

## Bước 5: Chuyển File Telegram Session lên Persistent Volume

File session hiện tại: `/root/telegram-auto-reply/telegram_auto_reply.session`.

Bạn có thể đưa file session vào Persistent Volume theo một trong các phương thức an toàn sau:

### Cách A: Qua SFTP / File Browser của Northflank Volume
* Mở tab **Files** của volume `telegram-data-storage`.
* Tải tệp `telegram_auto_reply.session` lên thư mục gốc của volume (tương ứng `/app/data/telegram_auto_reply.session`).

### Cách B: Qua mã hóa Base64 một lần
* Trên VPS, mã hóa file session:
  ```bash
  base64 -w 0 /root/telegram-auto-reply/telegram_auto_reply.session > /tmp/session_b64.txt
  ```
* Trong container console của Northflank, giải mã vào volume:
  ```bash
  echo "<CHUỖI_BASE64>" | base64 -d > /app/data/telegram_auto_reply.session
  chmod 600 /app/data/telegram_auto_reply.session
  ```

---

## Bước 6: Tạo Service và Cấu Hình Biến Môi Trường trên Northflank

1. Tạo **Deployment Service**:
   - **Service Name:** `telegram-ai-auto-reply`
   - **Service Type:** Worker (Deployment Service, không cần public port).
   - **Source:** Kết nối với GitHub Repository của bạn.
   - **Build Type:** Dockerfile (chọn `Dockerfile` trong root).
   - **Replicas:** **Chính xác 1** (Tuyệt đối không tăng > 1).
   - **Restart Policy:** `Always`.

2. Gắn Persistent Volume:
   - Gắn volume `telegram-data-storage` vào đường dẫn `/app/data`.

3. Cấu hình Healthcheck:
   - **Type:** Exec / Command
   - **Command:** `python scripts/healthcheck.py`
   - **Interval:** 30s | **Timeout:** 5s | **Retries:** 3.

4. Khai báo Environment Variables & Secrets:

| Tên Biến | Giá Trị Mẫu | Mô Tả |
| :--- | :--- | :--- |
| `TELEGRAM_API_ID` | `<your_telegram_api_id>` | API ID từ my.telegram.org |
| `TELEGRAM_API_HASH` | `<your_telegram_api_hash>` | API Hash |
| `TELEGRAM_SESSION_PATH` | `/app/data/telegram_auto_reply` | Đường dẫn session trong volume |
| `DATABASE_PROVIDER` | `turso` *(hoặc `sqlite`)* | Loại cơ sở dữ liệu |
| `TURSO_DATABASE_URL` | `https://telegram-auto-reply-<org>.turso.io` | *(Nếu dùng Turso)* |
| `TURSO_AUTH_TOKEN` | `<turso_token>` | *(Nếu dùng Turso)* |
| `SQLITE_DB_PATH` | `/app/data/auto_reply.db` | *(Nếu dùng SQLite)* |
| `AI_BASE_URL` | `https://api.toolnet.tech/v1` | ToolNet API Endpoint |
| `AI_API_KEY` | `sk-...` | ToolNet API Key |
| `AI_MODEL` | `groq/qwen/qwen3.8-27b` | Model OpenAI-compatible |
| `AI_TIMEOUT` | `30` | Timeout gọi AI (giây) |
| `ACTIVITY_GRACE_PERIOD`| `120` | Thời gian đếm ngược (giây) |
| `USER_COOLDOWN_SECONDS`| `600` | Cooldown mỗi người dùng |
| `MAX_REPLIES_PER_HOUR` | `10` | Giới hạn phản hồi mỗi giờ |
| `MANUAL_REPLY_PAUSE_SECONDS` | `1800` | Tạm dừng khi chủ tài khoản nhắn |

---

## Bước 7: Khởi Chạy và Kiểm Tra Hoạt Động (Verification)

1. Nhấn **Deploy** trên Northflank.
2. Kiểm tra log container trong tab **Logs**:
   - Xác nhận có dòng log:
     ```
     Telegram Auto Reply connected as: MEDIA LÊ TẤN (ID: 1208342332)
     Application running. Mode: auto, Current State: UNKNOWN
     ```
3. Mở Telegram trên điện thoại của bạn -> vào **Saved Messages**:
   - Gửi: `/away status`
   - Nhận phản hồi trạng thái từ bot.
   - Gửi: `/away stats`
   - Xác nhận hệ thống phản hồi chính xác.

---

## Bước 8: Quy Trình Rollback Khẩn Cấp về VPS

Nếu cần quay trở lại VPS bất cứ lúc nào:

1. **Dừng container trên Northflank:**
   - Vào Northflank -> chọn service `telegram-ai-auto-reply` -> nhấn **Pause** hoặc đặt **Replicas = 0**.
2. **Khởi động lại trên VPS:**
   ```bash
   systemctl enable telegram-auto-reply.service
   systemctl start telegram-auto-reply.service
   /root/telegram-auto-reply/scripts/healthcheck.sh
   ```
3. **Kiểm tra trạng thái VPS:**
   ```bash
   journalctl -u telegram-auto-reply.service -n 10 --no-pager
   ```
