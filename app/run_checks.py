#!/usr/bin/env python3
"""Run the same core test bodies on macOS Command Line Tools without XCTest.

    python3 app/run_checks.py

Normal XCTest test sources are retained. Generated copies replace only imports;
PortableAssertions.swift supplies the limited assertion/setup API used here.
This runner is explicitly NOT XCTest: it supports serial zero-argument methods,
sync/async + throws, and per-instance setup/teardown. Unsupported declarations or
assertion APIs fail instead of disappearing. Current tests use injected network
fixtures and bounded local process fixtures; no GUI, TD or provider API is run.
Build files and test-source hashes stay under ignored app/build/portable-checks.
"""
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import signal
import subprocess
import sys

APP = Path(__file__).resolve().parent
TESTS = APP / "Tests" / "WallpaperCoreTests"
BUILD = APP / "build" / "portable-checks"
ASSERTIONS = APP / "TestSupport" / "PortableAssertions.swift"


def mask_swift(source):
    """Mask comments/literals while preserving offsets, newlines and code braces."""
    masked = list(source)

    def erase(start, end):
        masked[start:end] = ["\n" if c == "\n" else " " for c in source[start:end]]

    def comment_end(i):
        if source.startswith("//", i):
            end = source.find("\n", i)
            return len(source) if end < 0 else end
        depth, j = 1, i + 2
        while j < len(source):
            if source.startswith("/*", j): depth += 1; j += 2
            elif source.startswith("*/", j):
                depth -= 1; j += 2
                if depth == 0: return j
            else: j += 1
        raise ValueError("Unclosed Swift comment")

    def string_start(i):
        j = i
        while j < len(source) and source[j] == "#": j += 1
        if j < len(source) and source[j] == '"':
            return j - i, 3 if source.startswith('"""', j) else 1
        return None

    def interpolation_end(i):
        depth = 1
        while i < len(source):
            if source.startswith(("//", "/*"), i): i = comment_end(i); continue
            literal = string_start(i)
            if literal: i = string_end(i, *literal); continue
            if source[i] == "(": depth += 1
            elif source[i] == ")":
                depth -= 1
                if depth == 0: return i + 1
            i += 1
        raise ValueError("Unclosed Swift interpolation")

    def string_end(start, hashes, quotes):
        end_token, escape = '"' * quotes + "#" * hashes, "\\" + "#" * hashes
        i = start + hashes + quotes
        while i < len(source):
            if source.startswith(end_token, i): return i + len(end_token)
            if source.startswith(escape + "(", i): i = interpolation_end(i + len(escape) + 1)
            elif source.startswith(escape, i): i += len(escape) + 1
            else: i += 1
        raise ValueError("Unclosed Swift string")

    i = 0
    while i < len(source):
        if source.startswith(("//", "/*"), i):
            end = comment_end(i); erase(i, end); i = end; continue
        literal = string_start(i)
        if literal:
            end = string_end(i, *literal); erase(i, end); i = end; continue
        i += 1
    return "".join(masked)


def discover(path):
    source = path.read_text(encoding="utf-8")
    code = mask_swift(source)
    if re.search(r"#\s*(?:if|elseif|else|endif)\b|@Test\b|@objc\b|\bXCTSkip\b|\bfunc\s+`test", code):
        raise ValueError(f"{path.name}: conditional/discovery/skip constructs are unsupported")
    supported = set(re.findall(r"\bfunc\s+(XCT\w+)\b", ASSERTIONS.read_text()))
    used = set(re.findall(r"\b(XCT\w+)\s*\(", code)) - {"XCTestCase"}
    if used - supported:
        raise ValueError(f"{path.name}: unsupported assertion APIs {sorted(used - supported)}")
    stack, closing, depths = [], {}, []
    for index, char in enumerate(code):
        depths.append(len(stack))
        if char == "{": stack.append(index)
        elif char == "}":
            if not stack: raise ValueError(f"{path.name}: unbalanced braces")
            closing[stack.pop()] = index
    if stack: raise ValueError(f"{path.name}: unbalanced braces")
    classes = list(re.finditer(r"\bclass\s+(\w+)\s*:\s*XCTestCase\s*\{", code))
    if len(classes) != len(re.findall(r"\bXCTestCase\b", code)):
        raise ValueError(f"{path.name}: unsupported XCTestCase declaration")
    cases = []
    declarations = list(re.finditer(r"\bfunc\s+(test\w*)\b", code))
    for declaration in declarations:
        owners = [c for c in classes if c.end() - 1 < declaration.start() < closing[c.end() - 1]
                  and depths[declaration.start()] == depths[c.end() - 1] + 1]
        signature = re.match(r"\s*\(\s*\)\s*(async\s+)?(throws\s+)?(?:->\s*Void\s*)?\{", code[declaration.end():])
        if len(owners) != 1 or not signature:
            raise ValueError(f"{path.name}: unsupported/unowned test {declaration.group(1)}")
        line_start = code.rfind("\n", 0, declaration.start()) + 1
        if re.search(r"\b(?:static|class|private|fileprivate)\b", code[line_start:declaration.start()]):
            raise ValueError(f"{path.name}: test must be an accessible instance method")
        cases.append((owners[0].group(1), declaration.group(1), bool(signature.group(1)), bool(signature.group(2))))
    if not classes or not cases or any(not any(t[0] == c.group(1) for t in cases) for c in classes):
        raise ValueError(f"{path.name}: empty test file/class, refusing silent omission")
    if len({(c, m) for c, m, _, _ in cases}) != len(cases):
        raise ValueError(f"{path.name}: duplicate test methods")
    for hook in re.finditer(r"\bfunc\s+(setUp\w*|tearDown\w*)\b", code):
        line_start = code.rfind("\n", 0, hook.start()) + 1
        if re.search(r"\b(?:static|class)\b", code[line_start:hook.start()]):
            raise ValueError(f"{path.name}: class lifecycle hooks are unsupported")
        if not re.match(r"\s*\(\s*\)\s*(?:throws\s+)?\{", code[hook.end():]):
            raise ValueError(f"{path.name}: unsupported lifecycle hook {hook.group(1)}")
        if hook.group(1) not in {"setUp", "setUpWithError", "tearDown", "tearDownWithError"}:
            raise ValueError(f"{path.name}: unknown lifecycle hook")
    # Imports are the only edits to generated copies; preserve source line numbers.
    transformed, count = re.subn(r"(?m)^[ \t]*import XCTest[ \t]*$", "import Foundation", source)
    if count != 1: raise ValueError(f"{path.name}: expected exactly one XCTest import")
    transformed, count = re.subn(r"(?m)^@testable import WallpaperCore[ \t]*$", "// Core sources compile in this same module.", transformed)
    if count != 1: raise ValueError(f"{path.name}: expected exactly one WallpaperCore import")
    return source, transformed, cases


