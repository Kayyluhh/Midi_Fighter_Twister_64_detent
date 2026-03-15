# Makefile for Midi Fighter Twister firmware
# Replaces Microchip Studio / Atmel Studio .cproj build
# Extracted from Midi_Fighter_Twister.cproj

# ── Toolchain ────────────────────────────────────────────────
CC      = avr-gcc
OBJCOPY = avr-objcopy
OBJDUMP = avr-objdump
SIZE    = avr-size

# ── Target MCU ───────────────────────────────────────────────
MCU     = atxmega128a4u

# ── Project ──────────────────────────────────────────────────
TARGET  = Midi_Fighter_Twister
SRCDIR  = src
BUILDDIR = build

# ── Preprocessor Defines ────────────────────────────────────
DEFINES  = -DF_USB=48000000UL
DEFINES += -DF_CPU=32000000UL
DEFINES += -DBOARD=USER_BOARD
DEFINES += -DUSE_LUFA_CONFIG_HEADER
DEFINES += -DARCH=ARCH_XMEGA

# ── Include Paths ────────────────────────────────────────────
INCLUDES  = -I$(SRCDIR)
INCLUDES += -I$(SRCDIR)/ASF/common/boards/user_board
INCLUDES += -I$(SRCDIR)/config
INCLUDES += -I$(SRCDIR)/ASF/common/boards
INCLUDES += -I$(SRCDIR)/ASF/common/services/fifo
INCLUDES += -I$(SRCDIR)/ASF/common/services/hugemem
INCLUDES += -I$(SRCDIR)/ASF/xmega/drivers/cpu
INCLUDES += -I$(SRCDIR)/ASF/xmega/drivers/dma
INCLUDES += -I$(SRCDIR)/ASF/xmega/drivers/nvm
INCLUDES += -I$(SRCDIR)/ASF/xmega/drivers/pmic
INCLUDES += -I$(SRCDIR)/ASF/xmega/drivers/sleep
INCLUDES += -I$(SRCDIR)/ASF/xmega/drivers/tc
INCLUDES += -I$(SRCDIR)/ASF/xmega/drivers/wdt
INCLUDES += -I$(SRCDIR)/ASF/common/drivers/nvm
INCLUDES += -I$(SRCDIR)/ASF/common/services/clock
INCLUDES += -I$(SRCDIR)/ASF/common/services/delay
INCLUDES += -I$(SRCDIR)/ASF/common/services/ioport
INCLUDES += -I$(SRCDIR)/ASF/common/services/serial/xmega_usart
INCLUDES += -I$(SRCDIR)/ASF/common/services/serial
INCLUDES += -I$(SRCDIR)/ASF/common/services/sleepmgr
INCLUDES += -I$(SRCDIR)/ASF/common/utils
INCLUDES += -I$(SRCDIR)/ASF/xmega/drivers/usart
INCLUDES += -I$(SRCDIR)/ASF/xmega/utils/preprocessor
INCLUDES += -I$(SRCDIR)/ASF/xmega/utils
INCLUDES += -I$(SRCDIR)/LUFA

# ── Compiler / Linker Flags ──────────────────────────────────
CFLAGS   = -mmcu=$(MCU)
CFLAGS  += -Os
CFLAGS  += -fdata-sections -ffunction-sections
CFLAGS  += -Wall
CFLAGS  += -std=gnu99
CFLAGS  += -fno-strict-aliasing
CFLAGS  += -Wstrict-prototypes -Wmissing-prototypes
CFLAGS  += -Werror-implicit-function-declaration
CFLAGS  += -Wpointer-arith
CFLAGS  += -mrelax
CFLAGS  += $(DEFINES)
CFLAGS  += $(INCLUDES)

ASFLAGS  = -mmcu=$(MCU)
ASFLAGS += -mrelax
ASFLAGS += -DBOARD=USER_BOARD
ASFLAGS += $(INCLUDES)
ASFLAGS += -x assembler-with-cpp

LDFLAGS  = -mmcu=$(MCU)
LDFLAGS += -Wl,--relax
LDFLAGS += -Wl,--gc-sections
LDFLAGS += -lm

