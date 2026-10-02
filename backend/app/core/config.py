import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / '.env')

def as_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {'1','true','yes','on'}

class Settings:
    app_name = os.getenv('APP_NAME', 'Faculty Research Intelligence')
    version = os.getenv('APP_VERSION', '1.0.0')
    database_path = os.getenv('DATABASE_PATH', str(ROOT / 'data' / 'research_intelligence.db'))
    cors_origins = os.getenv('CORS_ORIGINS', 'http://127.0.0.1:5173,http://localhost:5173')
    university_name = os.getenv('UNIVERSITY_NAME', 'Vignan University')
    scopus_api_key = os.getenv('SCOPUS_API_KEY', '')
    scopus_inst_token = os.getenv('SCOPUS_INST_TOKEN', '')
    scopus_enabled = as_bool('SCOPUS_ENABLED', True)
    serpapi_api_key = os.getenv('SERPAPI_API_KEY', '')
    scholar_provider_url = os.getenv('SCHOLAR_PROVIDER_URL', 'https://serpapi.com/search?engine=google_scholar_author')
    scholar_enabled = as_bool('SCHOLAR_ENABLED', False)
    sync_interval_hours = int(os.getenv('SYNC_INTERVAL_HOURS', '6'))
    auth_required = as_bool('AUTH_REQUIRED', True)
    auth_username = os.getenv('AUTH_USERNAME', 'admin')
    auth_password = os.getenv('AUTH_PASSWORD', 'change-me-now')
    auth_secret = os.getenv('AUTH_SECRET', 'change-this-secret-in-production')
    auth_session_hours = int(os.getenv('AUTH_SESSION_HOURS', '8'))

settings = Settings()
Path(settings.database_path).parent.mkdir(parents=True, exist_ok=True)
