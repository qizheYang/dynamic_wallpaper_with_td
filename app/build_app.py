#!/usr/bin/env python3
"""Build a local native .app, including a versioned on-demand authoring kit."""
import argparse
import hashlib
import json
import os
import pathlib
import plistlib
import shutil
import subprocess

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent


def build(configuration="release", demo_bundle=None):
    output = HERE / "build"
    output.mkdir(exist_ok=True)
    command = ["xcrun", "swift", "build", "--package-path", str(HERE),
               "--scratch-path", str(output / "swift"), "--cache-path", str(output / "cache"),
               "--config-path", str(output / "config"), "--security-path", str(output / "security"),
               "--disable-sandbox", "-c", configuration]
    if configuration == "release":
        command += ["-debug-info-format", "none"]
    environment = dict(os.environ, CLANG_MODULE_CACHE_PATH=str(output / "clang-cache"),
                       SWIFTPM_MODULECACHE_OVERRIDE=str(output / "module-cache"))
    subprocess.run(command, check=True, env=environment)
    binary_dir = pathlib.Path(subprocess.check_output(command + ["--show-bin-path"], text=True, env=environment).strip())
    app = output / "Wallpaper Studio.app"
    staging = output / "Wallpaper Studio.staging.app"
    if staging.exists():
        shutil.rmtree(staging)
    executable = staging / "Contents/MacOS"
    resources = staging / "Contents/Resources"
    executable.mkdir(parents=True)
    resources.mkdir()
    if demo_bundle:
        demo = pathlib.Path(demo_bundle).resolve()
        if not all((demo / name).is_file() for name in ("manifest.json", "loop.mp4", "cover.png", "frames.sha256.txt")):
            raise ValueError("demo bundle must be a completed pipeline bundle")
        destination = resources / "Demo"
        destination.mkdir()
        for name in ("manifest.json", "loop.mp4", "cover.png", "frames.sha256.txt"):
            shutil.copy2(demo / name, destination / name)
    shutil.copy2(binary_dir / "WallpaperStudio", executable / "WallpaperStudio")
    authoring = resources / "Authoring"
    authoring.mkdir()
    for name in ("pipeline", "skills/td-wallpaper", "jobs", "td/controller", "td/shaders", "td/calibration", "plan"):
        source = REPO / name
        destination = authoring / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
    for name in ("td/run_td.py", "td/wallpaper_template.toe", "td/README.md", "README.md", "AGENTS.md",
                 "CLAUDE.md", "app/README.md", "agents_shared/README.md",
                 "research/05-performance-feasibility.md", "research/06-interactive-agent-platform.md",
                 "research/benchmarks/README.md"):
        destination = authoring / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / name, destination)
    inventory = {str(p.relative_to(authoring)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in sorted(authoring.rglob("*")) if p.is_file()}
    kit_id = hashlib.sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest()
    (resources / "authoring-version.json").write_text(json.dumps({"id": kit_id, "files": inventory}, indent=2) + "\n")
    with (staging / "Contents/Info.plist").open("wb") as handle:
        plistlib.dump({"CFBundleExecutable": "WallpaperStudio", "CFBundleIdentifier": "io.github.qizheYang.WallpaperStudio",
                      "CFBundleName": "Wallpaper Studio", "CFBundleDisplayName": "Wallpaper Studio",
                      "CFBundlePackageType": "APPL", "CFBundleShortVersionString": "0.1.0",
                      "CFBundleVersion": "1", "LSMinimumSystemVersion": "13.0", "LSUIElement": True,
                      "NSHighResolutionCapable": True, "NSPrincipalClass": "NSApplication"}, handle)
    subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(staging)], check=True)
    if app.exists():
        shutil.rmtree(app)
    staging.rename(app)
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configuration", choices=("release", "debug"), default="release")
    parser.add_argument("--demo-bundle", type=pathlib.Path, help="optional local validated example; not a public redistribution grant")
    args = parser.parse_args()
    print(build(args.configuration, args.demo_bundle))
