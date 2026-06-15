#!/usr/bin/env python3

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys


CHIP = "esp32"
DEFAULT_OUT = (
    r"D:\Symlinks\Users\Linearch\AppData\Local\Arduino15\packages"
    r"\tuya_open\tools\vendor-esp32\0.0.5"
)
DEFAULT_TUYAOPEN_ROOT = r"E:\github\TuyaOpen"
DEFAULT_APP_RELPATH = r"apps\tuya_cloud\switch_demo"
DEFAULT_IDF_ROOT = r"E:\github\TuyaOpen-esp32\esp-idf"
SNAPSHOT_NAME = "default"

IDF_PREBUILT_BLOB_DIRS = [
    os.path.join("components", "bt", "controller", "lib_esp32", "esp32"),
    os.path.join("components", "esp_coex", "lib", "esp32"),
    os.path.join("components", "esp_phy", "lib", "esp32"),
    os.path.join("components", "esp_wifi", "lib", "esp32"),
]


def copy_file(source, target, force=True):
    if not os.path.exists(source):
        print(f"Not found [{source}].")
        return False
    if not force and os.path.exists(target):
        return True
    target_dir = os.path.dirname(target)
    if target_dir:
        os.makedirs(target_dir, exist_ok=True)
    shutil.copy(source, target)
    return True


def rm_rf(file_path):
    if os.path.isfile(file_path):
        os.remove(file_path)
    elif os.path.isdir(file_path):
        shutil.rmtree(file_path)
    return True


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def collect_idf_libs(build_dir):
    out = {}
    idf_dir = os.path.join(build_dir, "esp-idf")
    if not os.path.isdir(idf_dir):
        return out
    for comp in os.listdir(idf_dir):
        comp_path = os.path.join(idf_dir, comp)
        if not os.path.isdir(comp_path):
            continue
        for f in os.listdir(comp_path):
            if f.endswith(".a"):
                out[f] = os.path.join(comp_path, f)
    return out


def collect_dir_libs(lib_dir):
    out = {}
    if not os.path.isdir(lib_dir):
        return out
    for f in os.listdir(lib_dir):
        if f.endswith(".a"):
            out[f] = os.path.join(lib_dir, f)
    return out


def collect_ld_scripts(root_dir):
    found = []
    for dirpath, _, files in os.walk(root_dir):
        for f in files:
            if f.endswith(".ld"):
                abspath = os.path.join(dirpath, f)
                found.append((os.path.relpath(abspath, root_dir), abspath))
    return found


def clean_build_dirs(tuya_open_esp32_root, app_dir):
    rm_rf(os.path.join(tuya_open_esp32_root, "tuya_open_sdk", "build"))
    rm_rf(os.path.join(tuya_open_esp32_root, "tuya_open_sdk", "sdkconfig"))
    rm_rf(os.path.join(tuya_open_esp32_root, "tuya_open_sdk", "sdkconfig.old"))
    rm_rf(os.path.join(tuya_open_esp32_root, "tuya_open_sdk", "sdkconfig.defaults"))
    rm_rf(os.path.join(app_dir, ".build"))
    rm_rf(os.path.join(tuya_open_esp32_root, ".app"))
    rm_rf(os.path.join(tuya_open_esp32_root, ".target"))


def run_tos_build(tuyaopen_root, app_dir):
    venv_python = os.path.join(tuyaopen_root, ".venv", "Scripts", "python.exe")
    tos = os.path.join(tuyaopen_root, "tos.py")
    if not os.path.isfile(venv_python):
        print(f"Error: TuyaOpen venv missing at {venv_python}")
        print("       Run 'export.bat' once in the TuyaOpen root to create it.")
        return False
    env = os.environ.copy()
    env["OPEN_SDK_ROOT"] = tuyaopen_root
    env["OPEN_SDK_PYTHON"] = venv_python
    env["OPEN_SDK_PIP"] = os.path.join(tuyaopen_root, ".venv", "Scripts", "pip.exe")
    env.pop("TUYAOPEN_SDKCONFIG_SUFFIX", None)
    print(f"  tos.py build in {app_dir}")
    result = subprocess.run([venv_python, tos, "build"], cwd=app_dir, env=env, input="n\n", text=True)
    return result.returncode == 0


