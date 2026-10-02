"""Independent ESC/POS parser, used only by tests.

Deliberately written from the command spec, not by reusing phomemo.escpos,
so a bug in the encoder can't hide behind the same bug in the decoder.
"""
from PIL import Image


def decode(stream: bytes):
    """Return (commands, image). image is mode "1" rebuilt from GS v 0 blocks."""
    i, cmds, rows, width = 0, [], [], None
    while i < len(stream):
        b = stream[i]
        if stream[i:i + 2] == b"\x1b\x40":
            cmds.append(("init",)); i += 2
        elif stream[i:i + 6] == b"\x1d\x28\x4b\x02\x00\x31":
            cmds.append(("density", stream[i + 6])); i += 7
        elif stream[i:i + 2] == b"\x1b\x64":
            cmds.append(("feed", stream[i + 2])); i += 3
        elif stream[i:i + 2] == b"\x1d\x56":
            cmds.append(("cut", stream[i + 2])); i += 3
        elif stream[i:i + 4] == b"\x1d\x76\x30\x00":
            xb = stream[i + 4] | stream[i + 5] << 8
            h = stream[i + 6] | stream[i + 7] << 8
            data = stream[i + 8:i + 8 + xb * h]
            assert len(data) == xb * h, "truncated raster block"
            if width is None:
                width = xb * 8
            assert xb * 8 == width, "raster width changed mid-job"
            rows.append(data)
            cmds.append(("raster", xb, h)); i += 8 + xb * h
        else:
            raise AssertionError(f"unknown byte 0x{b:02x} at offset {i}")
    img = None
    if rows:
        raw = b"".join(rows)
        height = len(raw) // (width // 8)
        # ESC/POS: 1 = black. Pillow "1": 1 = white. Invert back.
        img = Image.frombytes("1", (width, height), bytes(255 - x for x in raw))
    return cmds, img
