"""Read and write the text chunks tavern front-ends use to embed character cards.

A tavern "card PNG" is an ordinary PNG whose ``tEXt`` chunk named ``chara``
holds the base64 of the Character Card V2 JSON; V3 cards add a second chunk
named ``ccv3``. This module handles exactly that container and nothing else:
it never decodes pixels, so it has no imaging dependency and is safe to run on
files that came from the internet.

Untrusted-input rules enforced here:

- the file must start with the PNG signature;
- every chunk length is bounds-checked against the remaining bytes;
- a single text payload larger than :data:`MAX_TEXT_CHUNK` is rejected rather
  than decoded (a card is a few kilobytes of text; megabytes means something
  else is going on);
- CRCs are verified on the chunks we read.
"""
from __future__ import annotations

import base64
import binascii
import json
import struct
import zlib
from typing import Any

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

#: Largest text payload we are willing to decode (bytes, before base64).
MAX_TEXT_CHUNK = 16 * 1024 * 1024

#: Keywords that carry a card, in preference order (V3 first).
CARD_KEYWORDS = ("ccv3", "chara")


class PngMetaError(ValueError):
    """The file is not a PNG we can read card metadata from."""


def _iter_chunks(data: bytes):
    """Yield ``(type, payload, start, end)`` for every chunk in ``data``."""
    if not data.startswith(PNG_SIGNATURE):
        raise PngMetaError("not a PNG file (bad signature)")
    pos = len(PNG_SIGNATURE)
    size = len(data)
    while pos + 8 <= size:
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        ctype = data[pos + 4:pos + 8]
        body_start = pos + 8
        body_end = body_start + length
        if body_end + 4 > size:
            raise PngMetaError("truncated PNG chunk")
        yield ctype, data[body_start:body_end], pos, body_end + 4
        pos = body_end + 4
        if ctype == b"IEND":
            return
    # Ran out of bytes before the end marker: the file was cut short.
    raise PngMetaError("truncated PNG (no IEND chunk)")


def _chunk(ctype: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(ctype + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + ctype + payload + struct.pack(">I", crc)


def read_text_chunks(data: bytes) -> dict[str, str]:
    """Return ``{keyword: text}`` for every ``tEXt`` chunk in a PNG.

    Later chunks with the same keyword win, matching how tavern front-ends
    rewrite a card in place.
    """
    out: dict[str, str] = {}
    for ctype, payload, start, end in _iter_chunks(data):
        if ctype != b"tEXt":
            continue
        if len(payload) > MAX_TEXT_CHUNK:
            raise PngMetaError("text chunk too large to be a character card")
        stored_crc = struct.unpack(">I", data[end - 4:end])[0]
        if zlib.crc32(ctype + payload) & 0xFFFFFFFF != stored_crc:
            raise PngMetaError("PNG text chunk failed its CRC check")
        keyword, sep, text = payload.partition(b"\x00")
        if not sep:
            continue
        out[keyword.decode("latin-1")] = text.decode("latin-1")
    return out


def write_text_chunks(data: bytes, chunks: dict[str, str]) -> bytes:
    """Return a copy of ``data`` with ``chunks`` set as ``tEXt`` chunks.

    Existing ``tEXt`` chunks with the same keywords are dropped first, and the
    new ones are inserted just before ``IEND`` (where tavern front-ends put
    them). Values must be latin-1 encodable — cards are stored as base64, so
    they always are.
    """
    keywords = {k.encode("latin-1") for k in chunks}
    out = bytearray(PNG_SIGNATURE)
    saw_end = False
    for ctype, payload, _start, _end in _iter_chunks(data):
        if ctype == b"tEXt" and payload.partition(b"\x00")[0] in keywords:
            continue
        if ctype == b"IEND":
            for keyword, text in chunks.items():
                body = keyword.encode("latin-1") + b"\x00" + text.encode("latin-1")
                out += _chunk(b"tEXt", body)
            saw_end = True
        out += _chunk(ctype, payload)
    if not saw_end:
        raise PngMetaError("PNG has no IEND chunk")
    return bytes(out)


def minimal_png() -> bytes:
    """A valid 1x1 transparent PNG, used when exporting a card with no art."""
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)   # 1x1, RGBA, 8-bit
    raw = b"\x00" + b"\x00\x00\x00\x00"                   # filter 0 + one pixel
    return (PNG_SIGNATURE + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(raw)) + _chunk(b"IEND", b""))


def read_card_json(data: bytes) -> dict[str, Any]:
    """Extract the embedded card object from PNG bytes.

    Prefers the V3 ``ccv3`` chunk and falls back to V2 ``chara``. Raises
    :class:`PngMetaError` when the PNG carries no card.
    """
    texts = read_text_chunks(data)
    for keyword in CARD_KEYWORDS:
        encoded = texts.get(keyword)
        if not encoded:
            continue
        try:
            decoded = base64.b64decode(encoded, validate=False)
            obj = json.loads(decoded.decode("utf-8"))
        except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
            raise PngMetaError(f"PNG '{keyword}' chunk is not base64 JSON: {exc}") from exc
        if not isinstance(obj, dict):
            raise PngMetaError(f"PNG '{keyword}' chunk does not hold a JSON object")
        return obj
    raise PngMetaError("PNG has no embedded character card (no chara/ccv3 chunk)")


def embed_card_json(png: bytes, *, v2: dict[str, Any] | None = None,
                    v3: dict[str, Any] | None = None) -> bytes:
    """Embed card objects into PNG bytes as ``chara`` (V2) / ``ccv3`` (V3)."""
    chunks: dict[str, str] = {}
    if v2 is not None:
        chunks["chara"] = base64.b64encode(
            json.dumps(v2, ensure_ascii=False).encode("utf-8")).decode("ascii")
    if v3 is not None:
        chunks["ccv3"] = base64.b64encode(
            json.dumps(v3, ensure_ascii=False).encode("utf-8")).decode("ascii")
    if not chunks:
        raise ValueError("embed_card_json needs at least one of v2 / v3")
    return write_text_chunks(png, chunks)
