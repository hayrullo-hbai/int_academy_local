import os


FRONTEND_PUBLIC_URL = os.getenv("FRONTEND_PUBLIC_URL", "https://iclass.ai")
MEDIA_URL = os.getenv("MEDIA_URL", f"{FRONTEND_PUBLIC_URL}/media")
