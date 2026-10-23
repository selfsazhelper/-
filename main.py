"""
یوزربات تلگرام + ربات کنترل

- یوزربات: با اکانت خودت وارد می‌شود و وقتی در گپ مشخص‌شده کلمه‌ی مورد نظر آمد،
  دکمه‌ی انتخابی همان پیام را می‌زند.
- ربات کنترل (با توکن BotFather): فقط مالک (OWNER_ID) می‌تواند از داخل تلگرام
  قوانین را اضافه/حذف/روشن/خاموش کند.
"""
import asyncio
import json
import logging
import os
import random
import re
from pathlib import Path

from telethon import Button, TelegramClient, events, utils
from telethon.sessions import StringSession

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("app")


def need(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"متغیر محیطی {name} تنظیم نشده است.")
    return value


API_ID = int(need("API_ID"))
API_HASH = need("API_HASH")
SESSION_STRING = need("SESSION_STRING")
BOT_TOKEN = need("BOT_TOKEN")
OWNER_ID = int(need("OWNER_ID"))
DEFAULT_DELAY = float(os.environ.get("DEFAULT_DELAY", "1"))

DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = DATA_DIR / "state.json"

FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


# ───────────────────────── ذخیره‌ی قوانین ─────────────────────────
class Store:
    def __init__(self, path: Path):
        self.path = path
        self.data = {"enabled": True, "next_id": 1, "rules": []}
        if path.exists():
            try:
                self.data.update(json.loads(path.read_text("utf-8")))
            except Exception:
                log.exception("خواندن state.json ناموفق بود؛ با حالت خالی شروع می‌شود")

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), "utf-8")
        tmp.replace(self.path)


store = Store(STATE_FILE)

user = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
bot = TelegramClient(str(DATA_DIR / "bot_session"), API_ID, API_HASH)


# ───────────────────────── توابع کمکی ─────────────────────────
def parse_chat(raw: str):
    """یوزرنیم، آیدی عددی یا لینک t.me را به مقداری تبدیل می‌کند که get_entity بفهمد."""
    s = raw.strip().translate(FA_DIGITS)
    m = re.match(r"^(?:https?://)?(?:t|telegram)\.me/c/(\d+)", s, re.I)
    if m:
        return int("-100" + m.group(1))
    m = re.match(r"^(?:https?://)?(?:t|telegram)\.me/([A-Za-z0-9_]{4,})", s, re.I)
    if m and m.group(1).lower() != "joinchat":
        return m.group(1)
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    m = re.fullmatch(r"@?([A-Za-z][A-Za-z0-9_]{3,31})", s)
    if m:
        return m.group(1)
    return None


def parse_button(raw: str) -> dict:
    s = raw.strip().translate(FA_DIGITS)
    m = re.fullmatch(r"#\s*(\d+)", s)
    if m:
        n = int(m.group(1))
        if n < 1:
            raise ValueError("شماره‌ی دکمه باید از ۱ شروع شود.")
        return {"button_number": n}
    if not s:
        raise ValueError("متن دکمه خالی است.")
    return {"button_text": raw.strip()}


def describe_button(rule: dict) -> str:
    if "button_text" in rule:
        return f"دکمه «{rule['button_text']}»"
    return f"دکمه‌ی شماره {rule['button_number']}"


def find_button(message, rule: dict):
    rows = message.buttons or []
    flat = [b for row in rows for b in row]
    if "button_text" in rule:
        want = rule["button_text"].strip().lower()
        for b in flat:
            if (b.text or "").strip().lower() == want:
                return b
        for b in flat:
            if want in (b.text or "").strip().lower():
                return b
        return None
    idx = rule["button_number"] - 1
    return flat[idx] if 0 <= idx < len(flat) else None


async def resolve_chat(raw: str):
    ref = parse_chat(raw)
    if ref is None:
        raise ValueError(
            "فرمت گپ درست نیست. یوزرنیم (@name)، آیدی عددی یا لینک t.me بفرست."
        )
    try:
        entity = await user.get_entity(ref)
    except Exception:
        await user.get_dialogs(limit=None)  # پر کردن کش و تلاش دوباره
        entity = await user.get_entity(ref)
    return utils.get_peer_id(entity), utils.get_display_name(entity)


def describe_rule(rule: dict) -> str:
    kw = "هر پیام" if rule["keyword"] == "*" else f"«{rule['keyword']}»"
    return (
        f"#{rule['id']}  📍 {rule['chat_title']}\n"
        f"      🔑 {kw}\n"
        f"      🔘 {describe_button(rule)}\n"
        f"      ⏱ {rule['delay']:g} ثانیه"
    )


