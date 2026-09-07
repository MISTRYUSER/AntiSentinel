"""Environment-backed configuration for the independent code-map daemon."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from antisentinel.domain.errors import InvalidInputError


@dataclass(frozen=True)
class CodeMapConfig:
    enabled: bool = False
    storage_root: Path = Path("storage")
    database_path: Path = Path("storage/antisentinel.db")
    cache_root: Path = Path("storage/code-map-git")
    poll_seconds: float = 5.0
    min_interval_seconds: int = 1_800
    max_interval_seconds: int = 14_400
    git_timeout_seconds: float = 60.0
    build_timeout_seconds: float = 120.0
    lease_seconds: int = 60
    heartbeat_seconds: int = 10

    def __post_init__(self) -> None:
        if self.poll_seconds <= 0 or self.git_timeout_seconds <= 0 or self.build_timeout_seconds <= 0:
            raise InvalidInputError("daemon timeouts and poll interval must be positive")
        if self.min_interval_seconds <= 0 or self.max_interval_seconds <= 0 or self.min_interval_seconds > self.max_interval_seconds:
            raise InvalidInputError("daemon intervals must satisfy 0 < min <= max")
        if self.lease_seconds <= 0 or self.heartbeat_seconds <= 0 or self.heartbeat_seconds >= self.lease_seconds:
            raise InvalidInputError("heartbeat must be positive and shorter than lease")

    @classmethod
    def from_environment(cls) -> "CodeMapConfig":
        storage_root = Path(os.getenv("ANTISENTINEL_STORAGE_ROOT", "storage")).expanduser()
        database_path = Path(os.getenv("ANTISENTINEL_CODE_MAP_DB", str(storage_root / "antisentinel.db"))).expanduser()
        cache_root = Path(os.getenv("ANTISENTINEL_CODE_MAP_CACHE_ROOT", str(storage_root / "code-map-git"))).expanduser()
        return cls(
            enabled=os.getenv("ANTISENTINEL_CODE_MAP_ENABLED", "0").strip() == "1",
            storage_root=storage_root,
            database_path=database_path,
            cache_root=cache_root,
            poll_seconds=float(os.getenv("ANTISENTINEL_CODE_MAP_POLL_SECONDS", "5")),
            min_interval_seconds=int(os.getenv("ANTISENTINEL_CODE_MAP_MIN_INTERVAL_SECONDS", "1800")),
            max_interval_seconds=int(os.getenv("ANTISENTINEL_CODE_MAP_MAX_INTERVAL_SECONDS", "14400")),
            git_timeout_seconds=float(os.getenv("ANTISENTINEL_CODE_MAP_GIT_TIMEOUT_SECONDS", "60")),
            build_timeout_seconds=float(os.getenv("ANTISENTINEL_CODE_MAP_BUILD_TIMEOUT_SECONDS", "120")),
            lease_seconds=int(os.getenv("ANTISENTINEL_CODE_MAP_LEASE_SECONDS", "60")),
            heartbeat_seconds=int(os.getenv("ANTISENTINEL_CODE_MAP_HEARTBEAT_SECONDS", "10")),
        )
