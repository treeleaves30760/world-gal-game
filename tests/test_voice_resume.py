"""Logical voice checkpoints and frame-aligned PCM resume."""
from __future__ import annotations

import os
import hashlib

os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

from world_gal_game.ui.assets import AssetManager


class _Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class _Channel:
    def __init__(self) -> None:
        self.busy = False
        self.played: list[object] = []

    def play(self, sound: object) -> None:
        self.played.append(sound)
        self.busy = True

    def get_busy(self) -> bool:
        return self.busy

    def stop(self) -> None:
        self.busy = False


class _SourceSound:
    def __init__(self, *, duration: float, raw: bytes = b"") -> None:
        self.duration = duration
        self.raw = raw
        self.volume = 1.0

    def get_length(self) -> float:
        return self.duration

    def get_raw(self) -> bytes:
        return self.raw

    def set_volume(self, volume: float) -> None:
        self.volume = volume


class _ResumedSound:
    def __init__(self, raw: bytes) -> None:
        self.raw = raw
        self.volume = 1.0

    def set_volume(self, volume: float) -> None:
        self.volume = volume


def _wire_playback(
    monkeypatch: pytest.MonkeyPatch,
    assets: AssetManager,
    source: _SourceSound,
) -> _Channel:
    channel = _Channel()
    monkeypatch.setattr(assets, "sound", lambda _path: source)
    monkeypatch.setattr(pygame.mixer, "Channel", lambda index: channel)
    monkeypatch.setattr(pygame.mixer, "get_init", lambda: (4, -16, 1))
    return channel


def test_capture_voice_playback_uses_injected_clock_at_midpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock(40.0)
    assets = AssetManager(monotonic=clock)
    raw = bytes(range(32))
    source = _SourceSound(duration=4.0, raw=raw)
    _wire_playback(monkeypatch, assets, source)

    handle = assets.play_voice("assets/voice/line.ogg", volume=0.65)
    assert handle is not None
    clock.now = 41.5

    checkpoint = assets.capture_voice_playback(
        expected_path="assets/voice/line.ogg",
        handle=handle,
    )

    assert checkpoint is not None
    assert checkpoint["version"] == 2
    assert checkpoint["state"] == "playing"
    assert checkpoint["position_seconds"] == pytest.approx(1.5)
    assert checkpoint["duration_seconds"] == pytest.approx(4.0)
    assert checkpoint["pcm_sha256"] == hashlib.sha256(raw).hexdigest()
    assert checkpoint["mixer_format"] == [4, -16, 1]
    assert checkpoint["fallback"] == "silence"


def test_capture_voice_playback_records_naturally_completed_clip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock(10.0)
    assets = AssetManager(monotonic=clock)
    raw = bytes(range(26))
    source = _SourceSound(duration=3.25, raw=raw)
    channel = _wire_playback(monkeypatch, assets, source)

    handle = assets.play_voice("assets/voice/finished.ogg")
    assert handle is not None
    clock.now = 10.2
    channel.busy = False

    checkpoint = assets.capture_voice_playback(
        expected_path="assets/voice/finished.ogg",
        handle=handle,
    )

    assert checkpoint == {
        "version": 2,
        "state": "completed",
        "position_seconds": 3.25,
        "duration_seconds": 3.25,
        "pcm_sha256": hashlib.sha256(raw).hexdigest(),
        "mixer_format": [4, -16, 1],
        "fallback": "silence",
    }


@pytest.mark.parametrize(
    ("duration", "raw"),
    [
        (2.0, b""),
        (2.0, b"\x00"),
        (8.0, bytes(range(16))),
    ],
    ids=["empty", "partial-frame", "duration-mismatch"],
)
def test_capture_voice_playback_refuses_unprovable_pcm_identity(
    monkeypatch: pytest.MonkeyPatch,
    duration: float,
    raw: bytes,
) -> None:
    assets = AssetManager(monotonic=lambda: 1.0)
    source = _SourceSound(duration=duration, raw=raw)
    _wire_playback(monkeypatch, assets, source)

    handle = assets.play_voice("assets/voice/unprovable.ogg")

    assert handle is not None
    assert assets.capture_voice_playback(
        expected_path="assets/voice/unprovable.ogg",
        handle=handle,
    ) is None


