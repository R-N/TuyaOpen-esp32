/**
 * @file tkl_spi.c
 * @brief ESP32 SPI master driver for the TuyaOS adapter.
 *
 * Drives the ESP32 hardware SPI (VSPI/HSPI) controller directly via the SPI
 * peripheral registers, full-duplex, in 64-byte FIFO chunks. The TUYA_SPI cfg
 * does not carry pins, so the controller is routed to the board's default VSPI
 * pins (CLK=18, MISO=19, MOSI=23); CS is driven by the caller. This mirrors the
 * behaviour the Arduino SPI wrapper (libraries/SPI) relies on.
 *
 * @copyright Copyright 2020-2021 Tuya Inc. All Rights Reserved.
 */
#include <string.h>

#include "esp_private/periph_ctrl.h"
#include "esp_rom_gpio.h"
#include "driver/gpio.h"
#include "soc/gpio_sig_map.h"
#include "soc/soc.h"
#include "soc/spi_reg.h"

#include "tuya_error_code.h"
#include "tuya_cloud_types.h"
#include "tkl_spi.h"

/* Default VSPI pinout (ESP32 classic). CS is handled by the caller. */
#define TKL_SPI_PIN_CLK    18
#define TKL_SPI_PIN_MISO   19
#define TKL_SPI_PIN_MOSI   23

static bool s_spi_ready = false;
static bool s_pins_connected = false;
static TUYA_SPI_BASE_CFG_T s_spi_cfg;

/* TUYA_SPI_NUM_0/_1 -> SPI peripheral index (2=HSPI, 3=VSPI). */
static int tkl_spi_index(TUYA_SPI_NUM_E port)
{
    return port == TUYA_SPI_NUM_1 ? 2 : 3;
}

static void tkl_spi_connect_pins(void)
{
    if (s_pins_connected) {
        return;
    }
    esp_rom_gpio_pad_select_gpio(TKL_SPI_PIN_CLK);
    esp_rom_gpio_pad_select_gpio(TKL_SPI_PIN_MOSI);
    esp_rom_gpio_pad_select_gpio(TKL_SPI_PIN_MISO);
    gpio_set_direction(TKL_SPI_PIN_CLK, GPIO_MODE_OUTPUT);
    gpio_set_direction(TKL_SPI_PIN_MOSI, GPIO_MODE_OUTPUT);
    gpio_set_direction(TKL_SPI_PIN_MISO, GPIO_MODE_INPUT);
    esp_rom_gpio_connect_out_signal(TKL_SPI_PIN_CLK, VSPICLK_OUT_IDX, false, false);
    esp_rom_gpio_connect_out_signal(TKL_SPI_PIN_MOSI, VSPID_OUT_IDX, false, false);
    esp_rom_gpio_connect_in_signal(TKL_SPI_PIN_MISO, VSPIQ_IN_IDX, false);
    s_pins_connected = true;
}

static void tkl_spi_set_clock(int spi, uint32_t hz)
{
    uint32_t div;
    uint32_t pre;
    uint32_t n;

    if (hz == 0) {
        hz = 1000000;
    }
    if (hz >= 80000000) {
        REG_WRITE(SPI_CLOCK_REG(spi), SPI_CLK_EQU_SYSCLK);
        return;
    }

    div = (80000000 + hz - 1) / hz;
    if (div < 2) {
        div = 2;
    }

    pre = (div + 63) / 64;
    if (pre == 0) {
        pre = 1;
    }
    if (pre > 8192) {
        pre = 8192;
    }

    n = div / pre;
    if (n < 2) {
        n = 2;
    }
    if (n > 64) {
        n = 64;
    }

    REG_WRITE(SPI_CLOCK_REG(spi), ((pre - 1) << SPI_CLKDIV_PRE_S) |
                                  ((n - 1) << SPI_CLKCNT_N_S) |
                                  (((n / 2) - 1) << SPI_CLKCNT_H_S) |
                                  ((n - 1) << SPI_CLKCNT_L_S));
}

static void tkl_spi_apply_config(TUYA_SPI_NUM_E port, const TUYA_SPI_BASE_CFG_T *cfg)
{
    int spi = tkl_spi_index(port);
    uint32_t user;
    uint32_t pin;
    uint32_t ctrl;

    periph_module_enable(spi == 2 ? PERIPH_HSPI_MODULE : PERIPH_VSPI_MODULE);
    tkl_spi_connect_pins();
    tkl_spi_set_clock(spi, cfg ? cfg->freq_hz : 1000000);

    user = SPI_DOUTDIN | SPI_USR_MOSI | SPI_USR_MISO;
    if (cfg && (cfg->mode == TUYA_SPI_MODE1 || cfg->mode == TUYA_SPI_MODE3)) {
        user |= SPI_CK_OUT_EDGE;
    }
    REG_WRITE(SPI_USER_REG(spi), user);

    pin = SPI_CS0_DIS | SPI_CS1_DIS | SPI_CS2_DIS;
    if (cfg && (cfg->mode == TUYA_SPI_MODE2 || cfg->mode == TUYA_SPI_MODE3)) {
        pin |= SPI_CK_IDLE_EDGE;
    }
    REG_WRITE(SPI_PIN_REG(spi), pin);

    ctrl = 0;
    if (cfg && cfg->bitorder == TUYA_SPI_ORDER_LSB2MSB) {
        ctrl = SPI_WR_BIT_ORDER | SPI_RD_BIT_ORDER;
    }
    REG_WRITE(SPI_CTRL_REG(spi), ctrl);
    REG_WRITE(SPI_SLAVE_REG(spi), 0);
}

