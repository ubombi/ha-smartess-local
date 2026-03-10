# SmartESS Local

HA integration for Voltronic/Axpert inverters with EyeBond WiFi dongles.
Polls and controls your inverter locally. No firmware changes, non-destructive,
works alongside the cloud.

Tested on **iGrid SV IV** (PI17, devcode 0x0994). Other models may work.
Protocol implementation is still rough -- more devices needed to test and clean up.

> The "cloud" is a Chinese server controlling your inverter
> without encryption. Be reasonably paranoid, and block the
> collector from internet.


## How it works

The SmartESS app redirects the collector via UDP broadcast.
We hijack this: HA announces itself as upstream, collector
connects, we talk directly to the inverter.

Protocol reverse-engineered from the SmartESS Android app.

```
[Inverter] --RS485-- [EyeBond Dongle] --TCP:8899--> [Home Assistant]
                            ^                              |
                            +------ UDP:58899 -------------+
                            "set>server=<ha_ip>:8899;"
```


## Protocol

```
UDP  :58899  "set>server=IP:PORT;"                discovery
TCP  :8899   [tid:2][dev:2][len:2][addr:1][fc:1]  EyBond ModBus
P17          ^P/^S<len><cmd><crc><CR>              poll/set
Data         comma-separated integers              response
```

CRC-16/XMODEM. Byte stuffing on 0x28/0x0D/0x0A.
ET, FLAG use Q-protocol framing: `(` instead of `^D`.


## Install

1. Copy `custom_components/smartess_local/` to HA config
2. Restart, add "SmartESS Local"
3. Collector connects within 60s

Cross-subnet: point broadcast IP directly at collector.


## Entities

**Sensors** -- voltage, current, power, frequency, temperature,
SOC, mode, faults, energy totals

**Energy Dashboard** -- ac_output_energy, battery_charge_energy,
battery_discharge_energy (kWh, total_increasing)

**Controls** -- output/charger priority, voltages, currents,
10 flag switches

**Polling** (configurable):

    Fast 5s    GS GS2 MOD
    Med  10s   FWS
    Slow 60s   ET FLAG
    Once       PIRI PI GMN ID VFW


## TODO

- [ ] Map GS fields 8-9, 11 (charge current always 0, field 11 unknown)
- [ ] Map GS2 fields 3-16 (need dual-MPPT to test)
- [ ] PV2 sensors: auto-discover via available_on_nonzero
- [ ] Verify RX CRC
- [ ] Test flag commands PEC..PDJ
- [ ] Diagnostics platform (coordinator state dump for bug reports)
- [ ] PI18/PI30 support
- [ ] HACS
- [ ] Drop dead crc16_modbus()


## License

MIT
