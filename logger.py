"""
logger.py — yt-video

Central logger. Import everywhere as:
    from logger import logger
"""

import logging
import sys

_FMT = "%(asctime)s  %(levelname)-8s  %(name)s — %(message)s"
_DATE_FMT = "%H:%M:%S"

logging.basicConfig(
    level=logging.INFO,
    format=_FMT,
    datefmt=_DATE_FMT,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("output/pipeline.log", mode="a", encoding="utf-8"),
    ],
)

logger = logging.getLogger("yt-video")
