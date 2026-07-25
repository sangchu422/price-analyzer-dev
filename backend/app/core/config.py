from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    project_root: Path = PROJECT_ROOT
    quote_folder: Path = Path("견적서")
    database_file: Path = Path("data/price_analyzer.sqlite3")
    hchat_embedding_enabled: bool = False
    hchat_embedding_endpoint: str | None = None
    hchat_embedding_api_key: SecretStr | None = None
    hchat_embedding_model: str | None = None
    hchat_embedding_api_style: Literal["openai", "custom"] = "custom"
    hchat_embedding_timeout_seconds: float = 10.0
    embedding_index_file: Path = Path(
        "backend/.local/standard-items.npz"
    )

    @property
    def quote_path(self) -> Path:
        return self._resolve_from_project_root(self.quote_folder)

    @property
    def database_path(self) -> Path:
        return self._resolve_from_project_root(self.database_file)

    @property
    def embedding_index_path(self) -> Path:
        return self._resolve_from_project_root(self.embedding_index_file)

    def _resolve_from_project_root(self, path: Path) -> Path:
        return path if path.is_absolute() else self.project_root / path


settings = Settings()
