"""Progressive Web App (PWA) asset generation for web builds.

When a pack is built for the web (:mod:`world_gal_game.build_web`) we want it
to be installable to a phone's home screen and to keep working offline. That
takes three small static artefacts dropped next to pygbag's ``index.html``:

- a **Web App Manifest** (``manifest.webmanifest``) — name, icons, display
  mode, so Android Chrome / iOS Safari offer "Add to Home Screen" and launch
  the page standalone (no browser chrome).
- a **service worker** (``service-worker.js``) — caches the app shell on
  install and serves cache-first so the installed app launches offline.
- a handful of ``<meta>`` / ``<link>`` tags injected into ``index.html`` —
  the manifest link, the service-worker registration script, and the
  ``apple-mobile-web-app-*`` tags iOS needs for a standalone launch.

**Everything in this module is pure.** The generators take plain data and
return strings / dicts; the one HTML-patching helper takes an HTML string and
returns a patched one. None of it imports pygbag, touches the network, or
needs a browser, so it is fully unit-testable. The build step
(:func:`world_gal_game.build_web.build_web`) calls
:func:`write_pwa_assets` after pygbag has emitted ``index.html`` to write the
files out and patch the HTML in place; that wrapper is the only part that does
file I/O and it degrades to a logged no-op if ``index.html`` is absent.

Nothing here is imported on the always-imported engine path — ``build_web``
imports it lazily, the running game never does.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

_log = logging.getLogger("world_gal_game.web_pwa")

# File names we write next to index.html. Kept as module constants so the
# build step, the HTML patcher, and tests all agree on one spelling.
MANIFEST_FILENAME = "manifest.webmanifest"
SERVICE_WORKER_FILENAME = "service-worker.js"

# Default app-shell entries a fresh install should pre-cache. pygbag always
# emits an index.html; the rest are best-effort (the service worker tolerates
# a failed precache entry — see service_worker_js).
DEFAULT_SHELL_ASSETS: tuple[str, ...] = ("./", "./index.html")

# Markers so a re-run of the patcher is idempotent (it won't inject twice).
_INJECT_MARKER = "wgg-pwa-injected"


# --------------------------------------------------------------------------
# pure generators
# --------------------------------------------------------------------------


def pwa_manifest(
    app_name: str,
    *,
    short_name: str | None = None,
    theme_color: str = "#000000",
    background_color: str = "#000000",
    orientation: str = "landscape",
    start_url: str = "./",
) -> dict:
    """Return a Web App Manifest dict for ``app_name``.

    The result is JSON-serialisable and contains every key a browser needs to
    offer "Add to Home Screen" and launch standalone: ``name`` / ``short_name``,
    ``display`` (always ``"standalone"`` so there's no browser chrome),
    ``orientation``, ``start_url``, theme/background colours, and a non-empty
    ``icons`` list.

    Icons reference ``icon-192.png`` / ``icon-512.png`` (and a maskable
    variant). We deliberately do **not** synthesise binary PNGs here — that
    would pull an image dependency onto a path that must stay pure. The build
    documents that a pack ships those icons (or that placeholders are dropped
    in); a missing icon only costs a generic launcher glyph, never a crash.

    ``short_name`` defaults to ``app_name`` and is truncated to 12 chars,
    matching the home-screen label budget on most launchers.
    """
    short = short_name if short_name is not None else app_name
    short = short[:12]
    return {
        "name": app_name,
        "short_name": short,
        "description": f"{app_name} — a World Gal-Game visual novel.",
        "display": "standalone",
        "orientation": orientation,
        "start_url": start_url,
        "scope": "./",
        "theme_color": theme_color,
        "background_color": background_color,
        "icons": [
            {
                "src": "icon-192.png",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": "icon-512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any",
            },
            {
                "src": "icon-512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "maskable",
            },
        ],
    }


def service_worker_js(cache_name: str, assets) -> str:
    """Return a minimal offline-cache service worker as a JS source string.

    Behaviour:

    - **install** — open ``cache_name`` and pre-cache ``assets`` (the app
      shell). Entries are added individually and a failed entry is swallowed,
      so one missing optional asset can't abort the whole install.
    - **activate** — drop any old caches whose name differs from
      ``cache_name`` (so a new build's worker evicts the previous shell).
    - **fetch** — cache-first: serve a cached response when present, otherwise
      go to the network and, on success, populate the cache for next time.
      Network failures with no cache entry simply reject (offline + uncached).

    ``assets`` is any iterable of URL strings; it is embedded as a JSON array
    so arbitrary paths can't break out of the string literal.
    """
    asset_list = list(assets)
    assets_json = json.dumps(asset_list)
    cache_json = json.dumps(cache_name)
    return f"""\
// Auto-generated by world_gal_game.web_pwa — offline app-shell cache.
// Do not edit by hand; regenerate via the web build.
const CACHE_NAME = {cache_json};
const SHELL_ASSETS = {assets_json};

self.addEventListener("install", (event) => {{
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) =>
      // Add entries one at a time so a single 404 doesn't abort the install.
      Promise.all(
        SHELL_ASSETS.map((url) =>
          cache.add(url).catch((err) => {{
            console.warn("[wgg-sw] precache skipped", url, err);
          }})
        )
      )
    ).then(() => self.skipWaiting())
  );
}});

self.addEventListener("activate", (event) => {{
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
}});