# ── Source Files ─────────────────────────────────────────────
# Application sources
CSRC  = $(SRCDIR)/main.c
CSRC += $(SRCDIR)/colorMap.c
CSRC += $(SRCDIR)/config.c
CSRC += $(SRCDIR)/Descriptors.c
CSRC += $(SRCDIR)/display_driver.c
CSRC += $(SRCDIR)/eeprom.c
CSRC += $(SRCDIR)/encoders.c
CSRC += $(SRCDIR)/input.c
CSRC += $(SRCDIR)/jump_to_bootloader.c
CSRC += $(SRCDIR)/midi.c
CSRC += $(SRCDIR)/self_test.c
CSRC += $(SRCDIR)/sequencer.c
CSRC += $(SRCDIR)/sequencer_display.c
CSRC += $(SRCDIR)/sequencer_input.c
CSRC += $(SRCDIR)/side_switch.c
CSRC += $(SRCDIR)/sysex.c

# ASF sources
CSRC += $(SRCDIR)/ASF/common/boards/user_board/init.c
CSRC += $(SRCDIR)/ASF/common/drivers/nvm/xmega/xmega_nvm.c
CSRC += $(SRCDIR)/ASF/common/services/clock/xmega/sysclk.c
CSRC += $(SRCDIR)/ASF/common/services/fifo/fifo.c
CSRC += $(SRCDIR)/ASF/common/services/hugemem/avr8/avr8_hugemem.c
CSRC += $(SRCDIR)/ASF/common/services/ioport/xmega/ioport_compat.c
CSRC += $(SRCDIR)/ASF/common/services/serial/usart_serial.c
CSRC += $(SRCDIR)/ASF/common/services/sleepmgr/xmega/sleepmgr.c
CSRC += $(SRCDIR)/ASF/xmega/drivers/dma/dma.c
CSRC += $(SRCDIR)/ASF/xmega/drivers/nvm/nvm.c
CSRC += $(SRCDIR)/ASF/xmega/drivers/tc/tc.c
CSRC += $(SRCDIR)/ASF/xmega/drivers/usart/usart.c
CSRC += $(SRCDIR)/ASF/xmega/drivers/wdt/wdt.c

# LUFA USB sources
CSRC += $(SRCDIR)/LUFA/LUFA/Drivers/USB/Class/Common/HIDParser.c
CSRC += $(SRCDIR)/LUFA/LUFA/Drivers/USB/Class/Device/AudioClassDevice.c
CSRC += $(SRCDIR)/LUFA/LUFA/Drivers/USB/Class/Device/CDCClassDevice.c
CSRC += $(SRCDIR)/LUFA/LUFA/Drivers/USB/Class/Device/HIDClassDevice.c
CSRC += $(SRCDIR)/LUFA/LUFA/Drivers/USB/Class/Device/MassStorageClassDevice.c
CSRC += $(SRCDIR)/LUFA/LUFA/Drivers/USB/Class/Device/MIDIClassDevice.c
CSRC += $(SRCDIR)/LUFA/LUFA/Drivers/USB/Class/Device/PrinterClassDevice.c
CSRC += $(SRCDIR)/LUFA/LUFA/Drivers/USB/Class/Device/RNDISClassDevice.c
CSRC += $(SRCDIR)/LUFA/LUFA/Drivers/USB/Class/Host/AndroidAccessoryClassHost.c
CSRC += $(SRCDIR)/LUFA/LUFA/Drivers/USB/Class/Host/AudioClassHost.c
CSRC += $(SRCDIR)/LUFA/LUFA/Drivers/USB/Class/Host/CDCClassHost.c
CSRC += $(SRCDIR)/LUFA/LUFA/Drivers/USB/Class/Host/HIDClassHost.c
CSRC += $(SRCDIR)/LUFA/LUFA/Drivers/USB/Class/Host/MassStorageClassHost.c
CSRC += $(SRCDIR)/LUFA/LUFA/Drivers/USB/Class/Host/MIDIClassHost.c
CSRC += $(SRCDIR)/LUFA/LUFA/Drivers/USB/Class/Host/PrinterClassHost.c
CSRC += $(SRCDIR)/LUFA/LUFA/Drivers/USB/Class/Host/RNDISClassHost.c
CSRC += $(SRCDIR)/LUFA/LUFA/Drivers/USB/Class/Host/StillImageClassHost.c
CSRC += $(SRCDIR)/LUFA/LUFA/Drivers/USB/Core/ConfigDescriptors.c
CSRC += $(SRCDIR)/LUFA/LUFA/Drivers/USB/Core/DeviceStandardReq.c
CSRC += $(SRCDIR)/LUFA/LUFA/Drivers/USB/Core/Events.c
CSRC += $(SRCDIR)/LUFA/LUFA/Drivers/USB/Core/HostStandardReq.c
CSRC += $(SRCDIR)/LUFA/LUFA/Drivers/USB/Core/USBTask.c
CSRC += $(SRCDIR)/LUFA/LUFA/Drivers/USB/Core/XMEGA/Device_XMEGA.c
CSRC += $(SRCDIR)/LUFA/LUFA/Drivers/USB/Core/XMEGA/EndpointStream_XMEGA.c
CSRC += $(SRCDIR)/LUFA/LUFA/Drivers/USB/Core/XMEGA/Endpoint_XMEGA.c
CSRC += $(SRCDIR)/LUFA/LUFA/Drivers/USB/Core/XMEGA/Pipe_XMEGA.c
CSRC += $(SRCDIR)/LUFA/LUFA/Drivers/USB/Core/XMEGA/USBController_XMEGA.c
CSRC += $(SRCDIR)/LUFA/LUFA/Drivers/USB/Core/XMEGA/USBInterrupt_XMEGA.c

