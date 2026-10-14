from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Validated once at startup; the process refuses to boot with invalid config."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: Literal["development", "test", "production"] = Field("development", alias="NODE_ENV")
    port: int = Field(4002, ge=1, le=65535)
    log_level: Literal["critical", "error", "warning", "info", "debug"] = "info"
    cors_allowed_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    database_url: str = Field(pattern=r"^postgresql\+psycopg://")
    db_pool_size: int = Field(5, ge=1, le=50)

    # Token verification (tokens are issued by ecommerce-auth-service).
    jwks_url: str = "http://localhost:4001/.well-known/jwks.json"
    jwt_issuer: str = "ecommerce-auth-service"
    jwt_audience: str = "ecommerce-api"

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue]  (values come from the environment)
