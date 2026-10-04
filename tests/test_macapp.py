"""`phomemo ui --make-app`: the generated applet script and bundle."""
from __future__ import annotations

import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from phomemo import macapp


def test_script_quotes_paths_and_never_persists_state():
    src = macapp.applescript(Path("/Users/a b/proj/.venv/bin/phomemo"), port=8700)
    assert 'quoted form of "/Users/a b/proj/.venv/bin/phomemo"' in src
    assert "--port 8700 --idle-exit 180" in src
    assert "[u]i --no-open --from-app --port 8700" in src     # pkill can't match itself
    assert "< /dev/null &" in src                            # do shell script returns at once
    assert "property " not in src and "global " not in src   # would rewrite main.scpt
    assert "Chrome" not in src


def test_icon_is_square_with_transparent_corners():
    img = macapp.draw_icon(256)
    assert img.size == (256, 256)
    assert img.getpixel((2, 2))[3] == 0 and img.getpixel((128, 128))[3] == 255


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("osacompile"),
                    reason="builds a macOS app")
def test_make_app_builds_a_signed_bundle_with_bluetooth(tmp_path, monkeypatch):
    monkeypatch.setattr(macapp, "phomemo_command", lambda: Path("/usr/bin/true"))
    app = macapp.make_app(tmp_path, port=8701)
    info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleIdentifier"] == macapp.BUNDLE_ID
    assert "NSBluetoothAlwaysUsageDescription" in info
    assert (app / "Contents" / "Resources" / "applet.icns").stat().st_size > 10_000
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
