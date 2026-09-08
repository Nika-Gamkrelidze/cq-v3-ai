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

    # --- Acoustic sentiment (self-hosted prosody sidecar) ---
    # Empty disables the prosody half entirely: sentiment then reports the text signal alone
    # rather than failing, so the stack still runs with no extra container.
    sentiment_url: str = "http://sentiment:8080"

    # Where retained anonymous submissions (audio + synthesised clips) are written. A volume,
    # never the image: these outlive a rebuild and must not be baked into one.
    media_root: str = "/data/media"

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

    # --- Capacity knobs (one uvicorn worker; these ARE the concurrency ceilings) ---
    # The asyncpg pool is shared by HTTP traffic, KB ingestion and (soon) chat precompute,
    # so max is env-driven: it is the one dial that can be turned without a code change.
    db_pool_min: int = 2                     # warm, so the first chat turn never pays connect cost
    db_pool_max: int = 10                    # env: DB_POOL_MAX
    llm_max_concurrency: int = 8             # admission control — 429 fast rather than queue
    embed_query_timeout_s: float = 10.0      # latency-critical query embeds fail fast, not hang

    # --- Anonymous quota identity (services/auth.py::visitor_key) ---
    # The anonymous daily allowance is keyed on the visitor's own address. Where the
    # deployment's network masks it — a container reached through a published port on a host
    # that masquerades the source address, which is unconditional on Docker Desktop and the
    # default outcome on a firewalld host — EVERY visitor arrives as one private address (the
    # bridge gateway) and would share ONE allowance: the first person to spend it locks out
    # the world. The app refuses the anonymous tier in that state instead of pooling everyone.
    #
    # Set this true ONLY where private client addresses really are distinct visitors: a
    # LAN-only deployment, or local development where the one developer IS the traffic.
    # It must stay FALSE on a public deployment — there it can only mean the address was
    # rewritten on the way in, and the fix for that is on the host (see docker-compose.yml).
    anon_trust_private_client_ips: bool = False

    # --- Auth: signing secret for tenant-user session tokens (HMAC) ---
    jwt_secret: str = "change-me-jwt-secret"
    token_ttl_hours: int = 24

    # --- Encryption at rest for provider keys (services/secrets.py) ---
    # A Fernet key: urlsafe-base64 of 32 bytes, exactly what Fernet.generate_key() emits.
    # Empty = plaintext mode: the API boots and works, stores keys unencrypted, and says so
    # once in the log and in /health. Set it on the SERVER's .env and back it up — every
    # stored provider key is unreadable without the key it was sealed with.
    secrets_key: str = ""


settings = Settings()