def run_bounded(command, timeout):
    process = subprocess.Popen(command, cwd=APP.parent, start_new_session=True)
    try:
        return process.wait(timeout=timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        # The new process group belongs only to this compile/test invocation.
        try: os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError: pass
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            process.wait(timeout=5)
        raise


def main():
    if platform.system() != "Darwin": raise ValueError("These macOS core checks require macOS")
    paths = sorted(TESTS.glob("*.swift"))
    if not paths: raise ValueError("No test sources found")
    BUILD.mkdir(parents=True, exist_ok=True)
    cases, copies, manifest = [], [], []
    for path in paths:
        source, transformed, discovered = discover(path)
        # Distinct basename avoids Swift's #fileID collision warning while mapping
        # assertion diagnostics back to the original (unmodified) source lines.
        copy = BUILD / ("PortableCopy_" + path.name)
        copy.write_text(f"#sourceLocation(file: {json.dumps(str(path))}, line: 1)\n" + transformed, encoding="utf-8")
        copies.append(copy); cases.extend(discovered)
        manifest.append({"file": str(path.relative_to(APP)), "sha256": hashlib.sha256(source.encode()).hexdigest(),
                         "tests": [f"{c}.{m}" for c, m, _, _ in discovered]})
    if len({(c, m) for c, m, _, _ in cases}) != len(cases): raise ValueError("Duplicate test identifiers across files")
    driver = ["import Foundation", "@main struct PortableCheckMain {", "@MainActor static func main() async {",
              'print("Running original test bodies with the portable CLT assertion adapter; not XCTest.")']
    for cls, method, asynchronous, throwing in cases:
        prefix = ("try " if throwing else "") + ("await " if asynchronous else "")
        driver.extend(["do {", f"let instance = {cls}()",
                       f'await PortableCheckResults.shared.run("{cls}.{method}", instance: instance) {{',
                       f"{prefix}instance.{method}()", "}", "}"])
    driver.extend(["exit(PortableCheckResults.shared.finish())", "}", "}"])
    entry = BUILD / "PortableCheckMain.swift"
    entry.write_text("\n".join(driver) + "\n", encoding="utf-8")
    (BUILD / "discovery.json").write_text(json.dumps(manifest, indent=2) + "\n")
    core = sorted((APP / "Sources" / "WallpaperCore").rglob("*.swift"))
    if not core: raise ValueError("No core Swift sources found")
    arch = {"arm64": "arm64", "x86_64": "x86_64"}.get(platform.machine())
    if arch is None: raise ValueError("Unsupported host architecture")
    executable = BUILD / "portable-checks"
    command = ["xcrun", "swiftc", "-swift-version", "5", "-target", arch + "-apple-macosx13.0",
               "-module-cache-path", str(APP / "build" / "check-cache"), "-parse-as-library",
               *map(str, core), str(ASSERTIONS), *map(str, copies), str(entry), "-o", str(executable)]
    for framework in ("AppKit", "AVFoundation", "IOKit", "Security"):
        command.extend(["-framework", framework])
    print(f"Discovered {len(cases)} tests in {len(paths)} files; compiling unchanged test bodies…", flush=True)
    if run_bounded(command, 300) != 0: return 1
    return run_bounded([str(executable)], 120)


if __name__ == "__main__":
    try: sys.exit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print("Portable checks failed: " + str(error), file=sys.stderr); sys.exit(1)
    except KeyboardInterrupt:
        print("Portable checks cancelled", file=sys.stderr); sys.exit(130)
