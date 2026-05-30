#!/usr/bin/env python3
"""
Flash TuyaOpen switch_demo binary to ESP32 via tyutool_cli.
Usage:
  python flash_demo.py              # flash 80m (default)
  python flash_demo.py 40m          # flash 40m variant
  python flash_demo.py --port COM6  # override port
  python flash_demo.py --erase      # erase flash first
"""

import argparse
import os
import subprocess
import sys
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TUYAOPEN_ROOT = r"E:\github\TuyaOpen"
TUYAOPEN_ESP32_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", ".."))

TOOL_ROOTS = [
    r"D:\Symlinks\Users\Linearch\AppData\Local\Arduino15\packages\tuya_open\tools",
    r"C:\Users\Linearch\AppData\Local\Arduino15\packages\tuya_open\tools",
]


def first_existing(*relative_parts):
    for root in TOOL_ROOTS:
        path = os.path.join(root, *relative_parts)
        if os.path.isfile(path):
            return path
    return os.path.join(TOOL_ROOTS[0], *relative_parts)


TYUTOOL = first_existing("tyutool", "2.1.0", "tyutool_cli.exe")
ESPTOOL = first_existing("vendor-esp32", "0.0.5", "packager-tools", "windows", "esptool.exe")

APP_DIR = os.path.join(TUYAOPEN_ROOT, "apps", "tuya_cloud", "switch_demo")
DIST_DIR = os.path.join(APP_DIR, "dist")
VARIANT_SUFFIX = {"40m": "_psram40", "80m": "_psram80"}


def run_command(cmd, dry_run=False, cwd=None, env=None):
    print(f"Running: {' '.join(cmd)}")
    if dry_run:
        return 0
    return subprocess.run(cmd, cwd=cwd, env=env).returncode


def build_variant(variant, dry_run=False):
    venv_python = os.path.join(TUYAOPEN_ROOT, ".venv", "Scripts", "python.exe")
    tos = os.path.join(TUYAOPEN_ROOT, "tos.py")
    if not os.path.isfile(venv_python):
        print(f"ERROR: TuyaOpen venv missing: {venv_python}")
        return 1
    if not os.path.isfile(tos):
        print(f"ERROR: tos.py missing: {tos}")
        return 1
    env = os.environ.copy()
    env["OPEN_SDK_ROOT"] = TUYAOPEN_ROOT
    env["OPEN_SDK_PYTHON"] = venv_python
    env["OPEN_SDK_PIP"] = os.path.join(TUYAOPEN_ROOT, ".venv", "Scripts", "pip.exe")
    env["TUYAOPEN_SDKCONFIG_SUFFIX"] = VARIANT_SUFFIX[variant]
    return run_command([venv_python, tos, "build"], dry_run=dry_run, cwd=APP_DIR, env=env)


def find_binary(variant):
    """Find the QIO binary for the given variant.

    The dist directory only has the LAST build's output.
    For 80m: last build is 80m (default from build_psram_variants.py).
    For 40m: need to rebuild, or use snapshot.
    """
    qio = os.path.join(DIST_DIR, "switch_demo_1.0.0", "switch_demo_QIO_1.0.0.bin")
    if not os.path.isfile(qio):
        # Try flat dist dir
        qio = os.path.join(DIST_DIR, "switch_demo_QIO_1.0.0.bin")

    # Check snapshot directory for variant-specific binaries
    snap = os.path.join(TUYAOPEN_ESP32_ROOT, ".psram_snapshots", variant)
    snap_qio = os.path.join(snap, "switch_demo_QIO_1.0.0.bin")
    if os.path.isfile(snap_qio):
        return snap_qio

    if os.path.isfile(qio):
        return qio

    return None


def find_bootloader():
    """Find bootloader binary."""
    bl = os.path.join(DIST_DIR, "switch_demo_1.0.0", "bootloader.bin")
    if not os.path.isfile(bl):
        bl = os.path.join(DIST_DIR, "bootloader.bin")
    return bl if os.path.isfile(bl) else None


def find_partition_table():
    """Find partition table binary."""
    pt = os.path.join(DIST_DIR, "switch_demo_1.0.0", "partition-table.bin")
    if not os.path.isfile(pt):
        pt = os.path.join(DIST_DIR, "partition-table.bin")
    return pt if os.path.isfile(pt) else None


def find_app_bin():
    """Find app binary (without bootloader)."""
    app = os.path.join(DIST_DIR, "switch_demo_1.0.0", "switch_demo.bin")
    if not os.path.isfile(app):
        app = os.path.join(DIST_DIR, "switch_demo.bin")
    return app if os.path.isfile(app) else None


