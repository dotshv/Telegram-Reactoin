import time
from pymongo import MongoClient, ASCENDING
import config

_client = None
_db = None

def get_db():
    global _client, _db
    if _db is None:
        _client = MongoClient(config.MONGO_URL, serverSelectionTimeoutMS=10000)
        _db = _client[config.DATABASE_NAME]
    return _db

def init_db():
    db = get_db()
    try:
        db.users.create_index([("user_id", ASCENDING)], unique=True)
        db.accounts.create_index([("user_id", ASCENDING)], unique=True)
    except Exception:
        pass
    cur = db.settings.find_one({"_id": "bot_settings"})
    if not cur:
        db.settings.insert_one({
            "_id": "bot_settings",
            "approval_required": True,
            "admins": list(config.ADMIN_IDS)
        })

def get_settings():
    init_db()
    db = get_db()
    doc = db.settings.find_one({"_id": "bot_settings"})
    if not doc:
        doc = {
            "approval_required": True,
            "admins": list(config.ADMIN_IDS)
        }
        db.settings.update_one({"_id": "bot_settings"}, {"$set": doc}, upsert=True)
    return {
        "approval_required": doc.get("approval_required", True),
        "admins": doc.get("admins", list(config.ADMIN_IDS))
    }

def update_settings(key, value):
    init_db()
    db = get_db()
    db.settings.update_one({"_id": "bot_settings"}, {"$set": {key: value}}, upsert=True)

def toggle_approval_mode():
    settings = get_settings()
    new_mode = not settings.get("approval_required", True)
    update_settings("approval_required", new_mode)
    return new_mode

def is_admin(user_id):
    try:
        uid = int(user_id)
    except Exception:
        uid = user_id
    if uid in config.ADMIN_IDS:
        return True
    settings = get_settings()
    admins = settings.get("admins", [])
    if not admins and not config.ADMIN_IDS:
        add_admin(uid)
        return True
    return uid in admins

def add_admin(user_id):
    try:
        uid = int(user_id)
    except Exception:
        uid = user_id
    settings = get_settings()
    admins = list(settings.get("admins", []))
    if uid not in admins:
        admins.append(uid)
        update_settings("admins", admins)

def remove_admin(user_id):
    try:
        uid = int(user_id)
    except Exception:
        uid = user_id
    if uid in config.ADMIN_IDS:
        return False
    settings = get_settings()
    admins = list(settings.get("admins", []))
    if uid in admins:
        admins.remove(uid)
        update_settings("admins", admins)
        return True
    return False

def get_admins():
    settings = get_settings()
    admins = set(config.ADMIN_IDS)
    for a in settings.get("admins", []):
        try:
            admins.add(int(a))
        except Exception:
            admins.add(a)
    return list(admins)

def get_all_accounts():
    init_db()
    db = get_db()
    docs = list(db.accounts.find({}))
    accounts = []
    for d in docs:
        d.pop("_id", None)
        accounts.append(d)
    return accounts

def add_account(acc_data):
    init_db()
    db = get_db()
    data = dict(acc_data)
    try:
        uid = int(data.get("user_id"))
        data["user_id"] = uid
    except Exception:
        uid = data.get("user_id")
    clean_data = dict(data)
    clean_data.pop("_id", None)
    db.accounts.update_one({"user_id": uid}, {"$set": clean_data}, upsert=True)
    return True

def remove_account(acc_id):
    init_db()
    db = get_db()
    try:
        uid = int(acc_id)
    except Exception:
        uid = acc_id
    res = db.accounts.delete_one({"user_id": uid})
    return res.deleted_count > 0

def get_user(user_id):
    init_db()
    db = get_db()
    try:
        uid = int(user_id)
    except Exception:
        uid = user_id
    doc = db.users.find_one({"user_id": uid})
    if not doc and isinstance(uid, int):
        doc = db.users.find_one({"user_id": str(uid)})
    if doc:
        doc.pop("_id", None)
    return doc

def create_or_update_user(user_id, username=None, first_name=None):
    init_db()
    db = get_db()
    try:
        uid = int(user_id)
    except Exception:
        uid = user_id
    user = get_user(uid)
    settings = get_settings()
    admin_status = is_admin(uid)
    
    if user is None:
        if admin_status or not settings.get("approval_required", True):
            status = "approved"
        else:
            status = "pending"
            
        user = {
            "user_id": uid,
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
        clean_user = dict(user)
        clean_user.pop("_id", None)
        db.users.update_one({"user_id": uid}, {"$set": clean_user}, upsert=True)
    else:
        updates = {}
        if username is not None:
            user["username"] = username
            updates["username"] = username
        if first_name is not None:
            user["first_name"] = first_name
            updates["first_name"] = first_name
        if admin_status and user.get("status") != "approved":
            user["status"] = "approved"
            updates["status"] = "approved"
        if updates:
            db.users.update_one({"user_id": uid}, {"$set": updates})
    return user

def save_user(user):
    init_db()
    db = get_db()
    try:
        uid = int(user["user_id"])
    except Exception:
        uid = user["user_id"]
    clean_user = dict(user)
    clean_user.pop("_id", None)
    db.users.update_one({"user_id": uid}, {"$set": clean_user}, upsert=True)

def get_all_users():
    init_db()
    db = get_db()
    docs = list(db.users.find({}))
    users = []
    for d in docs:
        d.pop("_id", None)
        users.append(d)
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
                unique_channels.add(str(cid))
                
    return {
        "total_users": total_users,
        "approved": approved,
        "pending": pending,
        "banned": banned,
        "total_accounts": total_accounts,
        "total_channels": len(unique_channels)
    }
