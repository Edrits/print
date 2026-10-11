"""`phomemo app --make-app`: the Thermal.app bundle and its launcher."""
from __future__ import annotations

import plistlib
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

from phomemo import macapp


def test_icon_is_square_with_transparent_corners():
    img = macapp.draw_icon(256)
    assert img.size == (256, 256)
    assert img.getpixel((2, 2))[3] == 0 and img.getpixel((128, 128))[3] == 255


def test_launcher_runs_the_desktop_module_in_process():
    assert '"-m", "phomemo.desktop"' in macapp.LAUNCHER
    assert "PYTHONEXECUTABLE" in macapp.LAUNCHER      # how Python finds the venv
    assert "Py_BytesMain" in macapp.LAUNCHER          # embedded: no second Dock icon


def _framework_python() -> bool:
    try:
        macapp.python_framework()
        return True
    except ValueError:
        return False


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("clang") or not _framework_python(),
                    reason="builds a macOS app against a framework Python")
def test_make_app_builds_a_signed_native_bundle(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "webview", types.ModuleType("webview"))   # not needed to build
    app = macapp.make_app(tmp_path)
    info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleIdentifier"] == macapp.BUNDLE_ID
    assert info["CFBundleExecutable"] == "Thermal" and "NSBluetoothAlwaysUsageDescription" in info
    exe = app / "Contents" / "MacOS" / "Thermal"
    links = subprocess.run(["otool", "-L", str(exe)], capture_output=True, text=True).stdout
    assert ".framework/Versions/" in links                    # Python embedded, not exec'd
    assert (app / "Contents" / "Resources" / "Thermal.icns").stat().st_size > 10_000
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)


def test_make_app_without_pywebview_says_how_to_install(tmp_path, monkeypatch):
    if sys.platform != "darwin":
        pytest.skip("macOS only")
    monkeypatch.setitem(sys.modules, "webview", None)          # import fails
    with pytest.raises(ValueError, match=r"\.\[app\]"):
        macapp.make_app(tmp_path)


# -- the backup: Thermal Web.app, the page in a browser tab -------------------------

def test_web_script_quotes_paths_and_never_persists_state():
    src = macapp.applescript(Path("/Users/a b/proj/.venv/bin/phomemo"), port=8700)
    assert 'quoted form of "/Users/a b/proj/.venv/bin/phomemo"' in src
    assert "--port 8700 --idle-exit 180" in src
    assert "[u]i --no-open --from-app --port 8700" in src     # pkill can't match itself
    assert "< /dev/null &" in src                            # do shell script returns at once
    assert "property " not in src and "global " not in src   # would rewrite main.scpt


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("osacompile"),
                    reason="builds a macOS app")
def test_make_web_app_is_separate_from_thermal(tmp_path, monkeypatch):
    monkeypatch.setattr(macapp, "phomemo_command", lambda: Path("/usr/bin/true"))
    app = macapp.make_web_app(tmp_path, port=8701)
    assert app.name == "Thermal Web.app"
    info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleIdentifier"] == macapp.WEB_BUNDLE_ID != macapp.BUNDLE_ID
    assert "NSBluetoothAlwaysUsageDescription" in info
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
