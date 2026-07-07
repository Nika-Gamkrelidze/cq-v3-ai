from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://cq:cq@localhost:5432/cq"
    service_api_key: str = "change-me"

    anthropic_api_key: str = ""
    elevenlabs_api_key: str = ""
    s3_endpoint_url: str = ""
    s3_bucket: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_region: str = ""
    embedding_dim: int = 1536


settings = Settings()