OPERATE_RET tkl_spi_init(TUYA_SPI_NUM_E port, const TUYA_SPI_BASE_CFG_T *cfg)
{
    if (cfg) {
        memcpy(&s_spi_cfg, cfg, sizeof(s_spi_cfg));
    } else {
        memset(&s_spi_cfg, 0, sizeof(s_spi_cfg));
    }
    tkl_spi_apply_config(port, &s_spi_cfg);
    s_spi_ready = true;
    return OPRT_OK;
}

OPERATE_RET tkl_spi_deinit(TUYA_SPI_NUM_E port)
{
    (void)port;
    s_spi_ready = false;
    return OPRT_OK;
}

OPERATE_RET tkl_spi_transfer(TUYA_SPI_NUM_E port, void *send_buf, void *receive_buf, uint32_t length)
{
    int spi;
    uint8_t *tx;
    uint8_t *rx;
    uint32_t offset = 0;

    if (!s_spi_ready) {
        tkl_spi_apply_config(port, &s_spi_cfg);
    }
    if (length == 0) {
        return OPRT_OK;
    }

    spi = tkl_spi_index(port);
    tx = (uint8_t *)send_buf;
    rx = (uint8_t *)receive_buf;

    while (offset < length) {
        uint32_t chunk = length - offset;
        uint32_t bit_length;
        uint32_t i;
        uint32_t word;

        if (chunk > 64) {
            chunk = 64;
        }

        while (REG_READ(SPI_CMD_REG(spi)) & SPI_USR) {
        }

        for (word = 0; word < 16; ++word) {
            REG_WRITE(SPI_W0_REG(spi) + word * 4, 0);
        }

        for (i = 0; i < chunk; ++i) {
            uint32_t shift = (i % 4) * 8;
            uint32_t value;
            word = i / 4;
            value = REG_READ(SPI_W0_REG(spi) + word * 4);
            value |= (uint32_t)(tx ? tx[offset + i] : 0xFF) << shift;
            REG_WRITE(SPI_W0_REG(spi) + word * 4, value);
        }

        bit_length = chunk * 8 - 1;
        REG_WRITE(SPI_MOSI_DLEN_REG(spi), bit_length);
        REG_WRITE(SPI_MISO_DLEN_REG(spi), bit_length);
        REG_WRITE(SPI_CMD_REG(spi), SPI_USR);

        while (REG_READ(SPI_CMD_REG(spi)) & SPI_USR) {
        }

        if (rx) {
            for (i = 0; i < chunk; ++i) {
                uint32_t shift = (i % 4) * 8;
                word = i / 4;
                rx[offset + i] = (uint8_t)(REG_READ(SPI_W0_REG(spi) + word * 4) >> shift);
            }
        }

        offset += chunk;
    }

    return OPRT_OK;
}

OPERATE_RET tkl_spi_send(TUYA_SPI_NUM_E port, void *data, uint16_t size)
{
    return tkl_spi_transfer(port, data, NULL, size);
}

OPERATE_RET tkl_spi_recv(TUYA_SPI_NUM_E port, void *data, uint16_t size)
{
    return tkl_spi_transfer(port, NULL, data, size);
}

OPERATE_RET tkl_spi_abort_transfer(TUYA_SPI_NUM_E port)
{
    (void)port;
    return OPRT_OK;
}

OPERATE_RET tkl_spi_get_status(TUYA_SPI_NUM_E port, TUYA_SPI_STATUS_T *status)
{
    (void)port;
    if (status) {
        memset(status, 0, sizeof(*status));
    }
    return OPRT_OK;
}

OPERATE_RET tkl_spi_irq_init(TUYA_SPI_NUM_E port, TUYA_SPI_IRQ_CB cb)
{
    (void)port;
    (void)cb;
    return OPRT_OK;
}

OPERATE_RET tkl_spi_irq_enable(TUYA_SPI_NUM_E port)
{
    (void)port;
    return OPRT_OK;
}

OPERATE_RET tkl_spi_irq_disable(TUYA_SPI_NUM_E port)
{
    (void)port;
    return OPRT_OK;
}

int32_t tkl_spi_get_data_count(TUYA_SPI_NUM_E port)
{
    (void)port;
    return 0;
}

OPERATE_RET tkl_spi_ioctl(TUYA_SPI_NUM_E port, uint32_t cmd, void *args)
{
    (void)port;
    (void)cmd;
    (void)args;
    return OPRT_OK;
}

/* Referenced by the Arduino SPI wrapper to select the SPI-controller (vs.
 * SPI-flash) routing on some Tuya chips; no-op on ESP32. */
void tkl_spi_set_spic_flag(void)
{
}
