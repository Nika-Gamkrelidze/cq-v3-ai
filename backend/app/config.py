from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://cq:cq@localhost:5432/cq"
    service_api_key: str = "change-me"
    # Protects the admin panel endpoints (X-Admin-Token header).
    admin_token: str = "change-me-admin"
    # Super-admin login for the admin panel (username + password -> admin token).
    superadmin_username: str = "superadmin"
    superadmin_password: str = "change-me"

    # Integration keys — these are the BOOTSTRAP/fallback values. The admin panel
    # can override them at runtime; overrides are stored in the app_settings table.
    anthropic_api_key: str = ""
    elevenlabs_api_key: str = ""

    # Default model / voice choices (also overridable via the admin panel).
    llm_model: str = "claude-opus-4-8"
    stt_model: str = "scribe_v1"
    tts_model: str = "eleven_multilingual_v2"
    tts_voice_id: str = "21m00Tcm4TlvDq8ikWAM"  # ElevenLabs "Rachel" (default voice)

    s3_endpoint_url: str = ""
    s3_bucket: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_region: str = ""

    # --- Embeddings (provider-swappable; also overridable via the admin panel) ---
    # Default: self-hosted BGE-M3 via a Text-Embeddings-Inference (TEI) container.
    embedding_provider: str = "tei"          # tei | openai
    embedding_model: str = "BAAI/bge-m3"
    embedding_base_url: str = "http://embeddings:80"   # TEI service on the compose network
    embedding_api_key: str = ""              # only for the openai provider
    embedding_dim: int = 1024                # BGE-M3 dense dimension

    # --- Auth: signing secret for tenant-user session tokens (HMAC) ---
    jwt_secret: str = "change-me-jwt-secret"
    token_ttl_hours: int = 24


settings = Settings()
