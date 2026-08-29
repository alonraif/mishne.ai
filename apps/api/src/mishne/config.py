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

    # Three buckets by lifecycle, not one. Raw media is the customer's IP and
    # expensive to keep; derived audio is reproducible and should not be kept;
    # artifacts are the deliverable. A lifecycle rule is per bucket, and the
    # three lifecycles differ by more than an order of magnitude in both
    # directions. See infra/s3_lifecycle.py.
    s3_bucket_raw: str = "mishne-dev-raw"
    s3_bucket_derived: str = "mishne-dev-derived"
    s3_bucket_artifacts: str = "mishne-dev-artifacts"
    aws_region: str = "eu-west-1"

    # Point boto3 at MinIO or moto instead of AWS. Empty everywhere but a
    # developer's machine — the validator below refuses it in staging and
    # production, because an endpoint override there means customer media is
    # being written somewhere nobody audited.
    s3_endpoint_url: str = ""

    # The KMS key media is encrypted with at rest. Per environment, never
    # shared: a staging key that can decrypt production objects makes the
    # environment boundary decorative (ADR-0012). Empty means SSE-S3, which is
    # what local MinIO and moto support.
    s3_kms_key_id: str = ""

    # Where a worker stages objects so ffmpeg and pyaaf2 get real file paths.
    # This is the decision recorded in docs/adr/0013: media is downloaded to
    # local disk, not mounted. The consequence is that a worker's disk must
    # hold the largest asset it will ever be given plus its derived audio, and
    # that number is an input to B3's worker sizing.
    work_root: str = "/tmp/mishne"

    # Refuse an upload larger than this. Not a technical limit — S3 takes 5 TiB
    # — but the point at which a file is a conversation rather than a form
    # submission, and a wrong number in a client request should not be able to
    # commit us to storing a petabyte.
    max_upload_bytes: int = 512 * 1024**3  # 512 GiB

    # ── identity (B4) ──────────────────────────────────────────────────────
    #
    # Which provider answers "who is this?". `local` is email and password in
    # our own database — what a developer's machine and the test suite use, and
    # a real path for a single-editor customer. `workos` is the hosted provider
    # docs/architecture/04-security.md names, and the one that carries SAML SSO
    # and SCIM. See docs/adr/0015.
    auth_provider: Literal["local", "workos"] = "local"

    workos_api_key: str = ""
    workos_client_id: str = ""
    # Either pins the sign-in to one customer's connection or organisation. Left
    # empty, WorkOS's own hosted picker decides.
    workos_connection_id: str = ""
    workos_organization_id: str = ""

    # Where the web app is served from. The session cookie and the CORS policy
    # are both scoped to it, and the SSO callback returns the browser there.
    # Credentialed CORS cannot use a wildcard, which is the correct outcome:
    # the origins that may drive this API are named.
    app_origin: str = "http://localhost:3000"

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
        if self.s3_endpoint_url and self.environment != "local":
            raise ValueError(
                f"s3_endpoint_url is not permitted in environment={self.environment!r}. "
                "An endpoint override outside local development means customer "
                "media is being written to something other than the audited bucket."
            )
        if self.auth_provider == "workos" and not (
            self.workos_api_key and self.workos_client_id
        ):
            raise ValueError(
                "auth_provider='workos' needs WORKOS_API_KEY and WORKOS_CLIENT_ID. "
                "A process that boots without them serves an API nobody can sign "
                "in to, and the first symptom is a support ticket."
            )
        if self.environment != "local" and self.app_origin.startswith("http://"):
            raise ValueError(
                f"app_origin={self.app_origin!r} is not https. The session cookie "
                "is the credential for every request; sending it in the clear is "
                "not a state this system may reach."
            )
        if not self.s3_kms_key_id and self.environment != "local":
            raise ValueError(
                f"s3_kms_key_id is required in environment={self.environment!r}. "
                "Customer media is under embargo often enough that "
                "unencrypted-at-rest is not a state this system may reach."
            )
        return self


    @property
    def cookie_secure(self) -> bool:
        """`Secure` everywhere but a developer's machine.

        Not configurable: a session cookie without it is sent over plain HTTP by
        any redirect an attacker can cause, and "we turned it off to debug
        staging" is exactly how that happens.
        """
        return self.environment != "local"


@lru_cache
def get_settings() -> Settings:
    return Settings()