self.addEventListener("fetch", (event) => {{
  if (event.request.method !== "GET") {{
    return;
  }}
  event.respondWith(
    caches.match(event.request).then((cached) => {{
      if (cached) {{
        return cached;
      }}
      return fetch(event.request).then((response) => {{
        // Cache successful same-origin responses for next time.
        if (response && response.status === 200 && response.type === "basic") {{
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
        }}
        return response;
      }});
    }})
  );
}});
"""


def apple_meta_tags(app_name: str) -> str:
    """Return the iOS standalone ``<meta>`` / ``<link>`` tags for ``app_name``.

    Safari ignores the Web App Manifest's ``display`` field; to launch
    full-screen from the home screen it needs the legacy
    ``apple-mobile-web-app-*`` meta tags plus an ``apple-touch-icon`` link.
    The status-bar style is ``black-translucent`` so the VN draws under the
    notch area.

    Returns the tags as a newline-joined HTML fragment (no surrounding
    ``<head>``), so the caller can splice it wherever it injects.
    """
    title = app_name.replace('"', "&quot;")
    return "\n".join(
        [
            '<meta name="apple-mobile-web-app-capable" content="yes">',
            '<meta name="mobile-web-app-capable" content="yes">',
            '<meta name="apple-mobile-web-app-status-bar-style" '
            'content="black-translucent">',
            f'<meta name="apple-mobile-web-app-title" content="{title}">',
            '<link rel="apple-touch-icon" href="icon-192.png">',
        ]
    )


def _registration_script() -> str:
    """Return the inline ``<script>`` that registers the service worker."""
    return (
        "<script>\n"
        '  if ("serviceWorker" in navigator) {\n'
        '    window.addEventListener("load", () => {\n'
        f'      navigator.serviceWorker.register("{SERVICE_WORKER_FILENAME}")'
        '.catch((err) => console.warn("[wgg-sw] registration failed", err));\n'
        "    });\n"
        "  }\n"
        "</script>"
    )


def inject_pwa_into_html(html: str, app_name: str) -> str:
    """Return ``html`` with the manifest link + apple meta + SW script added.

    Inserts, just before ``</head>``:

    - ``<link rel="manifest" href="manifest.webmanifest">``
    - the :func:`apple_meta_tags` fragment, and
    - the service-worker registration ``<script>``.

    The block is wrapped in an HTML comment marker so calling this twice on
    the same document is a no-op (it detects the marker and returns the input
    unchanged). If the document has no ``</head>`` the block is appended to the
    end as a fallback rather than dropped, so even a degenerate pygbag template
    still ends up installable.
    """
    if _INJECT_MARKER in html:
        return html

    block = "\n".join(
        [
            f"<!-- {_INJECT_MARKER}: World Gal-Game PWA tags -->",
            f'<link rel="manifest" href="{MANIFEST_FILENAME}">',
            apple_meta_tags(app_name),
            _registration_script(),
            f"<!-- /{_INJECT_MARKER} -->",
        ]
    )

    # Insert before the first </head> (case-insensitive). Fall back to append.
    match = re.search(r"</head>", html, flags=re.IGNORECASE)
    if match is None:
        return html + "\n" + block + "\n"
    idx = match.start()
    return html[:idx] + block + "\n" + html[idx:]


# --------------------------------------------------------------------------
# build-step wrapper (the only part that does file I/O)
# --------------------------------------------------------------------------


def write_pwa_assets(
    output_dir: Path,
    app_name: str,
    *,
    cache_name: str | None = None,
    shell_assets=None,
) -> bool:
    """Write the manifest + service worker and patch ``index.html`` in place.

    Called by the web build *after* pygbag emits its output into
    ``output_dir``. Writes ``manifest.webmanifest`` and ``service-worker.js``
    next to ``index.html``, then injects the manifest link + apple meta + SW
    registration into ``index.html``.

    Resilient by design: if ``index.html`` is not present (pygbag layout
    changed, or this was called too early) the manifest + worker are still
    written but the HTML patch is logged-and-skipped — never raised. Returns
    ``True`` when ``index.html`` was found and patched, ``False`` otherwise.
    The manifest/worker writes themselves are best-effort and any I/O error is
    logged rather than propagated, so a PWA hiccup can't fail an otherwise good
    build.
    """
    output_dir = Path(output_dir)
    cache_name = cache_name or f"wgg-{app_name}-v1"
    shell_assets = (
        list(shell_assets) if shell_assets is not None
        else list(DEFAULT_SHELL_ASSETS)
    )

    try:
        manifest = pwa_manifest(app_name)
        (output_dir / MANIFEST_FILENAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (output_dir / SERVICE_WORKER_FILENAME).write_text(
            service_worker_js(cache_name, shell_assets),
            encoding="utf-8",
        )
    except OSError as exc:  # pragma: no cover - filesystem edge
        _log.warning("write_pwa_assets: could not write PWA files: %s", exc)
        return False

    index = output_dir / "index.html"
    if not index.exists():
        _log.warning(
            "write_pwa_assets: index.html not found in %s; wrote manifest + "
            "service worker but skipped HTML injection.",
            output_dir,
        )
        return False

    try:
        html = index.read_text(encoding="utf-8")
        index.write_text(inject_pwa_into_html(html, app_name), encoding="utf-8")
    except OSError as exc:  # pragma: no cover - filesystem edge
        _log.warning("write_pwa_assets: could not patch index.html: %s", exc)
        return False

    _log.info("write_pwa_assets: PWA assets written + index.html patched.")
    return True
