import os
import json
import time
import config

DATA_DIR = "data"
USERS_DIR = os.path.join(DATA_DIR, "users")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
ACCOUNTS_FILE = os.path.join(DATA_DIR, "accounts.json")

def init_db():
    os.makedirs(USERS_DIR, exist_ok=True)
    if not os.path.exists(SETTINGS_FILE):
        default_settings = {
            "approval_required": True,
            "admins": list(config.ADMIN_IDS)
        }
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(default_settings, f, indent=4)
    if not os.path.exists(ACCOUNTS_FILE):
        with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, indent=4)

def load_json(filepath, default_val):
    if not os.path.exists(filepath):
        return default_val
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default_val

def save_json(filepath, data):
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def get_settings():
    init_db()
    return load_json(SETTINGS_FILE, {"approval_required": True, "admins": list(config.ADMIN_IDS)})

def update_settings(key, value):
    settings = get_settings()
    settings[key] = value
    save_json(SETTINGS_FILE, settings)

def toggle_approval_mode():
    settings = get_settings()
    new_mode = not settings.get("approval_required", True)
    settings["approval_required"] = new_mode
    save_json(SETTINGS_FILE, settings)
    return new_mode

def is_admin(user_id):
    if user_id in config.ADMIN_IDS:
        return True
    settings = get_settings()
    admins = settings.get("admins", [])
    if not admins and not config.ADMIN_IDS:
        add_admin(user_id)
        return True
    return user_id in admins

def add_admin(user_id):
    settings = get_settings()
    admins = settings.get("admins", [])
    if user_id not in admins:
        admins.append(user_id)
        settings["admins"] = admins
        save_json(SETTINGS_FILE, settings)

def remove_admin(user_id):
    if user_id in config.ADMIN_IDS:
        return False
    settings = get_settings()
    admins = settings.get("admins", [])
    if user_id in admins:
        admins.remove(user_id)
        settings["admins"] = admins
        save_json(SETTINGS_FILE, settings)
        return True
    return False

def get_admins():
    settings = get_settings()
    admins = set(config.ADMIN_IDS)
    for a in settings.get("admins", []):
        admins.add(a)
    return list(admins)

def get_all_accounts():
    init_db()
    return load_json(ACCOUNTS_FILE, [])

def add_account(acc_data):
    init_db()
    accounts = get_all_accounts()
    accounts = [a for a in accounts if a.get("user_id") != acc_data.get("user_id")]
    accounts.append(acc_data)
    save_json(ACCOUNTS_FILE, accounts)
    return True

def remove_account(acc_id):
    init_db()
    accounts = get_all_accounts()
    new_accounts = [a for a in accounts if a.get("user_id") != acc_id]
    if len(accounts) == len(new_accounts):
        return False
    save_json(ACCOUNTS_FILE, new_accounts)
    return True

def user_file(user_id):
    return os.path.join(USERS_DIR, f"{user_id}.json")

def get_user(user_id):
    init_db()
    filepath = user_file(user_id)
    return load_json(filepath, None)

def create_or_update_user(user_id, username=None, first_name=None):
    init_db()
    user = get_user(user_id)
    settings = get_settings()
    admin_status = is_admin(user_id)
    
    if user is None:
        if admin_status or not settings.get("approval_required", True):
            status = "approved"
        else:
            status = "pending"
            
        user = {
            "user_id": user_id,
            "username": username or "",
            "first_name": first_name or "User",
            "status": status,
            "joined_at": int(time.time()),
            "channels": [],
            "settings": {
                "auto_react": True,
                "reaction_delay": 0.5,
                "emojis": list(config.DEFAULT_EMOJIS)
            }
        }
    else:
        if username is not None:
            user["username"] = username
        if first_name is not None:
            user["first_name"] = first_name
        if admin_status and user.get("status") != "approved":
            user["status"] = "approved"

    save_json(user_file(user_id), user)
    return user

