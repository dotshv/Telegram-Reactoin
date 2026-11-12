import os

MANAGER_BOT_TOKEN = os.getenv("MANAGER_BOT_TOKEN", "9216527978:AAHvrklzJ4LJZc3qtnd826OR4yfUtMr-zD0")

API_ID = int(os.getenv("API_ID", 21705136))
API_HASH = os.getenv("API_HASH", "78730e89d196e160b0f1992018c6cb19")

ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "9156503142").split(",") if x.strip()]

MONGO_URL = os.getenv("MONGO_URL", "mongodb+srv://Krishna:pss968048@cluster0.4rfuzro.mongodb.net/?retryWrites=true&w=majority")
DATABASE_NAME = os.getenv("DATABASE_NAME", "ReactionEngineDB")

DEFAULT_EMOJIS = [
    "👍", "❤️", "🔥", "🥰", "👏", "😁", "🎉", "🤩", "💖", "👌", 
    "😍", "💯", "🤝", "🤗", "😇", "🥳", "😎", "💪", "🙌", "✨", 
    "🌟", "🏆", "✌️"
]
