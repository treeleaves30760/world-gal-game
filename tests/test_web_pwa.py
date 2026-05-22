"""Tests for world_gal_game.web_pwa — the PWA asset generators.

Everything here is PURE: the manifest/service-worker/meta generators take
plain data and the HTML injector takes a string. No pygbag, no network, no
real index.html. The one filesystem helper (write_pwa_assets) is exercised
against tmp_path only.
"""
from __future__ import annotations

import json
from pathlib import Path

from world_gal_game.web_pwa import (
    MANIFEST_FILENAME,
    SERVICE_WORKER_FILENAME,
    apple_meta_tags,
    inject_pwa_into_html,
    pwa_manifest,
    service_worker_js,
    write_pwa_assets,
)


# --------------------------------------------------------------------------
# pwa_manifest
# --------------------------------------------------------------------------


def test_manifest_has_required_keys() -> None:
    m = pwa_manifest("My VN Game")
    # Required for Add-to-Home-Screen + standalone launch.
    assert m["name"] == "My VN Game"
    assert m["display"] == "standalone"
    assert m["start_url"]  # non-empty
    assert isinstance(m["icons"], list) and len(m["icons"]) >= 1
    # Every icon entry needs a src + sizes + type.
    for icon in m["icons"]:
        assert icon["src"]
        assert icon["sizes"]
        assert icon["type"] == "image/png"


def test_manifest_is_json_serialisable() -> None:
    m = pwa_manifest("Game")
    # Round-trips through JSON unchanged (no non-serialisable values).
    assert json.loads(json.dumps(m)) == m


def test_manifest_short_name_truncated_and_colors() -> None:
    m = pwa_manifest(
        "A Very Long Game Title Indeed",
        theme_color="#112233",
        background_color="#445566",
    )
    assert len(m["short_name"]) <= 12
    assert m["theme_color"] == "#112233"
    assert m["background_color"] == "#445566"


def test_manifest_has_a_maskable_icon() -> None:
    purposes = {icon.get("purpose") for icon in pwa_manifest("G")["icons"]}
    assert "maskable" in purposes


# --------------------------------------------------------------------------
# service_worker_js
# --------------------------------------------------------------------------


def test_service_worker_contains_lifecycle_and_cache_logic() -> None:
    sw = service_worker_js("wgg-cache-v1", ["./", "./index.html"])
    assert isinstance(sw, str)
    # Install / fetch handlers + the Cache Storage API must be present.
    assert "install" in sw
    assert "fetch" in sw
    assert "caches" in sw
    # The cache name and shell assets are embedded.
    assert "wgg-cache-v1" in sw
    assert "index.html" in sw


def test_service_worker_embeds_assets_as_json_array() -> None:
    sw = service_worker_js("c", ["a.js", "b.png"])
    # Assets are embedded as a JSON array literal (can't break the JS string).
    assert json.dumps(["a.js", "b.png"]) in sw


def test_service_worker_activate_evicts_old_caches() -> None:
    sw = service_worker_js("c", ["./"])
    assert "activate" in sw
    assert "caches.delete" in sw


# --------------------------------------------------------------------------
# apple_meta_tags
# --------------------------------------------------------------------------


def test_apple_meta_tags_present() -> None:
    tags = apple_meta_tags("My Game")
    assert "apple-mobile-web-app-capable" in tags
    assert "apple-mobile-web-app-status-bar-style" in tags
    assert "apple-touch-icon" in tags
    # The title is embedded.
    assert "My Game" in tags


def test_apple_meta_tags_escape_quotes_in_title() -> None:
    tags = apple_meta_tags('Quote"Game')
    # A double-quote in the title can't break out of the content attribute.
    assert 'content="Quote"Game"' not in tags
    assert "&quot;" in tags


# --------------------------------------------------------------------------
# inject_pwa_into_html
# --------------------------------------------------------------------------


def test_inject_adds_manifest_link_and_sw_registration() -> None:
    html = "<html><head></head><body></body></html>"
    out = inject_pwa_into_html(html, "My Game")
    # Manifest link.
    assert f'rel="manifest"' in out
    assert MANIFEST_FILENAME in out
    # Service worker registration script.
    assert "serviceWorker" in out
    assert SERVICE_WORKER_FILENAME in out
    # Apple meta tags spliced in too.
    assert "apple-mobile-web-app-capable" in out
    # Injected before </head>.
    assert out.index(MANIFEST_FILENAME) < out.lower().index("</head>")


def test_inject_is_idempotent() -> None:
    html = "<html><head></head><body></body></html>"
    once = inject_pwa_into_html(html, "G")
    twice = inject_pwa_into_html(once, "G")
    # Second call detects the marker and changes nothing.
    assert once == twice
    # The manifest link appears exactly once.
    assert twice.count(MANIFEST_FILENAME) == 1


def test_inject_without_head_appends_as_fallback() -> None:
    html = "<html><body>no head here</body></html>"
    out = inject_pwa_into_html(html, "G")
    # Degenerate template still ends up with the PWA tags rather than dropping.
    assert MANIFEST_FILENAME in out
    assert "serviceWorker" in out


def test_inject_case_insensitive_head() -> None:
    html = "<HTML><HEAD></HEAD></HTML>"
    out = inject_pwa_into_html(html, "G")
    assert MANIFEST_FILENAME in out


# --------------------------------------------------------------------------
# write_pwa_assets (filesystem wrapper, tmp_path only)
# --------------------------------------------------------------------------


def test_write_pwa_assets_writes_files_and_patches_index(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text(
        "<html><head></head><body></body></html>", encoding="utf-8"
    )
    ok = write_pwa_assets(tmp_path, "My Game")
    assert ok is True

    manifest_path = tmp_path / MANIFEST_FILENAME
    sw_path = tmp_path / SERVICE_WORKER_FILENAME
    assert manifest_path.exists()
    assert sw_path.exists()

    # Manifest is valid JSON with the right name.
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["name"] == "My Game"
    assert manifest["display"] == "standalone"

    # index.html was patched.
    patched = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert MANIFEST_FILENAME in patched
    assert "serviceWorker" in patched


def test_write_pwa_assets_missing_index_skips_patch_no_raise(
    tmp_path: Path,
) -> None:
    # No index.html present — must still write manifest + worker, return False,
    # and not raise.
    ok = write_pwa_assets(tmp_path, "Game")
    assert ok is False
    assert (tmp_path / MANIFEST_FILENAME).exists()
    assert (tmp_path / SERVICE_WORKER_FILENAME).exists()


def test_write_pwa_assets_default_cache_name_in_worker(tmp_path: Path) -> None:
    write_pwa_assets(tmp_path, "Game")
    sw = (tmp_path / SERVICE_WORKER_FILENAME).read_text(encoding="utf-8")
    # Default cache name is derived from the app name.
    assert "wgg-Game-v1" in sw
