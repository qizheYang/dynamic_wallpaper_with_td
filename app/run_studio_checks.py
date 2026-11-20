#!/usr/bin/env python3
"""Run focused Studio admission/callback regressions without opening app windows.

    python3 app/run_studio_checks.py

This uses real Studio/Core bodies, not XCTest or a UI automation session. Only
the generated Studio copies' WallpaperCore imports are removed for single-module
compilation. The busy panel guard is checked before execution so its removal
fails safely instead of opening a picker. Screen tests select an absent display.
AppKit may inspect the current session/screens, but no NSApplication event loop,
window, TD process, provider request, or Keychain write is requested.
"""
import hashlib
import json
from pathlib import Path
import platform
import re
import subprocess
import sys

sys.dont_write_bytecode = True
from run_checks import mask_swift, run_bounded

APP = Path(__file__).resolve().parent
BUILD = APP / "build" / "studio-checks"


def main():
    if platform.system() != "Darwin": raise ValueError("Studio lifecycle checks require macOS")
    studio = APP / "Sources" / "WallpaperStudio"
    paths = [studio / name for name in ("StudioModel.swift", "StudioPreferences.swift", "SceneDesktop.swift")]
    model_code = mask_swift(paths[0].read_text(encoding="utf-8"))
    chooser = re.search(r"\bfunc\s+chooseBundle\s*\(\s*\)\s*\{", model_code)
    if chooser is None or not re.match(r"\s*guard\s+!busy\s+else\s*\{\s*return\s*\}", model_code[chooser.end():]):
        raise ValueError("chooseBundle no longer starts with its busy guard; refusing any possible picker invocation")
    BUILD.mkdir(parents=True, exist_ok=True)
    copies, inventory = [], []
    for path in paths:
        source = path.read_text(encoding="utf-8")
        transformed, count = re.subn(r"(?m)^import WallpaperCore[ \t]*$", "// Core sources compile in the same module.", source)
        if count != 1: raise ValueError(f"{path.name}: expected exactly one WallpaperCore import")
        copy = BUILD / ("StudioCheckCopy_" + path.name)
        copy.write_text(f"#sourceLocation(file: {json.dumps(str(path))}, line: 1)\n" + transformed, encoding="utf-8")
        copies.append(copy)
        inventory.append({"file": str(path.relative_to(APP)), "sha256": hashlib.sha256(source.encode()).hexdigest()})
    (BUILD / "source-hashes.json").write_text(json.dumps(inventory, indent=2) + "\n")
    core = sorted((APP / "Sources" / "WallpaperCore").rglob("*.swift"))
    if not core: raise ValueError("Core sources are missing")
    arch = {"arm64": "arm64", "x86_64": "x86_64"}.get(platform.machine())
    if arch is None: raise ValueError("Unsupported host architecture")
    binary = BUILD / "studio-lifecycle-checks"
    command = ["xcrun", "swiftc", "-swift-version", "5", "-target", arch + "-apple-macosx13.0",
               "-module-cache-path", str(APP / "build" / "studio-check-cache"), "-parse-as-library",
               *map(str, core), *map(str, copies), str(APP / "TestSupport" / "StudioLifecycleChecks.swift"),
               "-o", str(binary)]
    for framework in ("AppKit", "AVFoundation", "IOKit", "Security"):
        command.extend(["-framework", framework])
    print("Compiling focused windowless Studio lifecycle checks…", flush=True)
    if run_bounded(command, 300) != 0: return 1
    return run_bounded([str(binary)], 30)


if __name__ == "__main__":
    try: sys.exit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print("Studio checks failed: " + str(error), file=sys.stderr); sys.exit(1)
    except KeyboardInterrupt:
        print("Studio checks cancelled", file=sys.stderr); sys.exit(130)