def new_rule(chat_id, title, keyword, button, delay=None) -> dict:
    keyword = keyword.strip()
    if not keyword:
        raise ValueError("کلمه خالی است.")
    rule = {
        "id": store.data["next_id"],
        "chat_id": chat_id,
        "chat_title": title,
        "keyword": keyword,
        "delay": DEFAULT_DELAY if delay is None else delay,
        **button,
    }
    store.data["next_id"] += 1
    store.data["rules"].append(rule)
    store.save()
    return rule


# ───────────────────────── یوزربات ─────────────────────────
@user.on(events.NewMessage(incoming=True))
async def on_message(event):
    if not store.data["enabled"]:
        return
    low = (event.raw_text or "").lower()
    for rule in store.data["rules"]:
        if rule["chat_id"] != event.chat_id:
            continue
        if rule["keyword"] != "*" and rule["keyword"].lower() not in low:
            continue
        btn = find_button(event.message, rule)
        if btn is None:
            log.info("قانون #%s: کلمه پیدا شد ولی دکمه‌ی مورد نظر نبود", rule["id"])
            continue
        await asyncio.sleep(rule["delay"] + random.uniform(0, 0.5))
        try:
            await btn.click()
            log.info("قانون #%s: %s زده شد", rule["id"], describe_button(rule))
        except Exception:
            log.exception("قانون #%s: خطا در زدن دکمه", rule["id"])
        break


# ───────────────────────── ربات کنترل ─────────────────────────
HELP = (
    "🤖 ربات کنترل یوزربات\n\n"
    "/add ـ افزودن قانون (مرحله‌به‌مرحله)\n"
    "/add @گپ | کلمه | دکمه ـ افزودن سریع\n"
    "   دکمه: متن دکمه، یا #2 برای دکمه‌ی شماره ۲\n"
    "   کلمه: * یعنی هر پیام\n"
    "/list ـ لیست قوانین\n"
    "/del N ـ حذف قانون شماره N\n"
    "/on  /off ـ روشن / خاموش کردن\n"
    "/status ـ وضعیت\n"
    "/cancel ـ لغو افزودن"
)

flow: dict = {}


def menu_buttons():
    on = store.data["enabled"]
    return [
        [Button.inline("📋 قوانین", b"list"), Button.inline("➕ افزودن", b"add")],
        [Button.inline("⏸ خاموش کردن" if on else "▶️ روشن کردن", b"toggle")],
    ]


def status_text() -> str:
    state = "🟢 روشن" if store.data["enabled"] else "🔴 خاموش"
    return f"وضعیت: {state}\nتعداد قوانین: {len(store.data['rules'])}"


async def send_menu(event):
    await event.respond(f"{HELP}\n\n{status_text()}", buttons=menu_buttons())


async def send_list(event):
    rules = store.data["rules"]
    if not rules:
        await event.respond(
            "هنوز قانونی نداری.", buttons=[[Button.inline("➕ افزودن", b"add")]]
        )
        return
    text = "📋 قوانین:\n\n" + "\n\n".join(describe_rule(r) for r in rules)
    buttons = [[Button.inline(f"🗑 حذف #{r['id']}", f"del:{r['id']}".encode())] for r in rules]
    await event.respond(text, buttons=buttons)


def delete_rule(rule_id: int) -> bool:
    before = len(store.data["rules"])
    store.data["rules"] = [r for r in store.data["rules"] if r["id"] != rule_id]
    if len(store.data["rules"]) != before:
        store.save()
        return True
    return False


async def start_flow(event):
    flow.clear()
    flow["step"] = "chat"
    await event.respond(
        "1️⃣ گپ را بفرست:\n"
        "یوزرنیم (@name)، آیدی عددی یا لینک t.me\n"
        "(اکانتت باید با آن گپ/ربات ارتباط داشته باشد)\n\n/cancel برای لغو"
    )


async def continue_flow(event, text: str):
    step = flow.get("step")
    if step == "chat":
        try:
            chat_id, title = await resolve_chat(text)
        except ValueError as e:
            await event.respond(f"❌ {e}")
            return
        except Exception:
            log.exception("resolve_chat failed")
            await event.respond(
                "❌ این گپ پیدا نشد. مطمئن شو اکانتت عضو آن است (یا قبلاً با آن چت کرده)، "
                "و دوباره بفرست یا /cancel."
            )
            return
        flow.update(step="keyword", chat_id=chat_id, title=title)
        await event.respond(
            f"✅ گپ: {title}\n\n2️⃣ کلمه را بفرست؛ اگر در پیام بود فعال شود.\n"
            "(برای «هر پیام» فقط * بفرست)"
        )
    elif step == "keyword":
        if not text:
            await event.respond("❌ کلمه خالی است، دوباره بفرست.")
            return
        flow.update(step="button", keyword=text)
        await event.respond(
            "3️⃣ دکمه را مشخص کن:\n"
            "• متن دکمه (مثلاً: تایید)\n"
            "• یا شماره‌ی دکمه به‌شکل #1 ، #2 ، ..."
        )
    elif step == "button":
        try:
            button = parse_button(text)
        except ValueError as e:
            await event.respond(f"❌ {e}")
            return
        rule = new_rule(flow["chat_id"], flow["title"], flow["keyword"], button)
        flow.clear()
        await event.respond(f"✅ قانون اضافه شد:\n\n{describe_rule(rule)}")


