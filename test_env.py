from dotenv import load_dotenv
import os

load_dotenv()
print("GOOGLE_CLIENT_ID:", os.getenv("GOOGLE_CLIENT_ID"))