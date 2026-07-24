from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    project_root: Path = PROJECT_ROOT
    quote_folder: Path = Path("견적서")
    database_file: Path = Path("data/price_analyzer.sqlite3")

    @property
    def quote_path(self) -> Path:
        return self._resolve_from_project_root(self.quote_folder)

    @property
    def database_path(self) -> Path:
        return self._resolve_from_project_root(self.database_file)

    def _resolve_from_project_root(self, path: Path) -> Path:
        return path if path.is_absolute() else self.project_root / path