def save_user(user):
    user_id = user["user_id"]
    save_json(user_file(user_id), user)

def get_all_users():
    init_db()
    users = []
    if not os.path.exists(USERS_DIR):
        return users
    for fname in os.listdir(USERS_DIR):
        if fname.endswith(".json"):
            filepath = os.path.join(USERS_DIR, fname)
            data = load_json(filepath, None)
            if data and "user_id" in data:
                users.append(data)
    return users

def set_user_status(user_id, status):
    user = get_user(user_id)
    if user:
        user["status"] = status
        save_user(user)
        return True
    return False

def get_user_channels(user_id):
    user = get_user(user_id)
    return user.get("channels", []) if user else []

def add_user_channel(user_id, channel_data):
    user = get_user(user_id)
    if not user:
        user = create_or_update_user(user_id)
    channels = user.get("channels", [])
    target_id = channel_data.get("chat_id")
    for ch in channels:
        if str(ch.get("chat_id")) == str(target_id):
            return False
    try:
        channel_data["chat_id"] = int(target_id)
    except Exception:
        pass
    channels.append(channel_data)
    user["channels"] = channels
    save_user(user)
    return True

def remove_user_channel(user_id, chat_id):
    user = get_user(user_id)
    if not user:
        return False
    channels = user.get("channels", [])
    new_channels = [c for c in channels if str(c.get("chat_id")) != str(chat_id)]
    if len(channels) == len(new_channels):
        return False
    user["channels"] = new_channels
    save_user(user)
    return True

def toggle_user_auto_react(user_id):
    user = get_user(user_id)
    if not user:
        return True
    curr = user.get("settings", {}).get("auto_react", True)
    if "settings" not in user:
        user["settings"] = {}
    user["settings"]["auto_react"] = not curr
    save_user(user)
    return not curr

def update_user_emojis(user_id, emoji_list):
    user = get_user(user_id)
    if not user:
        return False
    if "settings" not in user:
        user["settings"] = {}
    user["settings"]["emojis"] = emoji_list
    save_user(user)
    return True

def find_owners_of_channel(chat_id):
    users = get_all_users()
    owners = []
    str_cid = str(chat_id)
    for u in users:
        if u.get("status") == "approved" or is_admin(u.get("user_id")):
            for ch in u.get("channels", []):
                if str(ch.get("chat_id")) == str_cid:
                    owners.append(u["user_id"])
                    break
    return owners

def get_all_target_chat_ids():
    users = get_all_users()
    chat_map = {}
    for u in users:
        is_ok = u.get("status") == "approved" or is_admin(u.get("user_id"))
        auto_ok = (u.get("settings") or {}).get("auto_react", True)
        if is_ok and auto_ok:
            for ch in u.get("channels", []):
                cid = ch.get("chat_id")
                if cid is not None:
                    try:
                        int_id = int(cid)
                        if int_id not in chat_map:
                            chat_map[int_id] = []
                        chat_map[int_id].append(u["user_id"])
                    except Exception:
                        pass
                    str_id = str(cid)
                    if str_id not in chat_map:
                        chat_map[str_id] = []
                    chat_map[str_id].append(u["user_id"])
    return chat_map

def get_stats():
    users = get_all_users()
    total_users = len(users)
    approved = sum(1 for u in users if u.get("status") == "approved")
    pending = sum(1 for u in users if u.get("status") == "pending")
    banned = sum(1 for u in users if u.get("status") == "banned")
    total_accounts = len(get_all_accounts())
    
    unique_channels = set()
    for u in users:
        for ch in u.get("channels", []):
            cid = ch.get("chat_id")
            if cid:
                unique_channels.add(cid)
                
    return {
        "total_users": total_users,
        "approved": approved,
        "pending": pending,
        "banned": banned,
        "total_accounts": total_accounts,
        "total_channels": len(unique_channels)
    }
