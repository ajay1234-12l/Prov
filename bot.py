
#!/usr/bin/env python3
# premium_autolike_bot.py
# Full-featured Auto-Like Telegram bot (single-file).
# - /allowautopr {group_id} (owner) to allow autolike in groups
# - /run (owner) runs all autolikes immediately
# - /list shows all autolike entries & days remaining
# - /autolike usable in private for all users; in groups only if group allowed
# - Background scheduler still present (checks hourly)
# Note: keep your original API dependency (API_BASE_URL)

import telegram
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram import Update
import requests
import json
import os
import asyncio
from datetime import datetime, timedelta
import threading
from typing import Optional

# === CONFIG ===
BOT_TOKEN = "8454021375:AAGPyCecoAH5GOaESvevjazPPZT7ywyfIsc"  # <- replace if needed
API_BASE_URL = "https://rou-seven.vercel.app"  # <- your API base
DATA_FILE = "bot_data.json"
OWNER_ID = 7968668273  # <- bot owner Telegram ID

# === Helpers ===

def is_owner(user_id: int) -> bool:
    return int(user_id) == int(OWNER_ID)

def ensure_data_structure(data: dict):
    # Guarantee keys exist
    if "users" not in data:
        data["users"] = {}
    if "total_likes" not in data:
        data["total_likes"] = {}
    if "custom_message" not in data:
        data["custom_message"] = ""
    if "auto_like_users" not in data:
        data["auto_like_users"] = {}
    if "allowed_groups" not in data:
        data["allowed_groups"] = []

def load_data() -> dict:
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                data = json.load(f)
        except Exception:
            data = {}
    else:
        data = {}
    ensure_data_structure(data)
    return data

def save_data(data: dict):
    ensure_data_structure(data)
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

def compute_days_remaining(start_iso: str, total_days: int) -> int:
    try:
        start_dt = datetime.fromisoformat(start_iso)
    except Exception:
        return total_days
    elapsed = (datetime.now() - start_dt).days
    remaining = max(0, total_days - elapsed)
    return remaining

# === Bot Commands ===

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome to the Premium Auto-Like Bot! 🚀\n\n"
        "Use /help to see available commands."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🤖 Premium Auto-Like Bot Commands:\n\n"
        "/help - Show this help message\n"
        "/start - Start message\n"
        "/autolike {uid} {region} {days} - Set automatic 24h likes (private: anyone; group: only allowed groups)\n"
        "/like {uid} {region} - Send single like request (Owner only)\n"
        "/mylike - Check your total likes\n"
        "/status - See who is using the bot (Owner only)\n"
        "/setmessage <text> - Set custom autolike response message (Owner only)\n"
        "/setgroup {group_id} - Add group to allowed groups (Owner only)\n"
        "/allowautopr {group_id} - Allow autolike usage in a group (Owner only)\n"
        "/list - Show all autolike scheduled entries (Owner or user to view their own)\n"
        "/run - Run all scheduled autolikes now (Owner only)\n"
    )
    await update.message.reply_text(help_text)