def snapshot_build(tuya_open_esp32_root, app_dir, snapshots_dir):
    snap_root = os.path.join(snapshots_dir, SNAPSHOT_NAME)
    rm_rf(snap_root)
    os.makedirs(snap_root, exist_ok=True)

    idf_build = os.path.join(tuya_open_esp32_root, "tuya_open_sdk", "build")
    snap_idf_libs = os.path.join(snap_root, "idf-libs")
    os.makedirs(snap_idf_libs, exist_ok=True)
    for name, src in collect_idf_libs(idf_build).items():
        copy_file(src, os.path.join(snap_idf_libs, name))

    tuya_libs = os.path.join(app_dir, ".build", "lib")
    snap_tuya_libs = os.path.join(snap_root, "tuya-libs")
    os.makedirs(snap_tuya_libs, exist_ok=True)
    for name, src in collect_dir_libs(tuya_libs).items():
        copy_file(src, os.path.join(snap_tuya_libs, name))

    sdkconfig_h = os.path.join(idf_build, "config", "sdkconfig.h")
    if os.path.isfile(sdkconfig_h):
        copy_file(sdkconfig_h, os.path.join(snap_root, "sdkconfig.h"))

    snap_ld = os.path.join(snap_root, "ld")
    for relpath, src in collect_ld_scripts(idf_build):
        copy_file(src, os.path.join(snap_ld, relpath))

    print(f"  snapshot: idf-libs={len(os.listdir(snap_idf_libs))} tuya-libs={len(os.listdir(snap_tuya_libs))} -> {snap_root}")
    return True


def validate_spiram_disabled(sdkconfig_path):
    if not os.path.isfile(sdkconfig_path):
        print(f"Error: missing {sdkconfig_path}")
        return False
    with open(sdkconfig_path, "r", encoding="utf-8") as f:
        text = f.read()
    if "CONFIG_SPIRAM=y" in text or "CONFIG_SPIRAM_BOOT_INIT=y" in text:
        print(f"Error: {sdkconfig_path} has SPIRAM enabled")
        return False
    return True


def apply_to_vendor(snapshots_dir, vendor_dir, dry_run=False):
    snap_root = os.path.join(snapshots_dir, SNAPSHOT_NAME)
    idf_libs = collect_dir_libs(os.path.join(snap_root, "idf-libs"))
    tuya_libs = collect_dir_libs(os.path.join(snap_root, "tuya-libs"))
    libs = {}
    libs.update(idf_libs)
    libs.update(tuya_libs)

    libs_dst = os.path.join(vendor_dir, "libs")
    link_dst_root = os.path.join(vendor_dir, "link_path")
    platform_build = os.path.join(vendor_dir, "platform", "ESP32", "tuya_open_sdk", "build")

    actions = [("copy", src, os.path.join(libs_dst, name)) for name, src in sorted(libs.items())]

    ld_root = os.path.join(snap_root, "ld")
    if os.path.isdir(ld_root):
        for relpath, src in collect_ld_scripts(ld_root):
            actions.append(("copy", src, os.path.join(link_dst_root, relpath)))

    sdkconfig_h = os.path.join(snap_root, "sdkconfig.h")
    if os.path.isfile(sdkconfig_h):
        actions.append(("copy", sdkconfig_h, os.path.join(platform_build, "config", "sdkconfig.h")))

    manifest = {
        "build": SNAPSHOT_NAME,
        "idf_lib_count": len(idf_libs),
        "tuya_lib_count": len(tuya_libs),
        "lib_count": len(libs),
        "libs": sorted(libs.keys()),
    }
    actions.append(("write", os.path.join(libs_dst, "VENDOR_LIBS.json"), json.dumps(manifest, indent=2)))

    print(f"\n=== Plan: {len(actions)} actions ===")
    print(json.dumps({k: manifest[k] for k in ("build", "idf_lib_count", "tuya_lib_count", "lib_count")}, indent=2))

    if dry_run:
        for action in actions:
            print(" ", action[0], "->", action[-1])
        return manifest

    copied = written = 0
    for action in actions:
        if action[0] == "copy":
            _, src, dst = action
            copy_file(src, dst)
            copied += 1
        elif action[0] == "write":
            _, dst, content = action
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "w", encoding="utf-8") as f:
                f.write(content)
            written += 1
    print(f"\nCopied: {copied}, Wrote: {written}")
    return manifest


