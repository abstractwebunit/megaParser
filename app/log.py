import sys
from pathlib import Path

from loguru import logger

_configured = False


def setup_logging(log_dir: Path, level: str = "INFO") -> None:
    global _configured
    if _configured:
        return
    log_dir.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level:<8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        colorize=True,
    )
    logger.add(
        log_dir / "app.log",
        level=level,
        rotation="100 MB",
        retention=10,
        compression="gz",
        encoding="utf-8",
        enqueue=True,
    )
    logger.add(
        log_dir / "errors.log",
        level="ERROR",
        rotation="50 MB",
        retention=20,
        compression="gz",
        encoding="utf-8",
        enqueue=True,
        backtrace=True,
        diagnose=False,
    )
    _configured = True


__all__ = ["logger", "setup_logging"]
