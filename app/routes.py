"""Compatibility re-export module.

Legacy helper imports used to come from app.routes. The route registration block was
removed after the app moved to blueprints. Keep this module as a thin shell so any
older imports still resolve while the helpers live in services/utils modules.
"""

from __future__ import annotations

from .services._legacy_support import *  # noqa: F401,F403
