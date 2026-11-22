# Reference font for the test suite

`jf-openhuninn-2.1.ttf` is jf open 粉圓 2.1, published by justfont at
<https://github.com/justfont/open-huninn-font> under the SIL Open Font License
1.1 (`OFL.txt`, unmodified). The file is the upstream release, byte for byte.

`tests/conftest.py` resolves every font family the engine probes to this file,
so layout tests measure the same glyphs on every machine. It was chosen for its
metrics: one em of line height and full-width CJK glyphs, with Traditional
Chinese coverage.

It is a test fixture only. It is not part of the wheel and no pack depends on
it; a game that needs a font on every platform ships its own through
`bundled_font` (see docs/distribution-web.md).