async def autolike(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set automatic likes for a user. Anyone can use in private chat.
       In groups, the group must be allowed (via /allowautopr)."""
    if len(context.args) != 3:
        await update.message.reply_text("Usage: /autolike {uid} {region} {days}\nExample: /autolike 1234567890 US 30")
        return

    uid = context.args[0]
    region = context.args[1]
    try:
        days = int(context.args[2])
        if days <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Days must be a positive integer.")
        return

    chat_type = update.effective_chat.type
    if chat_type in ("group", "supergroup"):
        data = load_data()
        allowed = data.get("allowed_groups", [])
        if update.effective_chat.id not in allowed:
            await update.message.reply_text("❌ This group is not allowed to use /autolike. Owner can enable with /allowautopr {group_id}.")
            return

    user_id_str = str(update.effective_user.id)
    data = load_data()
    # Save auto-like config per-telegram-user
    data["auto_like_users"][user_id_str] = {
        "uid": str(uid),
        "region": str(region),
        "day": int(days),
        "chat_id": update.effective_chat.id,
        "last_run": datetime.now().isoformat(),
        "start_time": datetime.now().isoformat()
    }

    # Save user metadata
    if user_id_str not in data["users"]:
        data["users"][user_id_str] = {
            "telegram_name": update.effective_user.full_name,
            "uid": uid
        }
    save_data(data)

    # Send initial immediate request
    await send_like_request(uid, region, days, update, context)

    await update.message.reply_text(
        f"✅ Auto-like activated for {days} days!\n"
        f"UID: {uid}\nRegion: {region}\nStarted: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

async def send_like_request(uid: str, region: str, day: int, update_obj, context: Optional[ContextTypes.DEFAULT_TYPE]):
    """Send a request to the API and reply back to the user (update_obj must have .message.reply_text and effective_user/ chat)."""
    api_url = f"{API_BASE_URL}/like?uid={uid}&server_name={region}"
    try:
        response = requests.get(api_url, timeout=15)
        response.raise_for_status()
        api_data = response.json()
    except requests.exceptions.RequestException as e:
        # If update_obj supports reply_text, notify; else just print
        try:
            await update_obj.message.reply_text(f"❌ Error calling the API: {e}")
        except Exception:
            print("API error:", e)
        return
    except json.JSONDecodeError:
        try:
            await update_obj.message.reply_text("❌ Error: Could not decode the API response.")
        except Exception:
            print("JSON decode error from API")
        return

    # Build premium response with Days Remaining
    data = load_data()
    user_id_str = str(getattr(update_obj.effective_user, "id", "unknown"))
    auto_info = data.get("auto_like_users", {}).get(user_id_str)
    days_remaining = "N/A"
    if auto_info:
        days_remaining = compute_days_remaining(auto_info.get("start_time", datetime.now().isoformat()), int(auto_info.get("day", day)))

    # Format API-provided fields but be robust to missing keys:
    player_nickname = api_data.get("PlayerNickname") or api_data.get("playerNickname") or "N/A"
    uid_field = api_data.get("UID") or uid
    likes_before = api_data.get("LikesbeforeCommand") or api_data.get("likes_before") or "N/A"
    likes_after = api_data.get("LikesafterCommand") or api_data.get("likes_after") or "N/A"
    likes_given = api_data.get("LikesGivenByAPI") or api_data.get("likes_given") or 0
    status_field = api_data.get("status") or "N/A"

    formatted_response = (
        f"🎮 Auto-Like Results:\n\n"
        f"Player Nickname: {player_nickname}\n"
        f"UID: {uid_field}\n"
        f"Likes Before Command: {likes_before}\n"
        f"Likes After Command: {likes_after}\n"
        f"Likes Given by Bot: {likes_given}\n"
        f"Days Remaining: {days_remaining}\n"
        f"Status: {status_field}\n"
        f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    # Append custom message if set
    custom_message = data.get("custom_message", "")
    if custom_message:
        formatted_response += f"\n\n{custom_message}"

    # Reply to user/chat
    try:
        await update_obj.message.reply_text(formatted_response)
    except Exception as e:
        # If direct reply not available (rare), print to console
        print("Could not send message to chat:", e)

    # Update stats
    try:
        user = update_obj.effective_user
        user_id = str(user.id)
        if user_id not in data["users"]:
            data["users"][user_id] = {"telegram_name": user.full_name, "uid": uid}
        if isinstance(likes_given, int) or (isinstance(likes_given, str) and likes_given.isdigit()):
            likes_number = int(likes_given)
        else:
            try:
                likes_number = int(float(likes_given))
            except Exception:
                likes_number = 0
        if user_id not in data["total_likes"]:
            data["total_likes"][user_id] = {"count": 0, "days": 0}
        data["total_likes"][user_id]["count"] += likes_number
        # increment days only if this was triggered by an autolike (we can't reliably know; just increment 1)
        data["total_likes"][user_id]["days"] += 1
        save_data(data)
    except Exception:
        pass

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner only: show bot status."""
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Access denied. This command is only available to the bot owner.")
        return

    data = load_data()
    users = data.get("users", {})
    auto_like_users = data.get("auto_like_users", {})
    allowed_groups = data.get("allowed_groups", [])

    if not users and not auto_like_users:
        await update.message.reply_text("No users are currently using the bot.")
        return

    response_message = "📊 Bot Status:\n\n"

    if users:
        response_message += "👥 Users:\n"
        for user_id, user_info in users.items():
            response_message += f"- {user_info.get('telegram_name','Unknown')}\n  ID: {user_id}\n  UID: {user_info.get('uid','N/A')}\n\n"

    if auto_like_users:
        response_message += "🔄 Auto-Like Users:\n"
        for user_id, auto_info in auto_like_users.items():
            user_name = users.get(user_id, {}).get('telegram_name', 'Unknown')
            last_run_iso = auto_info.get('last_run')
            try:
                last_run = datetime.fromisoformat(last_run_iso).strftime('%Y-%m-%d %H:%M')
            except Exception:
                last_run = last_run_iso or "N/A"
            response_message += (
                f"- {user_name}\n"
                f"  UID: {auto_info.get('uid')}\n"
                f"  Region: {auto_info.get('region')}\n"
                f"  Days Total: {auto_info.get('day')}\n"
                f"  Last Run: {last_run}\n\n"
            )

    if allowed_groups:
        response_message += f"🏢 Allowed Groups: {len(allowed_groups)}\n"
        for group_id in allowed_groups:
            response_message += f"- {group_id}\n"

    await update.message.reply_text(response_message)

async def like(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner only single like."""
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Access denied. This command is only available to the bot owner.")
        return
    if len(context.args) != 2:
        await update.message.reply_text("Usage: /like {uid} {region}")
        return
    uid = context.args[0]
    region = context.args[1]
    # Use update as the reply target
    await send_like_request(uid, region, 1, update, context)

async def mylike(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user's accumulated likes stored locally."""
    data = load_data()
    user_id = str(update.effective_user.id)
    user_likes = data.get("total_likes", {}).get(user_id)
    if user_likes:
        response_message = (
            f"Your Like Stats:\n\n"
            f"Total Likes Received: {user_likes.get('count', 0)}\n"
            f"Total Days Used: {user_likes.get('days', 0)}"
        )
    else:
        response_message = "You have not used /autolike yet."
    await update.message.reply_text(response_message)

async def setmessage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner-only: set custom message appended to API responses."""
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Access denied. This command is only available to the bot owner.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /setmessage <your_message_here>")
        return
    custom_message = " ".join(context.args)
    data = load_data()
    data["custom_message"] = custom_message
    save_data(data)
    await update.message.reply_text(f"✅ Custom message set:\n\n{custom_message}")

async def setgroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner-only: add group id to allowed_groups (alias)."""
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Access denied. This command is only available to the bot owner.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /setgroup {group_id}\nExample: /setgroup -1001234567890")
        return
    try:
        group_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid group ID. Provide numeric group id.")
        return
    data = load_data()
    if group_id not in data["allowed_groups"]:
        data["allowed_groups"].append(group_id)
        save_data(data)
        await update.message.reply_text(f"✅ Group {group_id} added to allowed groups.")
    else:
        await update.message.reply_text(f"ℹ️ Group {group_id} is already allowed.")

async def allowautopr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner-only: allow a group to use autolike (preferred command)."""
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Access denied. This command is only available to the bot owner.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /allowautopr {group_id}\nExample: /allowautopr -1001234567890")
        return
    try:
        group_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid group ID. Provide numeric group id.")
        return
    data = load_data()
    if group_id in data["allowed_groups"]:
        await update.message.reply_text(f"ℹ️ Group {group_id} is already allowed for auto-pr.")
        return
    data["allowed_groups"].append(group_id)
    save_data(data)
    await update.message.reply_text(f"✅ Group {group_id} is now allowed to use /autolike.")

async def list_autolikes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show autolike entries. Owner sees all, users see their own."""
    data = load_data()
    auto_like_users = data.get("auto_like_users", {})
    if not auto_like_users:
        await update.message.reply_text("No autolike entries found.")
        return

    is_owner_user = is_owner(update.effective_user.id)
    response = "🔎 Autolike Entries:\n\n"
    count = 0
    for user_id, info in auto_like_users.items():
        # If not owner, only show the current user's entry
        if not is_owner_user and str(update.effective_user.id) != str(user_id):
            continue
        count += 1
        start_iso = info.get("start_time", info.get("last_run", datetime.now().isoformat()))
        total_days = int(info.get("day", 0))
        remaining = compute_days_remaining(start_iso, total_days)
        telegram_name = data.get("users", {}).get(user_id, {}).get("telegram_name", "Unknown")
        response += (
            f"- {telegram_name} (tg_id: {user_id})\n"
            f"  UID: {info.get('uid')}\n"
            f"  Region: {info.get('region')}\n"
            f"  Days Total: {total_days}\n"
            f"  Days Remaining: {remaining}\n"
            f"  Start: {start_iso}\n\n"
        )
    if count == 0:
        await update.message.reply_text("You have no autolike entries.")
    else:
        await update.message.reply_text(response)

async def run_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner-only: run all autolike jobs now (synchronously triggers send_like_request for each)."""
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("❌ Access denied. This command is only available to the bot owner.")
        return

    data = load_data()
    auto_like_users = data.get("auto_like_users", {})
    if not auto_like_users:
        await update.message.reply_text("No autolike entries to run.")
        return

    await update.message.reply_text(f"⚡ Running {len(auto_like_users)} autolike(s) now...")
    # We'll iterate and create a lightweight MockUpdate for each entry:
    class MockMessage:
        def __init__(self, chat_id):
            self.target_chat = chat_id
        async def reply_text(self, text, **kwargs):
            # If the chat is a real chat id and bot can send, we could send via bot API.
            # But since we don't have context here for sending from outside update, try using application.bot.send_message
            try:
                # Use global app object if exists in context; else fallback to printing
                app = context.application
                await app.bot.send_message(chat_id=self.target_chat, text=text)
            except Exception:
                print(f"[Mock reply to {self.target_chat}] {text[:120]}")

    async def run_one(user_id, info):
        mock_update = type("MU", (), {})()
        mock_update.effective_user = type("U", (), {"id": int(user_id), "full_name": data.get("users", {}).get(user_id, {}).get("telegram_name", "Auto User")})
        mock_update.effective_chat = type("C", (), {"id": info.get("chat_id")})
        mock_update.message = MockMessage(info.get("chat_id"))
        try:
            await send_like_request(info.get("uid"), info.get("region"), info.get("day"), mock_update, context)
            # Update last_run
            data['auto_like_users'][user_id]['last_run'] = datetime.now().isoformat()
            save_data(data)
        except Exception as e:
            print("Error running autolike for", user_id, e)

    # Run each sequentially to avoid flooding
    for user_id, info in list(auto_like_users.items()):
        await run_one(user_id, info)

    await update.message.reply_text("✅ Done running all autolikes.")

# === Scheduler ===

async def auto_like_scheduler():
    """Background loop that checks autolike entries hourly and triggers them if 24h passed."""
    while True:
        try:
            data = load_data()
            auto_like_users = data.get("auto_like_users", {})
            for user_id, auto_info in list(auto_like_users.items()):
                try:
                    last_run_iso = auto_info.get("last_run", auto_info.get("start_time", datetime.now().isoformat()))
                    last_run = datetime.fromisoformat(last_run_iso)
                except Exception:
                    last_run = datetime.now() - timedelta(days=1, minutes=1)  # force run if parse fails

                if datetime.now() - last_run >= timedelta(hours=24):
                    # Prepare a MockUpdate to allow send_like_request to reply to chat
                    class MockMessage:
                        def __init__(self, chat_id):
                            self.target_chat = chat_id
                        async def reply_text(self, text, **kwargs):
                            # Try to send via bot if app available in global scope; else print
                            try:
                                # Might not have Application instance here; print fallback
                                print(f"[Auto message to {self.target_chat}] {text[:180]}")
                            except Exception:
                                print("Auto message:", text[:180])

                    mock_update = type("MU", (), {})()
                    mock_update.effective_user = type("U", (), {
                        "id": int(user_id),
                        "full_name": data.get("users", {}).get(user_id, {}).get("telegram_name", "Auto User")
                    })
                    mock_update.effective_chat = type("C", (), {"id": auto_info.get("chat_id")})
                    mock_update.message = MockMessage(auto_info.get("chat_id"))

                    try:
                        await send_like_request(auto_info.get("uid"), auto_info.get("region"), auto_info.get("day"), mock_update, None)
                        data['auto_like_users'][user_id]['last_run'] = datetime.now().isoformat()
                        save_data(data)
                    except Exception as e:
                        print(f"Error in auto-like for user {user_id}: {e}")
        except Exception as e:
            print("Error in auto_like_scheduler:", e)
        # check every hour
        await asyncio.sleep(3600)

# === Main ===

def main():
    application = Application.builder().token(BOT_TOKEN).build()

    # Command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("autolike", autolike))
    application.add_handler(CommandHandler("like", like))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("mylike", mylike))
    application.add_handler(CommandHandler("setmessage", setmessage))
    application.add_handler(CommandHandler("setgroup", setgroup))
    application.add_handler(CommandHandler("allowautopr", allowautopr))
    application.add_handler(CommandHandler("list", list_autolikes))
    application.add_handler(CommandHandler("run", run_all))

    # Start the auto-like scheduler in a separate daemon thread to avoid blocking
    def start_scheduler():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(auto_like_scheduler())

    scheduler_thread = threading.Thread(target=start_scheduler, daemon=True)
    scheduler_thread.start()

    # Start polling
    application.run_polling()

if __name__ == "__main__":
    main()