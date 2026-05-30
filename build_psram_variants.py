#!/usr/bin/env python3
# coding=utf-8
"""
Build PSRAM variants for ESP32 via the conventional TuyaOpen build flow
(tos.py build in apps/tuya_cloud/switch_demo) and stage all produced libs
into the Arduino vendor-esp32 layout.

One invocation:
  for variant in [40m, 80m]:
    1. set TUYAOPEN_SDKCONFIG_SUFFIX=_psram<variant>
    2. clean build dirs (tuya_open_sdk/build + switch_demo/.build)
    3. run `tos.py build` in switch_demo (uses TuyaOpen venv)
    4. snapshot:
         - tuya_open_sdk/build/esp-idf/<comp>/*.a   (ESP-IDF libs)
         - switch_demo/.build/lib/*.a               (TuyaOpen libs)
         - tuya_open_sdk/build/config/sdkconfig.h
         - **/*.ld                                  (linker scripts)
    5. validate the generated sdkconfig.h matches the requested speed

After both variants, stage into vendor-esp32/0.0.5:
  - byte-diff each .a; shared libs keep their bare name, differing ones
    get a _40m / _80m suffix
  - 40m sdkconfig.h -> platform/.../config/sdkconfig.h (default)
  - 80m sdkconfig.h -> platform/.../config_80m/sdkconfig.h (menu override)
  - linker scripts -> link_path/
  - manifest at libs/VARIANTS.json

Usage:
  python build_psram_variants.py
  python build_psram_variants.py --skip-build      # reuse snapshots
  python build_psram_variants.py --dry-run         # plan only
  python build_psram_variants.py --out <dir>       # override target
  python build_psram_variants.py --app <relpath>   # different tuyaopen app
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys

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

CHIP = "esp32"

DEFAULT_OUT = (
    r"D:\Symlinks\Users\Linearch\AppData\Local\Arduino15\packages"
    r"\tuya_open\tools\vendor-esp32\0.0.5"
)
DEFAULT_TUYAOPEN_ROOT = r"E:\github\TuyaOpen"
DEFAULT_APP_RELPATH = r"apps\tuya_cloud\switch_demo"
DEFAULT_IDF_ROOT = r"E:\github\TuyaOpen-esp32\esp-idf"

# Closed-source prebuilt blobs shipped in the IDF source tree — NOT produced by
# the component build, so the snapshot collector misses them. Mirror each
# directory's *.a into vendor/link_path/<same-relpath>/. Listed paths are
# relative to the IDF root.
IDF_PREBUILT_BLOB_DIRS = [
    os.path.join("components", "bt", "controller", "lib_esp32", "esp32"),
    os.path.join("components", "esp_coex", "lib", "esp32"),
    os.path.join("components", "esp_phy", "lib", "esp32"),
    os.path.join("components", "esp_wifi", "lib", "esp32"),
]

VARIANTS = [
    {"name": "no_psram", "suffix": ""},
    {"name": "40m", "suffix": "_psram40"},
    {"name": "80m", "suffix": "_psram80"},
]
BASE_VARIANT = "no_psram"  # the variant whose sdkconfig.h + link scripts become default

EXCLUDED_VENDOR_LIBS: set[str] = set()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def collect_idf_libs(build_dir):
    """build/esp-idf/<comp>/*.a -> dict basename -> abs path"""
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
    """<dir>/*.a -> dict basename -> abs path (flat)"""
    out = {}
    if not os.path.isdir(lib_dir):
        return out
    for f in os.listdir(lib_dir):
        if f.endswith(".a"):
            out[f] = os.path.join(lib_dir, f)
    return out


def collect_ld_scripts(root_dir):
    """Walk for *.ld -> list of (relpath_from_root, abspath)"""
    found = []
    for dirpath, _, files in os.walk(root_dir):
        for f in files:
            if f.endswith(".ld"):
                abspath = os.path.join(dirpath, f)
                found.append((os.path.relpath(abspath, root_dir), abspath))
    return found


def clean_build_dirs(tuya_open_esp32_root, app_dir):
    """Wipe build artifacts for a clean variant build."""
    rm_rf(os.path.join(tuya_open_esp32_root, "tuya_open_sdk", "build"))
    rm_rf(os.path.join(tuya_open_esp32_root, "tuya_open_sdk", "sdkconfig"))
    rm_rf(os.path.join(tuya_open_esp32_root, "tuya_open_sdk", "sdkconfig.old"))
    rm_rf(os.path.join(tuya_open_esp32_root, "tuya_open_sdk", "sdkconfig.defaults"))
    rm_rf(os.path.join(app_dir, ".build"))
    rm_rf(os.path.join(tuya_open_esp32_root, ".app"))
    rm_rf(os.path.join(tuya_open_esp32_root, ".target"))


def run_tos_build(tuyaopen_root, app_dir, variant):
    """Invoke `tos.py build` in app_dir using TuyaOpen's venv python."""
    venv_python = os.path.join(tuyaopen_root, ".venv", "Scripts", "python.exe")
    tos = os.path.join(tuyaopen_root, "tos.py")
    if not os.path.isfile(venv_python):
        print(f"Error: TuyaOpen venv missing at {venv_python}")
        print("       Run 'export.bat' once in the TuyaOpen root to create it.")
        return False
    env = os.environ.copy()
    env["OPEN_SDK_ROOT"] = tuyaopen_root
    env["OPEN_SDK_PYTHON"] = venv_python
    env["OPEN_SDK_PIP"] = os.path.join(
        tuyaopen_root, ".venv", "Scripts", "pip.exe"
    )
    env["TUYAOPEN_SDKCONFIG_SUFFIX"] = variant["suffix"]
    print(f"  tos.py build in {app_dir} with {variant['suffix']}")
    result = subprocess.run(
        [venv_python, tos, "build"],
        cwd=app_dir,
        env=env,
        input="n\n",
        text=True,
    )
    return result.returncode == 0


def snapshot_variant(tuya_open_esp32_root, app_dir, variant, snapshots_dir):
    """Capture build output into snapshots/<variant>/."""
    snap_root = os.path.join(snapshots_dir, variant["name"])
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

    print(f"  snapshot: idf-libs={len(os.listdir(snap_idf_libs))} "
          f"tuya-libs={len(os.listdir(snap_tuya_libs))} -> {snap_root}")
    return True


def validate_snapshot_speed(snapshots_dir, variant):
    sdkconfig_h = os.path.join(snapshots_dir, variant["name"], "sdkconfig.h")
    if not os.path.isfile(sdkconfig_h):
        print(f"Error: missing snapshot sdkconfig.h for {variant['name']}")
        return False
    with open(sdkconfig_h, "r", encoding="utf-8") as f:
        text = f.read()
    if variant["name"] == "no_psram":
        if "CONFIG_SPIRAM" in text and "#define CONFIG_SPIRAM " not in [l.strip() for l in text.split("\n") if l.strip().startswith("#define CONFIG_SPIRAM")]:
            pass  # CONFIG_SPIRAM is not set
        elif "#define CONFIG_SPIRAM" not in text:
            pass  # no CONFIG_SPIRAM define at all
        else:
            print(f"Error: snapshot {variant['name']} has CONFIG_SPIRAM enabled (should be disabled)")
            return False
        return True
    expected = "80" if variant["name"] == "80m" else "40"
    if f"#define CONFIG_SPIRAM_SPEED {expected}" not in text:
        print(f"Error: snapshot {variant['name']} did not use {expected}MHz PSRAM config")
        return False
    return True


def validate_source_speed(tuya_open_esp32_root, variant):
    expected = "80" if variant["name"] == "80m" else "40"
    is_no_psram = variant["name"] == "no_psram"
    sdkconfig = os.path.join(
        tuya_open_esp32_root,
        "tuya_open_sdk",
        f"sdkconfig_{CHIP}{variant['suffix']}",
    )
    if not os.path.isfile(sdkconfig):
        print(f"Error: missing {sdkconfig}")
        return False
    with open(sdkconfig, "r", encoding="utf-8") as f:
        text = f.read()
    if is_no_psram:
        if "CONFIG_SPIRAM=y" in text or "CONFIG_SPIRAM_BOOT_INIT=y" in text:
            print(f"Error: {sdkconfig} has CONFIG_SPIRAM enabled (should be disabled)")
            return False
        return True
    if f"CONFIG_SPIRAM_SPEED={expected}" not in text:
        print(f"Error: {sdkconfig} is not configured for {expected}MHz")
        return False
    if expected == "80" and "CONFIG_ESPTOOLPY_FLASHFREQ_80M=y" not in text:
        print("Error: 80MHz PSRAM requires CONFIG_ESPTOOLPY_FLASHFREQ_80M=y")
        return False
    return True


def compute_lib_plan(snapshots_dir):
    """
    Combine idf-libs + tuya-libs per variant, diff across variants.
    Returns: shared dict, per_variant dict, summary.

    Only variants whose snapshot directory actually exists are considered.
    Without this, a missing variant counts as "different" against every lib in
    other variants and forces all libs to get a variant suffix — breaking the
    bare-name link path. This matters when the build loop is temporarily gated
    to a single variant.
    """
    variant_libs = {}
    for v in VARIANTS:
        vname = v["name"]
        snap = os.path.join(snapshots_dir, vname)
        if not os.path.isdir(snap):
            continue
        merged = {}
        merged.update(collect_dir_libs(os.path.join(snap, "idf-libs")))
        merged.update(collect_dir_libs(os.path.join(snap, "tuya-libs")))
        variant_libs[vname] = merged

    all_names = set()
    for libs in variant_libs.values():
        all_names.update(libs.keys())

    base = variant_libs[BASE_VARIANT]
    differing = set()
    for name in all_names:
        shas = []
        for vname, libs in variant_libs.items():
            shas.append(sha256_file(libs[name]) if name in libs else None)
        if len(set(shas)) > 1:
            differing.add(name)

    shared = {}
    for name in sorted(all_names - differing):
        src = base.get(name)
        if src is None:
            for vname, libs in variant_libs.items():
                if name in libs:
                    src = libs[name]
                    break
        shared[name] = src

    per_variant = {v["name"]: {} for v in VARIANTS}
    for name in sorted(differing):
        stem = name[:-2]  # strip .a
        for v in VARIANTS:
            libs = variant_libs[v["name"]]
            if name in libs:
                per_variant[v["name"]][f"{stem}_{v['name']}.a"] = libs[name]

    summary = {
        "variants": [v["name"] for v in VARIANTS],
        "shared_lib_count": len(shared),
        "differing_lib_count": len(differing),
        "differing_libs": sorted(differing),
    }
    return shared, per_variant, summary


def apply_to_vendor(snapshots_dir, vendor_dir, dry_run=False):
    shared, per_variant, summary = compute_lib_plan(snapshots_dir)

    libs_dst = os.path.join(vendor_dir, "libs")
    link_dst_root = os.path.join(vendor_dir, "link_path")
    platform_build = os.path.join(
        vendor_dir, "platform", "ESP32", "tuya_open_sdk", "build"
    )

    actions = []

    skipped = []
    for name, src in sorted(shared.items()):
        if name in EXCLUDED_VENDOR_LIBS:
            skipped.append(name)
            continue
        actions.append(("copy", src, os.path.join(libs_dst, name)))

    for vname, libs in per_variant.items():
        for new_name, src in sorted(libs.items()):
            stem = new_name.rsplit(f"_{vname}.a", 1)[0]
            if f"{stem}.a" in EXCLUDED_VENDOR_LIBS:
                skipped.append(new_name)
                continue
            actions.append(("copy", src, os.path.join(libs_dst, new_name)))
            actions.append((
                "delete-if-exists", os.path.join(libs_dst, f"{stem}.a")
            ))
    if skipped:
        print(f"Excluded from staging (kept original archive copy): {sorted(skipped)}")

    base_snap = os.path.join(snapshots_dir, BASE_VARIANT)
    base_ld = os.path.join(base_snap, "ld")
    if os.path.isdir(base_ld):
        for relpath, src in collect_ld_scripts(base_ld):
            actions.append((
                "copy", src, os.path.join(link_dst_root, relpath)
            ))

    for v in VARIANTS:
        src = os.path.join(snapshots_dir, v["name"], "sdkconfig.h")
        if not os.path.isfile(src):
            print(f"WARN: missing sdkconfig.h for {v['name']}")
            continue
        if v["name"] == BASE_VARIANT:
            dst = os.path.join(platform_build, "config", "sdkconfig.h")
        else:
            dst = os.path.join(platform_build, f"config_{v['name']}", "sdkconfig.h")
        actions.append(("copy", src, dst))

    actions.append((
        "write",
        os.path.join(libs_dst, "VARIANTS.json"),
        json.dumps(summary, indent=2),
    ))

    print(f"\n=== Plan: {len(actions)} actions ===")
    print(json.dumps(summary, indent=2))

    if dry_run:
        for a in actions:
            print(" ", a[0], "->", a[-1] if a[0] != "delete-if-exists" else a[1])
        return summary

    copied = deleted = written = 0
    for a in actions:
        if a[0] == "copy":
            _, src, dst = a
            copy_file(src, dst)
            copied += 1
        elif a[0] == "delete-if-exists":
            _, path = a
            if os.path.exists(path):
                os.remove(path)
                deleted += 1
        elif a[0] == "write":
            _, dst, content = a
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "w", encoding="utf-8") as f:
                f.write(content)
            written += 1
    print(f"\nCopied: {copied}, Deleted: {deleted}, Wrote: {written}")
    return summary


def sync_idf_prebuilt_blobs(idf_root, vendor_dir, dry_run=False):
    """Mirror IDF prebuilt closed-source blobs into vendor/link_path/.

    These (libbtdm_app.a, libphy.a, librtc.a, libcoexist.a, libnet80211.a, ...)
    ship as binaries in esp-idf/components/ and are referenced via -lbtdm_app /
    -lphy / etc. in libs_flags.txt. The IDF *component* build doesn't recompile
    them, so they're not in build/esp-idf/<comp>/*.a and the snapshot collector
    misses them. They must be staged from the IDF source tree directly.

    Staleness here = ABI mismatch with the freshly-built component libs, which
    is what caused the BLE controller failure observed 2026-05-28.
    """
    actions = []
    for blob_dir_rel in IDF_PREBUILT_BLOB_DIRS:
        src_dir = os.path.join(idf_root, blob_dir_rel)
        if not os.path.isdir(src_dir):
            print(f"WARN: IDF prebuilt blob dir missing: {src_dir}")
            continue
        dst_dir = os.path.join(vendor_dir, "link_path", "esp-idf", blob_dir_rel)
        for f in os.listdir(src_dir):
            if not f.endswith(".a"):
                continue
            actions.append((os.path.join(src_dir, f), os.path.join(dst_dir, f)))

    # Some blob names (libnet80211.a, libcoexist.a, libcore.a, libespnow.a,
    # libmesh.a, libpp.a, libsmartconfig.a, libwapi.a, libbtdm_app.a, libphy.a,
    # librtc.a) ALSO exist under vendor/libs/. -L<vendor>/libs comes first in
    # boards.txt esp32.compiler.flags.libs, so a stale copy in libs/ would
    # shadow the up-to-date blob in link_path/. Overwrite libs/ copies too.
    libs_dst = os.path.join(vendor_dir, "libs")
    libs_mirror_actions = []
    if os.path.isdir(libs_dst):
        for src, _ in actions:
            mirror = os.path.join(libs_dst, os.path.basename(src))
            if os.path.isfile(mirror):
                libs_mirror_actions.append((src, mirror))

    all_actions = actions + libs_mirror_actions
    print(f"\n=== IDF prebuilt blob sync: {len(all_actions)} files "
          f"({len(actions)} link_path + {len(libs_mirror_actions)} libs/ mirror) ===")
    if dry_run:
        for src, dst in all_actions:
            print(f"  {src} -> {dst}")
        return
    copied = unchanged = 0
    for src, dst in all_actions:
        if (os.path.isfile(dst)
                and os.path.getsize(dst) == os.path.getsize(src)
                and sha256_file(dst) == sha256_file(src)):
            unchanged += 1
            continue
        copy_file(src, dst)
        copied += 1
    print(f"Blobs copied: {copied}, unchanged: {unchanged}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=DEFAULT_OUT, help="vendor-esp32/0.0.5 dir")
    ap.add_argument("--tuyaopen-root", default=DEFAULT_TUYAOPEN_ROOT,
                    help="TuyaOpen repo root (default: %(default)s)")
    ap.add_argument("--idf-root", default=DEFAULT_IDF_ROOT,
                    help="esp-idf root for prebuilt-blob sync (default: %(default)s)")
    ap.add_argument("--app", default=DEFAULT_APP_RELPATH,
                    help="App relpath under tuyaopen-root (default: %(default)s)")
    ap.add_argument("--skip-build", action="store_true",
                    help="Reuse existing .psram_snapshots/")
    ap.add_argument("--skip-blob-sync", action="store_true",
                    help="Skip IDF prebuilt-blob mirror step")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print plan without modifying files")
    args = ap.parse_args()

    root = os.path.dirname(os.path.abspath(__file__))
    snapshots = os.path.join(root, ".psram_snapshots")
    app_dir = os.path.join(args.tuyaopen_root, args.app)

    if not args.skip_build:
        if not os.path.isdir(app_dir):
            print(f"Error: app dir missing: {app_dir}")
            sys.exit(1)

        rm_rf(snapshots)

        for v in VARIANTS:
            if v["name"] != "no_psram": continue  # TEMP: only rebuild no_psram
            print(f"\n=== Building variant {v['name']} ===")
            if not validate_source_speed(root, v):
                sys.exit(1)
            clean_build_dirs(root, app_dir)
            if not run_tos_build(args.tuyaopen_root, app_dir, v):
                print(f"Error: tos.py build failed for variant {v['name']}")
                sys.exit(1)
            if not snapshot_variant(root, app_dir, v, snapshots):
                sys.exit(1)
            if not validate_snapshot_speed(snapshots, v):
                sys.exit(1)
    else:
        for v in VARIANTS:
            if v["name"] != "no_psram": continue  # TEMP: only rebuild no_psram
            snap = os.path.join(snapshots, v["name"])
            if not os.path.isdir(snap):
                print(f"Error: --skip-build but snapshot missing: {snap}")
                sys.exit(1)
            if not validate_snapshot_speed(snapshots, v):
                sys.exit(1)

    if not os.path.exists(args.out):
        print(f"Error: --out does not exist: {args.out}")
        sys.exit(1)
    try:
        args.out = os.path.realpath(args.out)
    except OSError:
        # Windows nested-junction edge case (WinError 649). Use as-is.
        pass

    print(f"\n=== Staging into {args.out} ===")
    apply_to_vendor(snapshots, args.out, dry_run=args.dry_run)

    if not args.skip_blob_sync:
        sync_idf_prebuilt_blobs(args.idf_root, args.out, dry_run=args.dry_run)

    print("\nDone.")


if __name__ == "__main__":
    main()