def sync_idf_prebuilt_blobs(idf_root, vendor_dir, dry_run=False):
    actions = []
    for blob_dir_rel in IDF_PREBUILT_BLOB_DIRS:
        src_dir = os.path.join(idf_root, blob_dir_rel)
        if not os.path.isdir(src_dir):
            print(f"WARN: IDF prebuilt blob dir missing: {src_dir}")
            continue
        dst_dir = os.path.join(vendor_dir, "link_path", "esp-idf", blob_dir_rel)
        for f in os.listdir(src_dir):
            if f.endswith(".a"):
                actions.append((os.path.join(src_dir, f), os.path.join(dst_dir, f)))

    libs_dst = os.path.join(vendor_dir, "libs")
    if os.path.isdir(libs_dst):
        for src, _ in list(actions):
            mirror = os.path.join(libs_dst, os.path.basename(src))
            if os.path.isfile(mirror):
                actions.append((src, mirror))

    print(f"\n=== IDF prebuilt blob sync: {len(actions)} files ===")
    if dry_run:
        for src, dst in actions:
            print(f"  {src} -> {dst}")
        return

    copied = unchanged = 0
    for src, dst in actions:
        if os.path.isfile(dst) and os.path.getsize(dst) == os.path.getsize(src) and sha256_file(dst) == sha256_file(src):
            unchanged += 1
            continue
        copy_file(src, dst)
        copied += 1
    print(f"Blobs copied: {copied}, unchanged: {unchanged}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=DEFAULT_OUT, help="vendor-esp32/0.0.5 dir")
    ap.add_argument("--tuyaopen-root", default=DEFAULT_TUYAOPEN_ROOT, help="TuyaOpen repo root")
    ap.add_argument("--idf-root", default=DEFAULT_IDF_ROOT, help="esp-idf root for prebuilt-blob sync")
    ap.add_argument("--app", default=DEFAULT_APP_RELPATH, help="App relpath under tuyaopen-root")
    ap.add_argument("--skip-build", action="store_true", help="Reuse existing .vendor_lib_snapshot")
    ap.add_argument("--skip-blob-sync", action="store_true", help="Skip IDF prebuilt-blob mirror step")
    ap.add_argument("--dry-run", action="store_true", help="Print plan without modifying files")
    args = ap.parse_args()

    root = os.path.dirname(os.path.abspath(__file__))
    snapshots = os.path.join(root, ".vendor_lib_snapshot")
    app_dir = os.path.join(args.tuyaopen_root, args.app)

    if not args.skip_build:
        if not os.path.isdir(app_dir):
            print(f"Error: app dir missing: {app_dir}")
            sys.exit(1)
        source_sdkconfig = os.path.join(root, "tuya_open_sdk", f"sdkconfig_{CHIP}")
        if not validate_spiram_disabled(source_sdkconfig):
            sys.exit(1)
        rm_rf(snapshots)
        print("\n=== Building vendor libs ===")
        clean_build_dirs(root, app_dir)
        if not run_tos_build(args.tuyaopen_root, app_dir):
            print("Error: tos.py build failed")
            sys.exit(1)
        if not snapshot_build(root, app_dir, snapshots):
            sys.exit(1)
    else:
        snap = os.path.join(snapshots, SNAPSHOT_NAME)
        if not os.path.isdir(snap):
            print(f"Error: --skip-build but snapshot missing: {snap}")
            sys.exit(1)

    snapshot_sdkconfig = os.path.join(snapshots, SNAPSHOT_NAME, "sdkconfig.h")
    if not validate_spiram_disabled(snapshot_sdkconfig):
        sys.exit(1)

    if not os.path.exists(args.out):
        print(f"Error: --out does not exist: {args.out}")
        sys.exit(1)
    try:
        args.out = os.path.realpath(args.out)
    except OSError:
        pass

    print(f"\n=== Staging into {args.out} ===")
    apply_to_vendor(snapshots, args.out, dry_run=args.dry_run)

    if not args.skip_blob_sync:
        sync_idf_prebuilt_blobs(args.idf_root, args.out, dry_run=args.dry_run)

    print("\nDone.")


if __name__ == "__main__":
    main()