def test_resume_voice_plays_exact_frame_aligned_pcm_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Four 16-bit mono frames per second. A 0.875 second checkpoint floors to
    # frame 3, so the resumed Sound must begin at byte 6.
    raw = bytes(range(20))
    source = _SourceSound(duration=2.5, raw=raw)
    clock = _Clock(100.0)
    assets = AssetManager(monotonic=clock)
    channel = _Channel()
    created: list[_ResumedSound] = []

    monkeypatch.setattr(assets, "sound", lambda _path: source)
    monkeypatch.setattr(pygame.mixer, "get_init", lambda: (4, -16, 1))
    monkeypatch.setattr(pygame.mixer, "Channel", lambda index: channel)

    def make_sound(*, buffer: bytes) -> _ResumedSound:
        sound = _ResumedSound(bytes(buffer))
        created.append(sound)
        return sound

    monkeypatch.setattr(pygame.mixer, "Sound", make_sound)

    outcome = assets.resume_voice(
        "assets/voice/resume.ogg",
        position_seconds=0.875,
        duration_seconds=2.5,
        pcm_sha256=hashlib.sha256(raw).hexdigest(),
        mixer_format=[4, -16, 1],
        volume=0.35,
    )

    assert outcome == "exact"
    assert len(created) == 1
    assert created[0].raw == raw[6:]
    assert created[0].volume == pytest.approx(0.35)
    assert channel.played == [created[0]]

    # The logical clock continues from the actual decoded frame boundary.
    clock.now = 100.5
    checkpoint = assets.capture_voice_playback(
        expected_path="assets/voice/resume.ogg",
        handle=assets.current_voice_handle,
    )
    assert checkpoint is not None
    assert checkpoint["position_seconds"] == pytest.approx(1.25)


def test_resume_voice_is_silent_when_backend_cannot_expose_pcm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _SoundWithoutRaw:
        def get_length(self) -> float:
            return 2.0

    assets = AssetManager(monotonic=lambda: 1.0)
    monkeypatch.setattr(assets, "sound", lambda _path: _SoundWithoutRaw())
    monkeypatch.setattr(pygame.mixer, "get_init", lambda: (48000, -16, 2))

    def unexpected_channel(_index: int) -> _Channel:
        raise AssertionError("unsupported resume must not start a channel")

    def unexpected_sound(*, buffer: bytes) -> _ResumedSound:
        raise AssertionError("unsupported resume must not construct a tail")

    monkeypatch.setattr(pygame.mixer, "Channel", unexpected_channel)
    monkeypatch.setattr(pygame.mixer, "Sound", unexpected_sound)

    outcome = assets.resume_voice(
        "assets/voice/unsupported.ogg",
        position_seconds=0.75,
        duration_seconds=2.0,
        pcm_sha256="0" * 64,
        mixer_format=[48000, -16, 2],
        volume=0.8,
    )

    assert outcome == "silent"
    assert assets.current_voice_handle is None
    assert assets.capture_voice_playback(
        expected_path="assets/voice/unsupported.ogg",
    ) is None


def test_resume_voice_rejects_same_duration_replaced_pcm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = bytes(range(20))
    replacement = bytes(reversed(range(20)))
    source = _SourceSound(duration=2.5, raw=replacement)
    assets = AssetManager(monotonic=lambda: 1.0)
    monkeypatch.setattr(assets, "sound", lambda _path: source)
    monkeypatch.setattr(pygame.mixer, "get_init", lambda: (4, -16, 1))

    def unexpected_channel(_index: int) -> _Channel:
        raise AssertionError("changed PCM must never start playback")

    monkeypatch.setattr(pygame.mixer, "Channel", unexpected_channel)

    outcome = assets.resume_voice(
        "assets/voice/replaced.ogg",
        position_seconds=0.5,
        duration_seconds=2.5,
        pcm_sha256=hashlib.sha256(original).hexdigest(),
        mixer_format=[4, -16, 1],
        volume=0.8,
    )

    assert outcome == "silent"
    assert assets.last_voice_resume_outcome == "silent:asset_changed"
    assert assets.current_voice_handle is None
