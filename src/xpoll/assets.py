import hashlib
from pathlib import Path

from starlette.staticfiles import StaticFiles
from starlette.types import Scope

IMMUTABLE = "public, max-age=31536000, immutable"
SHORT = "public, max-age=300"


def asset_versions(directory: Path) -> dict[str, str]:
    """Content hash per static file, so every change gets a new URL and caches never go stale."""
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()[:12]
        for path in sorted(directory.iterdir())
        if path.is_file()
    }


class VersionedStaticFiles(StaticFiles):
    """Versioned URLs (?v=hash) are cached for a year; unversioned ones only briefly."""

    async def get_response(self, path: str, scope: Scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            versioned = b"v=" in scope.get("query_string", b"")
            response.headers["Cache-Control"] = IMMUTABLE if versioned else SHORT
        return response