async def cmd_add(event, arg: str):
    if "|" not in arg:
        await start_flow(event)
        return
    parts = [p.strip() for p in arg.split("|")]
    if len(parts) not in (3, 4):
        await event.respond("فرمت: /add @گپ | کلمه | دکمه")
        return
    try:
        chat_id, title = await resolve_chat(parts[0])
        button = parse_button(parts[2])
        delay = float(parts[3].translate(FA_DIGITS)) if len(parts) == 4 else None
        rule = new_rule(chat_id, title, parts[1], button, delay)
    except ValueError as e:
        await event.respond(f"❌ {e}")
        return
    except Exception:
        log.exception("add failed")
        await event.respond("❌ گپ پیدا نشد. اکانتت باید عضو آن باشد.")
        return
    await event.respond(f"✅ قانون اضافه شد:\n\n{describe_rule(rule)}")


@bot.on(events.NewMessage(incoming=True, func=lambda e: e.is_private and e.sender_id == OWNER_ID))
async def on_owner(event):
    text = (event.raw_text or "").strip()
    if text.startswith("/"):
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        arg = arg.strip()
        if cmd in ("/start", "/help", "/menu"):
            await send_menu(event)
        elif cmd == "/list":
            await send_list(event)
        elif cmd == "/add":
            await cmd_add(event, arg)
        elif cmd == "/del":
            try:
                ok = delete_rule(int(arg.translate(FA_DIGITS).lstrip("#")))
            except ValueError:
                await event.respond("فرمت: /del 3")
                return
            await event.respond("🗑 حذف شد." if ok else "قانونی با این شماره نیست.")
        elif cmd == "/on":
            store.data["enabled"] = True
            store.save()
            await event.respond("🟢 روشن شد.")
        elif cmd == "/off":
            store.data["enabled"] = False
            store.save()
            await event.respond("🔴 خاموش شد.")
        elif cmd == "/status":
            await event.respond(status_text())
        elif cmd == "/cancel":
            flow.clear()
            await event.respond("لغو شد.")
        else:
            await event.respond("دستور ناشناخته. /help")
        return
    if flow:
        await continue_flow(event, text)
    else:
        await send_menu(event)


@bot.on(events.NewMessage(incoming=True, func=lambda e: e.is_private and e.sender_id != OWNER_ID))
async def on_stranger(event):
    await event.respond(f"⛔ دسترسی ندارید.\nآیدی عددی شما: {event.sender_id}")


@bot.on(events.CallbackQuery(func=lambda e: e.sender_id == OWNER_ID))
async def on_callback(event):
    data = event.data.decode()
    await event.answer()
    if data == "list":
        await send_list(event)
    elif data == "add":
        await start_flow(event)
    elif data == "toggle":
        store.data["enabled"] = not store.data["enabled"]
        store.save()
        await event.respond(status_text(), buttons=menu_buttons())
    elif data.startswith("del:"):
        ok = delete_rule(int(data.split(":", 1)[1]))
        await event.respond("🗑 حذف شد." if ok else "قبلاً حذف شده بود.")


# ───────────────────────── اجرا ─────────────────────────
async def main():
    await user.connect()
    if not await user.is_user_authorized():
        raise SystemExit(
            "SESSION_STRING نامعتبر یا منقضی است. دوباره generate_session.py را اجرا کن."
        )
    me = await user.get_me()
    log.info("یوزربات وارد شد: %s (id=%s)", utils.get_display_name(me), me.id)

    await bot.start(bot_token=BOT_TOKEN)
    log.info("ربات کنترل آماده است")
    await user.get_dialogs(limit=None)  # پر کردن کش گپ‌ها

    try:
        await bot.send_message(
            OWNER_ID, "✅ برنامه روشن شد.\n\n" + status_text(), buttons=menu_buttons()
        )
    except Exception:
        log.warning("پیام شروع ارسال نشد (در ربات /start را بزن و OWNER_ID را چک کن)")

    await asyncio.gather(user.run_until_disconnected(), bot.run_until_disconnected())


if __name__ == "__main__":
    asyncio.run(main())
