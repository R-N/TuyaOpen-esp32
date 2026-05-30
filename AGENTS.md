# AGENTS.md — TuyaOpen-esp32

## What This Repo Is

Sub-repository of [tuyaopen](https://github.com/tuya/tuyaopen). **Cannot compile standalone.** Downloaded automatically by tuyaopen toolchain. Provides the ESP32 platform adaptation layer (HAL/adapter) for TuyaOpen SDK.

Locally also used as the **builder of vendor libs** consumed by the Arduino `tuya_open` hardware package at `D:\Symlinks\Users\Linearch\AppData\Local\Arduino15\packages\tuya_open\tools\vendor-esp32\0.0.5\`. See `build_psram_variants.py` and `CLAUDE.md` for the staging flow.

## Architecture

```
tuya_open_sdk/           # ESP-IDF project (the actual build target)
  main/                  # Entry: main.c → app_main() → tuya_app_main()
  tuyaos_adapter/         # TuyaOpen → ESP-IDF HAL bridge
    src/drivers/          # tkl_* driver implementations (wifi, bt, gpio, uart, flash, etc.)
    src/system/            # tkl_* OS primitives (thread, semaphore, mutex, memory, etc.)
    src/audio/             # Audio frontend (VAD, KWS)
    include/              # tkl_* headers + tuya utility headers
  sdkconfig_esp32*        # Per-chip sdkconfig presets (copied → sdkconfig.defaults at build)
  partitions_*.csv        # Flash partition tables (4M/8M/16M)
  build/                  # Build output (gitignored)
esp-idf/                  # ESP-IDF v5.4 checkout (gitignored, downloaded by platform_prepare.py)
tools/                   # Python build/prepare scripts
  prepare.py             # Downloads ESP-IDF + installs target toolchain
  util.py                # idf.py wrapper, mirror setup (JihuLab for China)
  idf_tools.py           # Patched IDF tools script
```

## Build System

- **Build system**: ESP-IDF CMake (`idf.py build`). Not a standard CMake project.
- **Build entry**: `python build_example.py <build_param_dir> build`
- **Setup (first time)**: `python build_setup.py <project_name> <platform> <framework> <chip>`
  - Downloads ESP-IDF v5.4 (shallow clone) into `esp-idf/`
  - Installs target toolchain into `.espressif/`
  - Supported chips: `esp32`, `esp32c3`, `esp32s3`, `esp32c6`
- **Clean**: `python build_example.py <build_param_dir> clean`

### Build Flow

1. `platform_prepare.py` → clones ESP-IDF, runs `install.sh <target>`
2. `build_setup.py` → copies chip-specific `sdkconfig_esp32*` → `sdkconfig.defaults`, runs `idf.py set-target <chip>`
3. `build_example.py` → sets env vars from `build_param.json`, selects partitions CSV, runs `idf.py build`, merges binaries with `esptool merge_bin`, copies output artifacts

### Key Env Vars (set by build scripts)

- `IDF_PATH` → `<root>/esp-idf`
- `IDF_TOOLS_PATH` → `<root>/.espressif`
- `BUILD_PARAM_DIR` → path containing `build_param.json` + `build_param.cmake`
- `TUYAOS_HEADER_DIR`, `TUYAOS_LIBS_DIR`, `TUYAOS_LIBS`, `TUYAOS_BOARD_PATH` → set from build_param

## Chip-Specific Notes

- **ESP32-S3**: Has two flash variants — `sdkconfig_esp32s3_uart` and `sdkconfig_esp32s3_usb_jtag`. Suffix selected by `CONFIG_ENABLE_ESP32S3_USB_JTAG_ONLY`.
- Partition table chosen by flash size: 4M (default), 8M, 16M — controlled by `CONFIG_PLATFORM_FLASHSIZE_*` in `default.config`.

## Conventions

- **Driver naming**: All HAL implementations follow `tkl_<peripheral>.c` pattern (e.g., `tkl_wifi.c`, `tkl_gpio.c`).
- **Header layout**: `tuyaos_adapter/include/<peripheral>/tkl_<peripheral>.h`
- **sdkconfig management**: Never edit `sdkconfig` directly. Edit `sdkconfig_esp32*` presets. Build scripts copy the right one → `sdkconfig.defaults`, then `idf.py set-target` regenerates `sdkconfig`.
- **Partitions**: `partitions.csv` is generated (gitignored) from `partitions_4M.csv` / `partitions_8M.csv` / `partitions_16M.csv` depending on flash config.
- **Build artifacts**: All in `tuya_open_sdk/build/` — gitignored. Merged binary named `<app_name>_QIO_<version>.bin`.

## What Not To Do

- Don't try to build standalone. Use tuyaopen parent repo or build scripts.
- Don't edit `sdkconfig` or `sdkconfig.defaults` directly — they're generated at build time.
- Don't edit `partitions.csv` directly — it's generated from size-specific CSVs.
- Don't commit `esp-idf/` or `.espressif/` — they're gitignored and downloaded fresh.
- Don't commit `managed_components/` — ESP-IDF component dependencies resolved at build time from `idf_component.yml`.

## Vendor Lib Staging (`build_psram_variants.py`)

Builds the `no_psram` / `40m` / `80m` PSRAM variants by re-running `tos.py build` in `apps/tuya_cloud/switch_demo` with `TUYAOPEN_SDKCONFIG_SUFFIX` set per variant, snapshots produced `.a` files + `sdkconfig.h` + `.ld` scripts into `.psram_snapshots/<variant>/`, then diffs across variants and stages into the Arduino vendor dir.

- `VARIANTS` + `BASE_VARIANT` control the variant matrix and which variant's sdkconfig.h/linker scripts become the default.
- `EXCLUDED_VENDOR_LIBS` skips named libs during staging. **Currently empty.** A 2026-05-30 bisect appeared to identify `libspi_flash.a` as a `RTCWDT_RTC_RESET` cause at `__esp_system_init_fn_init_flash` (`0x400e3e90`) and added it to the set, but that was a false positive — the real blockers were the wrong `DEFAULT_IDF_ROOT` (ABI mismatch around `esp_wifi_sta_get/set_reset_param_internal`) and stale WiFi blobs in `libs/` shadowing `link_path/`. After both fixes, the rebuilt `libspi_flash.a` boots cleanly. Mechanism kept as an escape hatch.
- `sync_idf_prebuilt_blobs` mirrors closed-source IDF blobs (`libbtdm_app`, `libphy`, `librtc`, `libcoexist`, `libnet80211`, ...) from `DEFAULT_IDF_ROOT/components/{bt,esp_coex,esp_phy,esp_wifi}\.../esp32/*.a` into `vendor/link_path/esp-idf/<same-relpath>/` **and also into `vendor/libs/`** when a same-named copy is already there. `boards.txt` puts `-L<vendor>/libs` first in `esp32.compiler.flags.libs`, so a stale `libs/` copy from the original 0.0.5 archive shadows the up-to-date `link_path/` copy. Affected blob names: `libnet80211.a`, `libcoexist.a`, `libcore.a`, `libespnow.a`, `libmesh.a`, `libpp.a`, `libsmartconfig.a`, `libwapi.a`, `libbtdm_app.a`, `libphy.a`, `librtc.a`.
- **`DEFAULT_IDF_ROOT` MUST be `E:\github\TuyaOpen-esp32\esp-idf`** (TuyaOpen's pinned IDF, commit `67c1de1e`), NOT `E:\github\esp-idf` (release/v5.4 newer). The newer branch removed/renamed `esp_wifi_sta_get/set_reset_param_internal`, which `libwpa_supplicant.a` from this build still calls — mixing the two causes undefined symbols at firmware link time.
- `compute_lib_plan` skips variants whose snapshot dirs don't exist. Without this, single-variant builds suffix every staged lib with `_no_psram` and delete the bare-name copies → `libs_flags.txt` (`-l<name>`) can't link.
- Both build loops in `main()` currently have a `if v["name"] != "no_psram": continue` TEMP gate so only `no_psram` is rebuilt/staged. Remove the gate to re-enable PSRAM variants.
- `tos.py build` prompts on platform-commit mismatch (TuyaOpen pin vs user's tree). Script pipes `"n\n"` to stdin so the prompt falls through; without this the build hangs.
- TuyaOpen venv at `E:\github\TuyaOpen\.venv` must have `pyserial` (imported transitively by `tos.py` → `cli_monitor` → `serial`).
- Unset `IDF_PATH` and `IDF_TOOLS_PATH` before running, or `toolchain_file.cmake:1-6` will fall back to the unusable `~/.espressif` path on Windows.

### Required Vendor Patches (Not Scripted)

Applied once by hand to `D:\Symlinks\...\vendor-esp32\0.0.5\`:

- `platform/ESP32/esp-idf/components/lwip/port/include/lwipopts.h:969` — make `LWIP_COMPAT_SOCKETS` `#ifndef`-guarded so the package-level `-DLWIP_COMPAT_SOCKETS=1` define wins.
- `packager-tools/bootloader.bin` — keep pre-rebuild hash `A7E0B5D0A2BCDDA3E54F816570CEBD214604826CFFFC75FCEC51A7A6DB3702E3` (canonical here). Original archive hash `75953D4B...`; freshly rebuilt `93F6C49B...` also boots but we don't ship it.

### Required Upstream TuyaOpen Patches (Not Scripted)

Applied once by hand to `E:\github\TuyaOpen` (parent repo) for `tos.py build` to succeed on the `switch_demo` app:

- `boards/ESP32/ESP32/Kconfig` — remove `select ENABLE_ESP_DISPLAY` (upstream commit `e92da875` unconditionally forces the display stack on, but `switch_demo` doesn't enable LVGL → missing `lcd_st7789_spi.h` / `xl9555.h`).
- `boards/ESP32/ESP32/esp32_bread_board.c` — wrap `lcd_st7789_spi.h`, `xl9555.h` includes and all `board_display_*` calls in `#if defined(CONFIG_ENABLE_ESP_DISPLAY)`.

## CI

Single workflow: `sync-to-gitee.yml` — mirrors pushes/deletes to Gitee. No build/test CI in this repo. Build verification happens in the parent tuyaopen repo.

## Dependencies

ESP-IDF components managed via `idf_component.yml` in `tuya_open_sdk/main/`:
- lvgl 9.2.0, esp-sr ^2.0.0, esp_codec_dev, esp_lcd_sh8601, esp_io_expander_tca9554, esp_lcd_touch_ft5x06

## Language

Build scripts and comments are mixed Chinese/English. Error messages in Python scripts are English.