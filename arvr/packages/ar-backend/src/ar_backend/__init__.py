"""ar_backend — Episodes + Scenes API (spec section 36-37), wrapping
ar_datapipe.run_episode. See app.create_app for the FastAPI factory."""

from .app import create_app
from .store import EpisodeRecord, EpisodeStore

__all__ = ["EpisodeRecord", "EpisodeStore", "create_app"]
