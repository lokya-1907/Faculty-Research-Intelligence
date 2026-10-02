import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


def as_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)

    if value is None:
        return default

    return value.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    # Application
    app_name = os.getenv(
        "APP_NAME",
        "Faculty Research Intelligence",
    )

    version = os.getenv(
        "APP_VERSION",
        "1.0.0",
    )

    # Database
    database_path = os.getenv(
        "DATABASE_PATH",
        str(ROOT / "data" / "research_intelligence.db"),
    )

    # CORS
    #
    # IMPORTANT:
    # The Vercel frontend URL must be present in Render's
    # CORS_ORIGINS environment variable.
    #
    # Example:
    # CORS_ORIGINS=http://127.0.0.1:5173,http://localhost:5173,https://faculty-research-intelligence.vercel.app
    #
    cors_origins = os.getenv(
        "CORS_ORIGINS",
        ",".join(
            [
                "http://127.0.0.1:5173",
                "http://localhost:5173",
                "https://faculty-research-intelligence.vercel.app",
            ]
        ),
    )

    # University
    university_name = os.getenv(
        "UNIVERSITY_NAME",
        "Vignan University",
    )

    # Scopus
    scopus_api_key = os.getenv(
        "SCOPUS_API_KEY",
        "",
    )

    scopus_inst_token = os.getenv(
        "SCOPUS_INST_TOKEN",
        "",
    )

    scopus_enabled = as_bool(
        "SCOPUS_ENABLED",
        True,
    )

    # Google Scholar / SerpAPI
    serpapi_api_key = os.getenv(
        "SERPAPI_API_KEY",
        "",
    )

    scholar_provider_url = os.getenv(
        "SCHOLAR_PROVIDER_URL",
        "https://serpapi.com/search?engine=google_scholar_author",
    )

    scholar_enabled = as_bool(
        "SCHOLAR_ENABLED",
        False,
    )

    # Synchronization
    sync_interval_hours = int(
        os.getenv(
            "SYNC_INTERVAL_HOURS",
            "6",
        )
    )

    # Authentication
    auth_required = as_bool(
        "AUTH_REQUIRED",
        True,
    )

    auth_username = os.getenv(
        "AUTH_USERNAME",
        "admin",
    )

    auth_password = os.getenv(
        "AUTH_PASSWORD",
        "change-me-now",
    )

    auth_secret = os.getenv(
        "AUTH_SECRET",
        "change-this-secret-in-production",
    )

    auth_session_hours = int(
        os.getenv(
            "AUTH_SESSION_HOURS",
            "8",
        )
    )


settings = Settings()

# Make sure the database directory exists.
Path(settings.database_path).parent.mkdir(
    parents=True,
    exist_ok=True,
)
