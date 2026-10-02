"""
Sentinews Observability and APM Package.
"""

from app.infrastructure.observability.decorators import track_compute
from app.infrastructure.observability.registry import get_registry
from app.infrastructure.observability.setup import setup_observability

__all__ = [
    "setup_observability",
    "track_compute",
    "get_registry",
]
