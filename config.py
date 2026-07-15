import os

from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
