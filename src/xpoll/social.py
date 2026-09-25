import logging
import struct
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("xpoll")

MAX_BYTES = 5 * 1024 * 1024  # X rejects card images above 5 MB
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@dataclass(frozen=True)
class SocialImage:
    path: Path
    media_type: str
    width: int | None
    height: int | None


def load_social_image(configured: str | None, config_dir: Path) -> SocialImage | None:
    """Resolve the preview image for link cards; a problem disables the card, never the app."""
    if not configured:
        return None
    path = (config_dir / configured).resolve()
    if not path.is_file():
        logger.warning("social_image not found: %s (link previews will have no image)", path)
        return None
    if path.stat().st_size > MAX_BYTES:
        logger.warning("social_image larger than 5 MB, ignored: %s", path)
        return None
    with path.open("rb") as fh:
        head = fh.read(24)
    if head.startswith(PNG_SIGNATURE):
        width, height = struct.unpack(">II", head[16:24])
        return SocialImage(path, "image/png", width, height)
    if head.startswith(b"\xff\xd8"):
        return SocialImage(path, "image/jpeg", None, None)
    logger.warning("social_image is not a PNG or JPEG file, ignored: %s", path)
    return None
