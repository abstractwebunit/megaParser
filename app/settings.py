from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RunnerCfg(BaseModel):
    max_concurrent_accounts: int = 3
    worker_pool_size: int = 3
    recovery_check_minutes: int = 5
    db_poll_interval_seconds: int = 30
    stale_task_timeout_minutes: int = 10


class RateLimitsCfg(BaseModel):
    delay_between_groups: tuple[float, float] = (8, 15)
    delay_between_profiles: tuple[float, float] = (2, 4)
    delay_between_searches: tuple[float, float] = (5, 10)
    delay_between_messages_batch: float = 1.5
    max_groups_per_day: int = 300
    max_groups_per_hour: int = 25
    max_profiles_per_day: int = 200
    max_searches_per_day: int = 20
    max_flood_waits_before_pause: int = 3
    flood_wait_multiplier: float = 2.0
    flood_reset_hours: int = 1
    flood_long_threshold_seconds: int = 300
    account_work_minutes: tuple[int, int] = (120, 180)
    account_rest_minutes: tuple[int, int] = (30, 60)
    night_pause_utc: tuple[int, int] = (2, 7)
    cooldown_rescan_hours: int = 24


class DiscoveryCfg(BaseModel):
    max_depth: int = 4
    max_resolve_per_cycle: int = 500
    seed_groups: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    skip_usernames: list[str] = Field(
        default_factory=lambda: ["joinchat", "addlist", "proxy", "socks", "c", "s", "iv", "share"]
    )


class ScannerCfg(BaseModel):
    messages_per_group: int = 1000
    messages_batch_size: int = 100
    fetch_profiles: bool = False
    max_profiles_per_group: int = 30
    enable_fulltext_index: bool = False


class MembersCfg(BaseModel):
    max_members_per_group: int = 10000
    alphabet_trick_threshold: int = 10000


class MonitorCfg(BaseModel):
    enabled: bool = False
    min_accounts: int = 2
    rebuild_filter_seconds: int = 30
    groups: list[str] = Field(default_factory=list)


class HealthCfg(BaseModel):
    watchdog_queue_stall_minutes: int = 30
    proxy_check_interval_minutes: int = 60


class YamlCfg(BaseModel):
    runner: RunnerCfg = Field(default_factory=RunnerCfg)
    rate_limits: RateLimitsCfg = Field(default_factory=RateLimitsCfg)
    discovery: DiscoveryCfg = Field(default_factory=DiscoveryCfg)
    scanner: ScannerCfg = Field(default_factory=ScannerCfg)
    members: MembersCfg = Field(default_factory=MembersCfg)
    monitor: MonitorCfg = Field(default_factory=MonitorCfg)
    health: HealthCfg = Field(default_factory=HealthCfg)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    database_url: str
    telegram_bot_token: str
    allowed_admin_ids: str = ""
    fernet_key: str
    data_dir: Path = Path("./sessions")
    config_path: Path = Path("./config.yaml")
    log_dir: Path = Path("./logs")
    log_level: str = "INFO"
    dry_run: bool = False

    yaml_cfg: YamlCfg = Field(default_factory=YamlCfg)

    @property
    def admin_ids(self) -> set[int]:
        if not self.allowed_admin_ids.strip():
            return set()
        return {int(x.strip()) for x in self.allowed_admin_ids.split(",") if x.strip()}

    def load_yaml(self) -> None:
        if not self.config_path.exists():
            return
        data: dict[str, Any] = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        self.yaml_cfg = YamlCfg(**data)

    @classmethod
    def load(cls) -> "Settings":
        s = cls()  # type: ignore[call-arg]
        s.load_yaml()
        s.data_dir.mkdir(parents=True, exist_ok=True)
        s.log_dir.mkdir(parents=True, exist_ok=True)
        return s


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings.load()
    return _settings
