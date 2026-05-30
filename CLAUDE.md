# CLAUDE.md — TuyaOpen-esp32

## What This Repo Is

Local clone of the TuyaOpen ESP32 platform adapter. Used here as a **builder of
vendor libs** that get staged into the Arduino `tuya_open` hardware package at
`D:\Symlinks\Users\Linearch\AppData\Local\Arduino15\packages\tuya_open\tools\vendor-esp32\0.0.5\`.
The Arduino-side firmware lives at `E:\Arduino\ac_automation_esp32`.

## Vendor Lib Build Flow

`build_psram_variants.py` is the entry point. For each variant in `VARIANTS`:

1. Validates `sdkconfig_esp32<suffix>` has the right PSRAM speed (or no PSRAM).
2. Cleans `tuya_open_sdk/build` + app `.build`.
3. Runs `tos.py build` in `apps/tuya_cloud/switch_demo` via TuyaOpen venv,
   with `TUYAOPEN_SDKCONFIG_SUFFIX` set so the right sdkconfig preset is used.
4. Snapshots build output into `.psram_snapshots/<variant>/`:
   - `idf-libs/` — `build/esp-idf/<comp>/*.a`
   - `tuya-libs/` — `<app>/.build/lib/*.a`
   - `sdkconfig.h`
   - `ld/` — all `.ld` linker scripts under build dir.

After all variants build, `apply_to_vendor` diffs libs across variants and stages:

- Shared libs (identical across variants) → `vendor/libs/<name>.a`
- Differing libs → `vendor/libs/<stem>_<variant>.a` (e.g. `_40m.a`, `_80m.a`)
- `sdkconfig.h` for base variant → `platform/.../config/sdkconfig.h`
- `sdkconfig.h` for non-base variants → `platform/.../config_<variant>/sdkconfig.h`
- Linker scripts → `link_path/`
- Manifest → `libs/VARIANTS.json`

`sync_idf_prebuilt_blobs` separately mirrors closed-source IDF blobs
(`libbtdm_app.a`, `libphy.a`, `librtc.a`, `libcoexist.a`, `libnet80211.a`, ...)
from `DEFAULT_IDF_ROOT/components/{bt,esp_coex,esp_phy,esp_wifi}\...\esp32\*.a`
into `vendor/link_path/esp-idf/<same-relpath>/`. These are NOT produced by the
component build but ARE referenced as `-l<name>` in `libs_flags.txt`. ABI
mismatch with freshly built libs caused BLE controller failures.

**`DEFAULT_IDF_ROOT` MUST be `E:\github\TuyaOpen-esp32\esp-idf`** (TuyaOpen's
pinned IDF, commit `67c1de1e`), not `E:\github\esp-idf` (release/v5.4 newer).
The newer branch removed/renamed `esp_wifi_sta_get_reset_param_internal` and
`esp_wifi_sta_set_reset_param_internal`, which `libwpa_supplicant.a` from this
build still calls. Mixing the two causes wpa_supplicant undefined symbols at
firmware link time.

**Blobs must be mirrored into BOTH `link_path/` and `libs/`.** `boards.txt`
puts `-L<vendor>/libs` first in `esp32.compiler.flags.libs`, so any stale copy
in `libs/<name>.a` (left over from the original 0.0.5 archive's blob set)
shadows the up-to-date copy in `link_path/`. Affected names: `libnet80211.a`,
`libcoexist.a`, `libcore.a`, `libespnow.a`, `libmesh.a`, `libpp.a`,
`libsmartconfig.a`, `libwapi.a`, `libbtdm_app.a`, `libphy.a`, `librtc.a`.
`sync_idf_prebuilt_blobs` now writes both destinations when a same-named copy
already exists under `libs/`.

### Single-Variant Staging Fix

`compute_lib_plan` skips variants whose snapshot dirs don't exist. Without
this, when only `no_psram` is built, every staged lib gets the `_no_psram`
suffix and the bare-name copies are deleted — but `libs_flags.txt` links via
bare `-l<name>`, so the firmware fails to link. With the skip, single-variant
builds treat every lib as shared (bare-named only).

### Temporary State

Both build loops in `main()` have `if v["name"] != "no_psram": continue` —
only the `no_psram` variant is built/staged. The 40m/80m variants are skipped
until needed. Remove the gate to re-enable PSRAM variant builds.

### tos.py Prompt Auto-Answer

`tos.py build` interactively prompts on platform-commit mismatch (TuyaOpen
pin vs user's working tree). The script pipes `"n\n"` into stdin so the prompt
falls through. Without this, `build_psram_variants.py` hangs at the prompt.

### TuyaOpen venv Requirement

`tos.py` imports `cli_monitor`, which imports `serial`. The TuyaOpen venv at
`E:\github\TuyaOpen\.venv` must have `pyserial` installed or `tos.py build`
fails on import.

## Historical Note: libspi_flash.a False-Positive Bisect

An earlier bisect (2026-05-30) appeared to show that rebuilding `libspi_flash.a`
caused `rst:0x10 (RTCWDT_RTC_RESET) at __esp_system_init_fn_init_flash
(0x400e3e90)`, and `EXCLUDED_VENDOR_LIBS` was set to `{"libspi_flash.a"}` to
ship the original archive copy. That finding was a **false positive** — the
real blockers were:

1. `DEFAULT_IDF_ROOT` pointing at `E:\github\esp-idf` (release/v5.4 newer)
   instead of TuyaOpen's pinned IDF (`67c1de1e` at
   `E:\github\TuyaOpen-esp32\esp-idf`) → wpa_supplicant ABI mismatch around
   `esp_wifi_sta_get/set_reset_param_internal`.
2. Stale `libs/*.a` WiFi blobs shadowing `link_path/` copies because
   `boards.txt` puts `-L<vendor>/libs` first in `esp32.compiler.flags.libs`.

After both were fixed, swapping the rebuilt `libspi_flash.a` (`23D63522...`)
into `vendor/libs/` and reflashing booted cleanly with the same loop pattern.
`EXCLUDED_VENDOR_LIBS` is now empty; the mechanism remains as an escape hatch
if a future lib genuinely needs to stay as the archive copy.

## Other Required Vendor Patches

These are NOT applied by the script — must be done once by hand on the vendor
dir at `D:\Symlinks\...\vendor-esp32\0.0.5\`:

1. **`platform/ESP32/esp-idf/components/lwip/port/include/lwipopts.h:969`** —
   guard `LWIP_COMPAT_SOCKETS` so the package-level `-DLWIP_COMPAT_SOCKETS=1`
   define wins:
   ```c
   #ifndef LWIP_COMPAT_SOCKETS
   #define LWIP_COMPAT_SOCKETS 0
   #endif
   ```
   Without this, `recv/send/getpeername` decl conflicts break the Arduino-side
   compile.

2. **Bootloader** at `packager-tools/bootloader.bin` should match the pre-rebuild
   hash `A7E0B5D0A2BCDDA3E54F816570CEBD214604826CFFFC75FCEC51A7A6DB3702E3`.
   The freshly rebuilt bootloader (`93F6C49B...`) and the original archive
   bootloader (`75953D4B...`) both boot, but the pre-rebuild one is treated as
   canonical here.

## Required TuyaOpen Patches (Upstream)

These live in `E:\github\TuyaOpen` (the parent repo), not here, but are needed
for `tos.py build` to succeed against the `switch_demo` app:

1. **`boards/ESP32/ESP32/Kconfig`** — remove `select ENABLE_ESP_DISPLAY`.
   Upstream commit `e92da875` made the ESP32 board unconditionally select the
   display stack, but `switch_demo` doesn't enable LVGL → build fails on
   missing `lcd_st7789_spi.h` / `xl9555.h`.
2. **`boards/ESP32/ESP32/esp32_bread_board.c`** — wrap `lcd_st7789_spi.h`,
   `xl9555.h` includes and all `board_display_*` calls in
   `#if defined(CONFIG_ENABLE_ESP_DISPLAY)`. Same root cause as above.

## ESP-IDF / Env Quirks

- `toolchain_file.cmake:1-6` falls back to `~/.espressif` when `IDF_PATH` env
  is set. On Windows that path is unusable; unset before running builds:
  - cmd: `set IDF_PATH=` and `set IDF_TOOLS_PATH=`
  - PowerShell: `$env:IDF_PATH = $null; $env:IDF_TOOLS_PATH = $null`
- The helper script does call `tos.py build` in `switch_demo`, so the `~`
  branch will trigger if `IDF_PATH` is set.

<!-- rtk-instructions v2 -->
# RTK (Rust Token Killer) - Token-Optimized Commands

## Golden Rule

**Always prefix commands with `rtk`**. If RTK has a dedicated filter, it uses it. If not, it passes through unchanged. This means RTK is always safe to use.

**Important**: Even in command chains with `&&`, use `rtk`:
```bash
# ❌ Wrong
git add . && git commit -m "msg" && git push

# ✅ Correct
rtk git add . && rtk git commit -m "msg" && rtk git push
```

## RTK Commands by Workflow

### Build & Compile (80-90% savings)
```bash
rtk cargo build         # Cargo build output
rtk cargo check         # Cargo check output
rtk cargo clippy        # Clippy warnings grouped by file (80%)
rtk tsc                 # TypeScript errors grouped by file/code (83%)
rtk lint                # ESLint/Biome violations grouped (84%)
rtk prettier --check    # Files needing format only (70%)
rtk next build          # Next.js build with route metrics (87%)
```

### Test (60-99% savings)
```bash
rtk cargo test          # Cargo test failures only (90%)
rtk go test             # Go test failures only (90%)
rtk jest                # Jest failures only (99.5%)
rtk vitest              # Vitest failures only (99.5%)
rtk playwright test     # Playwright failures only (94%)
rtk pytest              # Python test failures only (90%)
rtk rake test           # Ruby test failures only (90%)
rtk rspec               # RSpec test failures only (60%)
rtk test <cmd>          # Generic test wrapper - failures only
```

### Git (59-80% savings)
```bash
rtk git status          # Compact status
rtk git log             # Compact log (works with all git flags)
rtk git diff            # Compact diff (80%)
rtk git show            # Compact show (80%)
rtk git add             # Ultra-compact confirmations (59%)
rtk git commit          # Ultra-compact confirmations (59%)
rtk git push            # Ultra-compact confirmations
rtk git pull            # Ultra-compact confirmations
rtk git branch          # Compact branch list
rtk git fetch           # Compact fetch
rtk git stash           # Compact stash
rtk git worktree        # Compact worktree
```

Note: Git passthrough works for ALL subcommands, even those not explicitly listed.

### GitHub (26-87% savings)
```bash
rtk gh pr view <num>    # Compact PR view (87%)
rtk gh pr checks        # Compact PR checks (79%)
rtk gh run list         # Compact workflow runs (82%)
rtk gh issue list       # Compact issue list (80%)
rtk gh api              # Compact API responses (26%)
```

### JavaScript/TypeScript Tooling (70-90% savings)
```bash
rtk pnpm list           # Compact dependency tree (70%)
rtk pnpm outdated       # Compact outdated packages (80%)
rtk pnpm install        # Compact install output (90%)
rtk npm run <script>    # Compact npm script output
rtk npx <cmd>           # Compact npx command output
rtk prisma              # Prisma without ASCII art (88%)
```

### Files & Search (60-75% savings)
```bash
rtk ls <path>           # Tree format, compact (65%)
rtk read <file>         # Code reading with filtering (60%)
rtk grep <pattern>      # Search grouped by file (75%)
rtk find <pattern>      # Find grouped by directory (70%)
```

### Analysis & Debug (70-90% savings)
```bash
rtk err <cmd>           # Filter errors only from any command
rtk log <file>          # Deduplicated logs with counts
rtk json <file>         # JSON structure without values
rtk deps                # Dependency overview
rtk env                 # Environment variables compact
rtk summary <cmd>       # Smart summary of command output
rtk diff                # Ultra-compact diffs
```

### Infrastructure (85% savings)
```bash
rtk docker ps           # Compact container list
rtk docker images       # Compact image list
rtk docker logs <c>     # Deduplicated logs
rtk kubectl get         # Compact resource list
rtk kubectl logs        # Deduplicated pod logs
```

### Network (65-70% savings)
```bash
rtk curl <url>          # Compact HTTP responses (70%)
rtk wget <url>          # Compact download output (65%)
```

### Meta Commands
```bash
rtk gain                # View token savings statistics
rtk gain --history      # View command history with savings
rtk discover            # Analyze Claude Code sessions for missed RTK usage
rtk proxy <cmd>         # Run command without filtering (for debugging)
rtk init                # Add RTK instructions to CLAUDE.md
rtk init --global       # Add RTK to ~/.claude/CLAUDE.md
```

## Token Savings Overview

| Category | Commands | Typical Savings |
|----------|----------|-----------------|
| Tests | vitest, playwright, cargo test | 90-99% |
| Build | next, tsc, lint, prettier | 70-87% |
| Git | status, log, diff, add, commit | 59-80% |
| GitHub | gh pr, gh run, gh issue | 26-87% |
| Package Managers | pnpm, npm, npx | 70-90% |
| Files | ls, read, grep, find | 60-75% |
| Infrastructure | docker, kubectl | 85% |
| Network | curl, wget | 65-70% |

Overall average: **60-90% token reduction** on common development operations.
<!-- /rtk-instructions -->
