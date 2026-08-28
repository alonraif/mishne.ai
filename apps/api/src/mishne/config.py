from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://mishne:mishne@localhost:5432/mishne"

    s3_bucket_raw: str = "mishne-dev-raw"
    s3_bucket_derived: str = "mishne-dev-derived"
    s3_bucket_artifacts: str = "mishne-dev-artifacts"
    aws_region: str = "eu-west-1"

    # Vendors. Never commit real keys — see .env.example.
    asr_provider: str = "mock"
    asr_api_key: str = ""
    llm_provider: str = "anthropic"
    anthropic_api_key: str = ""

    presign_ttl_seconds: int = 900

    # While the pipeline is a stub, every endpoint serves fixtures.
    use_mocks: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