# Assembly sources
ASRC  = $(SRCDIR)/ASF/xmega/drivers/cpu/ccp.s
ASRC += $(SRCDIR)/ASF/xmega/drivers/nvm/nvm_asm.s

# ── Object Files ─────────────────────────────────────────────
COBJ  = $(patsubst $(SRCDIR)/%.c,$(BUILDDIR)/%.o,$(CSRC))
AOBJ  = $(patsubst $(SRCDIR)/%.s,$(BUILDDIR)/%.o,$(ASRC))
OBJ   = $(COBJ) $(AOBJ)

# ── Rules ────────────────────────────────────────────────────
.PHONY: all clean size

all: $(BUILDDIR)/$(TARGET).hex $(BUILDDIR)/$(TARGET).eep $(BUILDDIR)/$(TARGET).lss size

$(BUILDDIR)/$(TARGET).elf: $(OBJ)
	@echo "  LINK    $@"
	@$(CC) $(LDFLAGS) -o $@ $^

$(BUILDDIR)/$(TARGET).hex: $(BUILDDIR)/$(TARGET).elf
	@echo "  HEX     $@"
	@$(OBJCOPY) -O ihex -R .eeprom -R .fuse -R .lock -R .signature $< $@

$(BUILDDIR)/$(TARGET).eep: $(BUILDDIR)/$(TARGET).elf
	@echo "  EEP     $@"
	@$(OBJCOPY) -j .eeprom --set-section-flags=.eeprom="alloc,load" \
		--change-section-lma .eeprom=0 --no-change-warnings -O ihex $< $@

$(BUILDDIR)/$(TARGET).lss: $(BUILDDIR)/$(TARGET).elf
	@echo "  LSS     $@"
	@$(OBJDUMP) -h -S $< > $@

size: $(BUILDDIR)/$(TARGET).elf
	@echo ""
	@$(SIZE) --mcu=$(MCU) --format=avr $<

# Compile C sources
$(BUILDDIR)/%.o: $(SRCDIR)/%.c
	@mkdir -p $(dir $@)
	@echo "  CC      $<"
	@$(CC) $(CFLAGS) -c -o $@ $<

# Assemble .s sources
$(BUILDDIR)/%.o: $(SRCDIR)/%.s
	@mkdir -p $(dir $@)
	@echo "  AS      $<"
	@$(CC) $(ASFLAGS) -c -o $@ $<

clean:
	@echo "  CLEAN"
	@rm -rf $(BUILDDIR)
