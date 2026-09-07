"""Run the independent code-map scheduler and worker process."""

from __future__ import annotations

import signal

from .config import CodeMapConfig
from .daemon import build_daemon_from_environment


def main() -> int:
    config = CodeMapConfig.from_environment()
    if not config.enabled:
        return 0
    daemon = build_daemon_from_environment(config)
    signal.signal(signal.SIGINT, lambda *_: daemon.stop())
    signal.signal(signal.SIGTERM, lambda *_: daemon.stop())
    try:
        daemon.run_forever()
    finally:
        if daemon.telemetry is not None:
            daemon.telemetry.force_flush()
            daemon.telemetry.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
