"""
این فایل را فقط یک بار روی کامپیوتر/گوشی خودت اجرا کن تا SESSION_STRING بگیری.
(روی Railway اجرا نکن.)
"""
from telethon import TelegramClient
from telethon.sessions import StringSession

api_id = int(input("API_ID: ").strip())
api_hash = input("API_HASH: ").strip()

with TelegramClient(StringSession(), api_id, api_hash) as client:
    print("\n=== SESSION_STRING شما (محرمانه؛ مثل رمز اکانتت) ===\n")
    print(client.session.save())
    print("\n=== پایان ===")
