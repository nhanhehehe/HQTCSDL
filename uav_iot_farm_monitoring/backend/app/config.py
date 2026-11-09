import os

from dotenv import load_dotenv


load_dotenv()


class Settings:
    database_url = os.getenv(
        "DATABASE_URL",
        "mssql+pyodbc://@localhost/FarmIoT_DB?driver=ODBC+Driver+18+for+SQL+Server&trusted_connection=yes&TrustServerCertificate=yes",
    )
    app_secret = os.getenv("APP_SECRET", "local-development-secret-change-me")
    token_minutes = int(os.getenv("TOKEN_MINUTES", "480"))


settings = Settings()