def flash_tyutool(binary, port, baud, dry_run=False):
    """Flash using tyutool_cli (same tool Arduino IDE uses)."""
    cmd = [
        TYUTOOL, "-n", "write",
        "-d", "esp32",
        "-p", port,
        "-b", str(baud),
        "-s", "0x000000",
        "-f", binary,
        "--tqdm",
    ]
    return run_command(cmd, dry_run=dry_run)


def flash_esptool_combined(binary, port, baud, dry_run=False):
    """Flash combined QIO binary using esptool."""
    cmd = [
        sys.executable if not os.path.isfile(ESPTOOL) else ESPTOOL,
        "--chip", "esp32",
        "--port", port,
        "--baud", str(baud),
        "write_flash",
        "-z",
        "0x0",
        binary,
    ]
    return run_command(cmd, dry_run=dry_run)


def flash_esptool_parts(bootloader, partition_table, app, port, baud, dry_run=False):
    """Flash individual parts using esptool."""
    esptool = ESPTOOL
    cmd_parts = [
        ("bootloader", "0x1000", bootloader),
        ("partition-table", "0x8000", partition_table),
        ("app", "0x20000", app),
    ]
    for name, offset, binary in cmd_parts:
        if binary is None:
            print(f"SKIP {name}: binary not found")
            continue
        cmd = [
            esptool,
            "--chip", "esp32",
            "--port", port,
            "--baud", str(baud),
            "write_flash",
            offset,
            binary,
        ]
        print(f"\nFlashing {name}")
        ret = run_command(cmd, dry_run=dry_run)
        if ret != 0:
            print(f"FAILED flashing {name}")
            return ret
    return 0


def erase_flash(port, baud, dry_run=False):
    """Erase entire flash."""
    cmd = [ESPTOOL, "--chip", "esp32", "--port", port, "--baud", str(baud), "erase_flash"]
    print("Erasing flash")
    return run_command(cmd, dry_run=dry_run)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("variant", nargs="?", default="80m", choices=["40m", "80m"],
                    help="PSRAM variant (default: 80m)")
    ap.add_argument("--port", default="COM6", help="Serial port (default: COM6)")
    ap.add_argument("--baud", type=int, default=921600, help="Baud rate (default: 921600)")
    ap.add_argument("--erase", action="store_true", help="Erase flash before writing")
    ap.add_argument("--build", action="store_true", help="Run tos.py build for this variant before flashing")
    ap.add_argument("--dry-run", action="store_true", help="Print commands without building/flashing")
    ap.add_argument("--esptool", action="store_true", help="Use esptool instead of tyutool")
    ap.add_argument("--parts", action="store_true", help="Flash individual parts instead of QIO binary")
    args = ap.parse_args()

    if not os.path.isfile(TYUTOOL):
        print(f"tyutool not found: {TYUTOOL}")
        print("Falling back to esptool")
        args.esptool = True

    if args.build:
        ret = build_variant(args.variant, dry_run=args.dry_run)
        if ret != 0:
            print(f"Build failed for {args.variant}")
            sys.exit(ret)

    if args.erase:
        ret = erase_flash(args.port, args.baud, dry_run=args.dry_run)
        if ret != 0:
            print("Erase failed, continuing anyway...")

    if args.parts:
        bootloader = find_bootloader()
        partition_table = find_partition_table()
        app = find_app_bin()
        print(f"Bootloader: {bootloader}")
        print(f"Partition table: {partition_table}")
        print(f"App: {app}")
        if app is None:
            print("ERROR: No app binary found")
            sys.exit(1)
        ret = flash_esptool_parts(bootloader, partition_table, app, args.port, args.baud, dry_run=args.dry_run)
    else:
        binary = find_binary(args.variant)
        if binary is None:
            print(f"ERROR: No QIO binary found for variant '{args.variant}'")
            print(f"Looked in: {DIST_DIR}")
            print(f"  and: {os.path.join(TUYAOPEN_ESP32_ROOT, '.psram_snapshots', args.variant)}")
            print(f"\nTo build, run: python {os.path.join(TUYAOPEN_ESP32_ROOT, 'build_psram_variants.py')}")
            sys.exit(1)

        size_mb = os.path.getsize(binary) / (1024 * 1024)
        print(f"Binary: {binary} ({size_mb:.1f} MB)")
        print(f"Variant: {args.variant}, Port: {args.port}, Baud: {args.baud}")

        if args.esptool:
            ret = flash_esptool_combined(binary, args.port, args.baud, dry_run=args.dry_run)
        else:
            ret = flash_tyutool(binary, args.port, args.baud, dry_run=args.dry_run)

    if ret == 0:
        print("\nDone! Open serial monitor at 115200 baud to see boot output.")
    else:
        print(f"\nFAILED with exit code {ret}")
    sys.exit(ret)


if __name__ == "__main__":
    main()
