from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Two deployed environments, and `local` for docker-compose (ADR-0012).
    environment: Literal["local", "staging", "production"] = "local"

    # The OWNER connection. Migrations use this, and only migrations should.
    database_url: str = "postgresql://mishne:mishne@localhost:5432/mishne"

    # The APPLICATION connection: a member of the `mishne_app` role, which is
    # not a superuser and does not have BYPASSRLS, so row-level security
    # actually applies to it. Pointing the API at `database_url` would work
    # perfectly and silently disable every policy in the database.
    # See apps/api/migrations/README.md and infra/local-app-user.sql.
    app_database_url: str = "postgresql://mishne_local:mishne_local@localhost:5432/mishne"

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

    # Serve fixtures instead of querying Postgres.
    use_mocks: bool = True

    # Which org a request belongs to, until B4 puts a real authenticated
    # identity in front of the API. Local only — see the validator below.
    dev_org_id: str = "org_7fa2"

    @model_validator(mode="after")
    def _mocks_never_where_there_is_real_data(self) -> "Settings":
        """`use_mocks` is a development affordance and nothing else.

        Staging holds synthetic media, but it holds real rows: real orgs, real
        jobs, a real ledger. An API that answers from fixtures there reports a
        balance nobody has and a job nobody ran, and it does it convincingly.
        Fail at startup instead — a process that will not boot is a five-minute
        problem, and a deployment quietly serving fixtures is not.
        """
        if self.use_mocks and self.environment != "local":
            raise ValueError(
                f"use_mocks=True is not permitted in environment={self.environment!r}. "
                "Fixtures are for local development against docker-compose."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
