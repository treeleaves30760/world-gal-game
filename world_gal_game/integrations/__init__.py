"""Optional platform integrations (Steam, …).

Everything under this package is **opt-in and gracefully degrading**:
importing it must never fail and must never require a third-party library
or a native redistributable to be present. The engine runs identically
whether or not any integration is active.

Currently:

- :mod:`world_gal_game.integrations.steam_bridge` — a tiny ``ctypes``
  wrapper over the Steamworks ``steam_api`` library (achievements +
  callbacks). Returns ``None`` from :meth:`SteamBridge.try_init` when Steam
  is absent so the game keeps running on itch.io / dev / CI unchanged.
- :mod:`world_gal_game.integrations.steam_plugin` — a hook that mirrors the
  engine's unlocked achievements onto a live :class:`SteamBridge`.
"""
