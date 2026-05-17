"""Developer tools.

`HotReloader` powers F5-reload inside the running game (WGG_DEV=1).

`GameDriver` is a *headless* harness for AI-assisted debugging: boot a
real pygame App without a window, inject events, snapshot state.
Available regardless of WGG_DEV — useful in tests too.
"""

from .hot_reload import HotReloader
from .driver import GameDriver

__all__ = ["HotReloader", "GameDriver"]
