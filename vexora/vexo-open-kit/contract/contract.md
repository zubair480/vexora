# Vexo Open band contract v1.1

Every command you can send to a Vexo Open band and every kind of data it sends back, byte by
byte. The libraries in `js/` and `python/` implement exactly this; read it if you want to
write your own client or understand an error.

Event bands run firmware 1.1.0, which speaks this contract (v1.1, revision **2026-11-12**):
`GET_INFO` reports `proto_major` 1, `proto_minor` 1 and firmware 1.1.0.

**Encoding rules.** Little-endian, packed, no padding. `u8/u16/u32` are unsigned, `i8/i16/i32`
signed, `u64` is an unsigned 8-byte number. `t_ms` = `u32` milliseconds since the band
started (wraps after 49.7 days); section 5.8 says how to turn it into real time. Clients
must ignore frame types, event codes and capability bits they do not know.

---

## 1. Finding and connecting

| Item | Value |
|---|---|
| Name | `Vexo-HACK-XX`: the prefix `Vexo-HACK-` + the band number, written with 2 digits for 1..99 (`Vexo-HACK-01` .. `Vexo-HACK-99`) and 3 digits for 100..999 (`Vexo-HACK-100` .. `Vexo-HACK-999`). 12 or 13 ASCII characters. `Vexo-HACK-00` = number not set. The number of bands is not fixed: any number 1..999 is valid |
| Advertising packet | Flags + the 128-bit service UUID below + manufacturer data, company `0xFFFF`: `[0x4F][proto_major][band_no u16]`. Every scanner receives this packet, so a band can always be recognised and told apart by number |
| Scan response | Complete local name `Vexo-HACK-XX`. Only a scanner that scans actively receives it (Web Bluetooth and bleak do by default). The name does not fit in the advertising packet next to the UUID |
| Idle | Not advertising, not connectable |
| Open the connect window | Double-tap the band: it advertises for 60 s. Another double-tap restarts the 60 s (it never shortens a longer window). A double-tap while a Bluetooth client is connected does nothing |
| After power-on / restart | Window opens for 60 s |
| After the link closes | Window reopens for 120 s, whether the link dropped or the client disconnected (not after `SLEEP`) |
| Connections | One at a time. Advertising stops while connected |
| Security | None: no pairing, no bonding, no encryption. A pairing request is rejected |
| ATT MTU | The band asks for 247. `max_frame` = MTU - 3, reported by `GET_INFO` |
| PHY, connection interval | Chosen by the band (2M when the other side allows; 30-50 ms idle, asked for 5 s after connecting, and 15 ms while audio streams). The other side may refuse; until then and after a refusal the interval is its own choice. Clients cannot and need not set them. The band also asks for 15 ms while MOTION2 streams over Bluetooth at more than 2000 bytes a second (208 Hz with both sensors, 416 Hz and up with one sensor; table in section 5.2), and goes back to the idle values when neither that nor audio needs it |
| Radio power | Default -8 dBm. See `RADIO_SET_TX_POWER` |
| Status LED | Window open: blue blink once a second (half a second on, half a second off). Connected: yours (default off). Idle: off. The window blink comes before your LED setting: a USB client's `LED_SET` during an open window is stored and answered OK, and shows when the window closes or a Bluetooth client connects |

Notes for client authors:

- Web Bluetooth, one band: `filters: [{ name: "Vexo-HACK-42", services: ["254f0001-df82-4a9c-a2b5-563a0f6b2021"] }]`.
  Any band: `filters: [{ services: ["254f0001-df82-4a9c-a2b5-563a0f6b2021"] }]`. Filter on the
  service UUID together with the name, so only Vexo Open bands can appear. The browser joins
  the advertising packet and the scan response before it compares them with the filter.
- Use the exact name for one band. A prefix that ends in a digit is ambiguous:
  `namePrefix: "Vexo-HACK-1"` matches bands 10..19 and 100..199.
- Python bleak: scan in the default (active) mode with `service_uuids=["254f0001-..."]` and
  match `advertisement_data.local_name == "Vexo-HACK-42"`. Prefer that over `device.name`,
  which some computers fill from memory. If the name has not arrived, the band number is in
  `advertisement_data.manufacturer_data[0xFFFF]` = `4F 01 lo hi`.
- A band can be seen a moment before its name is known, because the scan response arrives
  separately. Scan for at least 5 s before reporting "not found".

## 2. Transport

One frame format on Bluetooth and USB. A frame is `[type:1][body]`, 1..244 bytes.

### 2.1 Bluetooth (GATT)

UUID base `254fXXXX-df82-4a9c-a2b5-563a0f6b2021`.

| Attribute | UUID | Properties | Use |
|---|---|---|---|
| Vexo Open service | `254f0001-df82-4a9c-a2b5-563a0f6b2021` | primary | |
| RX | `254f0002-df82-4a9c-a2b5-563a0f6b2021` | write, write without response | client to band: one frame per write, at most `max_frame` bytes |
| TX | `254f0003-df82-4a9c-a2b5-563a0f6b2021` | notify | band to client: one frame per notification |
| Device Information | `0x180A` | read | Manufacturer = `Vexo Open`, Model = `Vexo Open`, Firmware Revision = `open-` + the firmware version (`open-1.1.0`), Hardware Revision = `vexo_open_xiao` |
| Device name | `0x2A00` | read | `Vexo-HACK-XX`; not writable |

There are no other services.

Client rules: subscribe to TX once, before the first command. Until you have subscribed
no command runs: a write-with-response fails with an ATT error and a write-without-response
is dropped. Use write-with-response for commands. Send one command, wait for its response (timeout 2 s), then send the next.

### 2.2 USB cable

| Item | Value |
|---|---|
| Port | The band shows up as one USB serial port (CDC ACM). It carries contract frames only |
| Serial settings | Any baud rate (ignored). No flow control: RTS/CTS flow control is not supported, leave it off (it is off by default in pyserial and in Web Serial). Assert DTR when you open the port, once, before your first command; DTR low = session closed ("USB sessions" below) |
| Framing | Each frame is COBS-encoded and followed by one `0x00` byte. No checksum. After opening, send one `0x00` before your first frame and discard received bytes up to the first `0x00`; after any garbage a `0x00` gets both sides back in step |
| Availability | Whenever the cable is plugged in. No double-tap needed. Works while a Bluetooth client is connected |
| No console | There is no shell and no text console. Bytes that do not decode to a valid frame are dropped with no reply |
| USB strings | Manufacturer `Vexo Open`, product `Vexo Open band` |
| USB vendor / product ID | `0x2FE3:0x0004`. Find the band by its USB strings rather than by these numbers |

COBS (Consistent Overhead Byte Stuffing) removes every `0x00` from the frame so that `0x00`
can mark the end. Example: frame `01 00 01` is sent as `02 01 02 01 00`.

#### Writing your own USB client: checklist

For a client that does not use the kit's libraries. Each line is explained further down.

1. Open the port once. Assert DTR as part of the open, before your first command.
2. No RTS/CTS flow control.
3. Send one `0x00` before your first command.
4. Keep reading all the time, on a thread or task of its own: also while you wait for a
   response and while your own code is busy.
5. Match every response by its `seq`, and check its length against the opcode table.
   Ignore one that does not fit and keep waiting.
6. Before you close the port, send `STREAM_STOP` for everything (`mask` `0xFFFF`), then
   lower DTR.

**USB sessions.** A session is open while the cable is plugged in and DTR is high. The band
looks at DTR every 50 ms. A session ends when DTR goes low (lower it for at least 100 ms)
or the cable is pulled. Nothing else ends it: not RTS, not a change of baud rate, not a
computer that stops reading. When a session ends the band stops that session's streams and
throws away every byte it had not delivered yet.

Some computers do not lower DTR when a program closes the port. The band then cannot know
that the program is gone: the session stays open, its streams keep running (with AUDIO or
MIC_LEVEL the microphone stays on and the light stays blue), the settings stay as they
are, and the next program that opens the port finds all of that. So:

- When you finish: `STREAM_STOP` with `mask` `0xFFFF`, then close the port.
- When you start: do not assume the defaults. Send `STREAM_STOP` with `mask` `0xFFFF` and
  set what you need. Expect that a stream frame can arrive that you did not ask for.

Rules for a client:

- Assert DTR once, when you open the port, before your first command. With DTR low the
  band reads nothing: bytes sent then are thrown away.
- Leave RTS/CTS flow control off. It is not supported.
- Send one `0x00` before your first frame.
- Throw away what you receive up to the first `0x00`, and skip empty packets (two `0x00` in
  a row). The first frame of a session, and the first frame after the band gave up on a
  frame the computer did not take, can have one extra `0x00` in front of it.
- Never stop reading while the port is open, also while you wait for a response and while
  your own code is busy. Read on a thread or task of its own.
- Match every response by its `seq` (section 3).
- Check the length of every response before you use it (below).

**A computer that stops reading.** The band waits at most 100 ms for the computer to take
a frame. A stream frame that was not taken in that time is dropped, and the stream reports
STREAM_DROPPED (Notice 3), at most once a second for each stream. A response or a notice
is tried 4 times and is then lost: after about half a second without reading, send the
command again. The session goes on, with its streams and settings.

**Checking the length of a response.** A response with status OK must have exactly the data
bytes the opcode table in section 3 gives for its command: 16 for `GET_INFO`, 7 for
`LED_GET`, none for `LED_SET`, and so on. Three are not one fixed size: `GET_NAME` has 12
or 13 bytes, `STORE_GET` 1 to 232, and a response with another status has none. If the
length is wrong, do not use the response: ignore it and keep waiting for the right one
until your usual timeout. The kit's libraries check every length this way.

### 2.3 Both transports at once

A transport is open while a client is attached: over Bluetooth from the moment the client
subscribes to TX until it unsubscribes or disconnects; over USB for the length of the
session (section 2.2). Settings are one shared state, and so is the LED: the last `LED_SET`
counts, whichever transport sent it. A response goes to the transport that sent the
command. A stream goes to the transport that last sent `STREAM_START` for it. The transport
that had it before gets no notice: its frames simply stop while `STREAM_GET` still reports
the stream as active. A client that started a stream, did not stop it and sees it go silent
should ask `STREAM_GET`: active means the other transport has it now, not active means it
was stopped (by the other transport, or because that transport closed). `STREAM_START`
again takes it back. At most 4 commands wait at a time, counted over both transports
together. When a transport closes its streams stop. When no transport is open, every
setting in section 5 returns to its default and the LED turns off; only the band number
and storage persist, and the clock (section 5.8) keeps running until the band restarts.
While a USB session stays open, a Bluetooth disconnect therefore resets nothing except the
radio power (section 5.4).

## 3. Commands and responses

| Frame | type | Body |
|---|---|---|
| Command | `0x01` | `[seq:1][opcode:1][args]` |
| Response | `0x02` | `[seq:1][opcode:1][status:1][data]`; `seq` and `opcode` echo the command |
| Notice (unsolicited) | `0x03` | `[code:1][arg:1]` |
| Stream | `0x10..0x18` | section 4 |

Every command gets exactly one response. Commands run in arrival order. `seq` is any byte
you choose; use a counter so you can match responses. Ignore a response whose `seq` is not
the one you are waiting for. Also ignore an OK response whose data does not have the
length the table below gives for its command, and keep waiting (section 2.2, "Checking
the length of a response").

| status | Name | Meaning |
|---|---|---|
| 0 | OK | |
| 1 | UNKNOWN_OPCODE | |
| 2 | BAD_LENGTH | argument bytes do not match the table |
| 3 | BAD_ARG | value outside the allowed set; nothing changed |
| 4 | BUSY | storage is not ready (still starting, still erasing, or more than 10 writes in a second: section 5.5), or more than 4 commands waiting at once (both transports together) |
| 5 | NOT_AVAILABLE | that part failed its start-up check on this band (see `caps`), or it does not exist on this transport (the LINK stream over USB) |
| 6 | DENIED | blocked by a safety rule (section 6): the LED while the microphone is on or the battery is critical, the band number over Bluetooth, a motion rate too fast for the transport, BATTERY or TEMP started while the battery is critical |
| 7 | NOT_FOUND | storage key empty |
| 8 | NO_SPACE | storage full |
| 9 | IO_ERROR | sensor, storage or radio error |

| Notice code | Meaning | arg |
|---|---|---|
| 1 | LOW_BATTERY: LED brightness capped | percent |
| 2 | CRITICAL_BATTERY: streams stopped and LED off. A connected client stays connected; once it leaves, the band cannot be found until it is charging (section 6) | percent |
| 3 | STREAM_DROPPED: frames were lost because the link was too slow or a frame was larger than `max_frame`. Sent at most once a second for each stream. For MOTION2 (arg 8) at 208 Hz and up it can also mean the sensor stopped delivering: if no MOTION2 frames follow, stop and start the stream or send `MOTION2_SET` again | stream bit number 0..8 |
| 4 | USB_POWER changed | 0 unplugged, 1 plugged |

### Opcode table

Args follow the 3-byte command header; response data follows the 4-byte response header.

| Opcode | Name | Args | Response data | Notes |
|---|---|---|---|---|
| `0x01` | GET_INFO | none | `proto_major u8 = 1, proto_minor u8 = 1, fw_major u8, fw_minor u8, fw_patch u8, hw u8 (1 = XIAO nRF52840 Sense), band_no u16, caps u16, max_frame u16, uptime_ms u32` (16 bytes) | version and capability query. An event band reports firmware 1.1.0 (`fw_major` 1, `fw_minor` 1, `fw_patch` 0) |
| `0x02` | GET_NAME | none | ASCII name, no terminator | the name comes from the band number; there is no SET_NAME |
| `0x03` | SET_BAND_NUMBER | `number u16 (1..999), magic u16 = 0x5856` | `number u16` | USB only: over Bluetooth the response is DENIED and nothing changes. Persists. `GET_NAME` gives the new name at once; it is advertised from the next connect window |
| `0x04` | IDENTIFY | `seconds u8 (1..30)` | none | white blink 4 times a second, then the previous LED state returns. DENIED while the mic indicator is lit (AUDIO or MIC_LEVEL running) or the battery is critical |
| `0x05` | RESTART | none | none | restarts 200 ms after the response; the window then opens for 60 s. The band does not close the Bluetooth link first, so a Bluetooth client notices only when the link times out (about 4 s). The clock (section 5.8) is lost |
| `0x06` | SLEEP | none | none | 200 ms after the response: every stream off (on both transports) and the LED setting back to off. 200 ms later (about 400 ms after the response) the Bluetooth link is closed the normal way, with no reopen window; a connect window that was open closes too. The band can be found again after a double-tap. Other settings are not reset by `SLEEP` itself. Over USB the cable session stays open and keeps answering commands |
| `0x07` | TIME_SET | `unix_ms u64` | `set u8 = 1, unix_ms u64, t_ms u32` (13 bytes) | section 5.8. Sets the band's clock. `unix_ms` must be a time from 2020-01-01 up to, not including, 2100-01-01 UTC, in milliseconds (1577836800000 <= `unix_ms` < 4102444800000); anything else, for example a value in seconds, gives BAD_ARG and nothing changes |
| `0x08` | TIME_GET | none | `set u8 (0 not set, 1 set), unix_ms u64 (0 when not set), t_ms u32` (13 bytes) | section 5.8. `unix_ms` and `t_ms` describe the same moment: real time, and the band's own counter |
| `0x10` | STREAM_START | `mask u16` | `active u16` | bits in section 4; a bit above 8, or one for a part that failed its start-up check, or the LINK bit over USB, gives NOT_AVAILABLE with no data and nothing starts. If MOTION2 (bit 8) is set faster than the transport that sends the start allows, the answer is DENIED and nothing starts (section 5.2). A start from the other transport takes the stream over. The take-over happens before the start is checked: a start that fails (NOT_AVAILABLE, DENIED, IO_ERROR) still stops a stream in its mask that was running on the other transport |
| `0x11` | STREAM_STOP | `mask u16` | `active u16` | unknown or inactive bits are ignored; may stop a stream owned by the other transport. Error responses never carry data |
| `0x12` | STREAM_GET | none | `active u16` | |
| `0x13` | STREAM_SET_INTERVAL | `stream u8 (bit number), interval_ms u32` | `stream u8, interval_ms u32, min_ms u32, max_ms u32` (13 bytes) | section 5.7. Streams 2, 4, 5, 6 and 7 only. Any other stream number, or a value that stream does not allow, gives BAD_ARG and nothing changes |
| `0x14` | STREAM_GET_INTERVAL | `stream u8 (bit number)` | the same 13 bytes | section 5.7. BAD_ARG for a stream that has no interval |
| `0x20` | IMU_SET | `rate_hz u8, accel_range_g u8, gyro_range_dps u16` | the same 4 bytes, as applied | section 5.2 |
| `0x21` | IMU_GET | none | `rate_hz u8, accel_range_g u8, gyro_range_dps u16` | |
| `0x22` | EVENT_SET | `event_mask u8, tap_threshold_mg u16` | the same 3 bytes, as applied | section 5.2 |
| `0x23` | EVENT_GET | none | `event_mask u8, tap_threshold_mg u16` | |
| `0x24` | MOTION2_SET | `rate_hz u16, sensors u8 (1 accelerometer, 2 gyroscope, 3 both)` | `rate_hz u16, sensors u8, max_accel_hz u16, max_gyro_hz u16, max_both_hz u16` (9 bytes) | section 5.2. BAD_ARG for a rate or a `sensors` value that does not exist. DENIED, with nothing changed, if MOTION2 is streaming and the new setting is faster than its transport allows |
| `0x25` | MOTION2_GET | none | the same 9 bytes | The three `max_` values are the fastest rate for accelerometer alone, gyroscope alone and both, on the transport that asked: 833, 833, 416 over Bluetooth; 1666, 833, 833 over USB |
| `0x30` | MIC_SET_GAIN | `gain_half_db u8 (0..80)` | `gain_half_db u8` | section 5.3 |
| `0x31` | MIC_GET | none | `gain_half_db u8, sample_rate u16 = 16000, frame_ms u8 = 20, channels u8 = 1, bitrate u16 = 24000` | Opus settings are fixed |
| `0x40` | RADIO_SET_TX_POWER | `dbm i8` | `dbm i8` | section 5.4. The response is the power the radio really uses now; with no Bluetooth link (over USB) it is the value you sent, kept for the next link |
| `0x41` | RADIO_GET | none | `tx_dbm i8, rssi_dbm i8, phy u8 (1 = 1M, 2 = 2M), interval u16 (1.25 ms units), mtu u16` | `rssi_dbm` 127 = no reading. Over USB with no Bluetooth link: rssi 127, phy 0, interval 0, mtu 0 |
| `0x50` | LED_SET | `r u8, g u8, b u8, brightness u8, pattern u8, period_ms u16` | none | section 5.1. DENIED while the mic indicator is lit (AUDIO or MIC_LEVEL running) or the battery is critical |
| `0x51` | LED_GET | none | the same 7 bytes | |
| `0x60` | TEMP_SET_INTERVAL | `seconds u8 (1..255)` | `seconds u8` | default 5. This is the same setting as `STREAM_SET_INTERVAL` for stream 5, in whole seconds |
| `0x61` | BATTERY_GET | none | BATTERY body (section 4) | read once without starting the stream |
| `0x70` | STORE_INFO | none | `keys_max u16, value_max u16, free_bytes u32, used_keys u16` | section 5.5 |
| `0x71` | STORE_PUT | `key u16, value (1..value_max bytes)` | none | |
| `0x72` | STORE_GET | `key u16` | value bytes | |
| `0x73` | STORE_DEL | `key u16` | none | |
| `0x74` | STORE_WIPE | `magic u16 = 0x5856` | none | erases every key. OK means the erase has started, not that it has finished: section 5.5 |

`caps` bits (set only if that part passed its start-up check on this band): 0 accelerometer,
1 gyroscope, 2 motion events, 3 step counter, 4 microphone, 5 RGB LED, 6 battery, 7 chip
temperature, 8 storage, 9 radio power control, 10 USB transport.

Worked example, LED solid green at half brightness: send `01 07 50 00 FF 00 80 01 E8 03`
(command, seq 7, LED_SET, r 0, g 255, b 0, brightness 128, pattern 1, period 1000 ms);
the response is `02 07 50 00`.

## 4. Streams

Every stream is off after you connect. `STREAM_START` bit n starts frame type `0x10 + n`.
Where the Rate column gives "once a second", "10 per second" or an interval, that is the
default; `STREAM_SET_INTERVAL` changes it (section 5.7).

| Bit | Type | Stream | Body | Rate | Bytes/s (typical) |
|---|---|---|---|---|---|
| 0 | `0x10` | MOTION | `t_ms u32, period_ms u8, count u8`, then `count` x `{ax, ay, az i16 (milli-g), gx, gy, gz i16 (tenths of a degree per second)}`; sample i was taken at `t_ms + i x period_ms` | `rate_hz` 10, 25 (default), 50 or 100; 10 samples per frame (fewer if `max_frame` < 127) | 320 at 25 Hz, 1270 at 100 Hz |
| 1 | `0x11` | EVENT | `t_ms u32, event u8, arg u8` | when it happens | < 50 |
| 2 | `0x12` | ACTIVITY | `t_ms u32, steps_total u32, cadence_spm u8, state u8 (0 still, 1 walk, 2 run), motion_mg u16` | once a second | 13 |
| 3 | `0x13` | AUDIO | `seq u16, len u8, opus[len]`; one complete Opus packet per frame | 50 frames/s | about 3200 |
| 4 | `0x14` | BATTERY | `percent u8, state u8 (0 on battery, 1 charging, 2 charged), millivolts u16` | once at start, then when it changes (another percent, another state, or 20 mV or more away from the last frame sent), at most once a second; or on a timer (section 5.7) | < 5 |
| 5 | `0x15` | TEMP | `t_ms u32, temp_mc i32` (thousandths of a degree C, chip temperature) | every `TEMP_SET_INTERVAL` seconds, default 5 | 2 |
| 6 | `0x16` | LINK | `t_ms u32, rssi_dbm i8 (127 = no reading), tx_dbm i8, phy u8` | once a second, Bluetooth only: over USB `STREAM_START` with this bit returns NOT_AVAILABLE | 8 |
| 7 | `0x17` | MIC_LEVEL | `t_ms u32, peak_db u8, rms_db u8`; loudness over the last interval (100 ms by default, section 5.7) in dB below full scale (0 = as loud as it can measure, 96 = silence) | 10 per second | 70 |
| 8 | `0x18` | MOTION2 | `t_ms u32, sample_no u32, period_us u32, sensors u8, count u8`, then `count` samples. A sample is `ax, ay, az i16` (`sensors` 1), `gx, gy, gz i16` (`sensors` 2) or `ax, ay, az, gx, gy, gz i16` (`sensors` 3); units as MOTION. Sample i has number `sample_no + i` and was taken at `t_ms + i x period_us / 1000` | `MOTION2_SET`: 10, 25 (default), 50, 100, 208, 416, 833 or 1666 Hz, as far as the transport allows; a frame holds up to 50 ms of samples | 210 to 10,700: table in section 5.2 |

Details:

| Stream | Facts |
|---|---|
| MOTION | The sensor runs at 208 Hz; the stream takes the latest sample every `period_ms` with no filtering, so movement faster than `rate_hz / 2` aliases. `period_ms` = 1000 / rate. Units do not change with range. Gyro fields are 0 when the gyro is off, and for the first 100 ms after the gyroscope switches on (it is off between streams). |
| MOTION2 | A second motion stream, independent of MOTION: you choose the sensors and can go much faster. Both may run at once (one exception: 1666 Hz, section 5.2). **Up to 100 Hz** it works like MOTION: the latest sample at each point of a millisecond grid, and `period_us` is exactly 1,000,000 / rate. **From 208 Hz up** the sensor itself runs at that rate and the band sends every sample it takes: none is skipped or repeated, and they are evenly spaced. The sensor has its own clock, which can be a few percent fast or slow and differs from band to band. So `period_us` is **measured** by the band (the nominal value until enough samples have been timed, at most about a third of a second, then refined all the time): **always place samples in time with `t_ms` and `period_us`, never with the nominal rate**, or your timeline drifts. `t_ms` is the time of the first sample of the frame. **`sample_no`** starts at 0 at `STREAM_START` and at every `MOTION2_SET` that changes the stream, and counts every sample the band put in a frame, including frames that were then lost on a slow link: a jump in `sample_no` is exactly the number of samples lost. A step in `t_ms` without a jump in `sample_no` is a short pause in sampling (for example after a range change). **Frame size**: a frame holds the samples of 50 ms, at least 1 and at most what fits: (`max_frame` - 15) / 6 samples with one sensor, / 12 with both, and never more than 38 (one sensor) or 19 (both). `max_frame` must be at least 21 (one sensor) or 27 (both); below that every frame is dropped with Notice 3. **Gyroscope**: its range is `IMU_SET`'s `gyro_range_dps`; while that is 0 the gyro values are 0. They are also 0 for the first 100 ms after the gyroscope switches on. "Accelerometer only" lets the gyroscope sleep and saves a little power; "gyroscope only" saves bytes but not power, because the accelerometer always runs (it detects taps). **Side effects at 208 Hz and up**: while MOTION2 runs that fast, MOTION, EVENT, ACTIVITY and the double-tap that opens the connect window are fed from the fast samples averaged down to 208 Hz, so taps can feel slightly more or less sensitive. Starting, stopping or changing such a stream leaves a gap of about 30 ms in MOTION and EVENT. |
| EVENT | Codes: 1 single tap, 2 double tap, 3 free-fall, 4 tilt, 5 orientation changed (`arg` = face pointing up: 0 +X, 1 -X, 2 +Y, 3 -Y, 4 +Z, 5 -Z), 6 motion started, 7 motion stopped. `arg` = 0 otherwise. Free-fall = total acceleration below 0.35 g for 100 ms; tilt = gravity direction moved more than 35 degrees and held 2 s; orientation = one axis above 0.8 g for 300 ms; motion started / stopped = movement above 0.1 g / below 0.05 g for 2 s. Only the tap threshold can be changed. |
| ACTIVITY | `steps_total` is a lifetime count that survives restarts; subtract the first value you see. |
| AUDIO | Opus, 16 kHz mono, 20 ms frames, about 24 kbit/s, CELT mode. `seq` starts at 0 each time the stream starts and wraps at 65535; a gap means lost frames (use the decoder's packet-loss concealment). There is no timestamp: frame time = start time + `seq` x 20 ms, where the sound of frame 0 begins about 140 ms after `STREAM_START` (about 40 ms if MIC_LEVEL already had the microphone on). If the link is slow, frames queue on the band and are sent in order. The queue (4096 bytes for each transport) is shared by all streams; when it is full, new frames are dropped (the ones that no longer fit), and a frame the link does not take within 100 ms is dropped too. Both give Notice 3 and a gap in `seq`. Stopping the stream or closing the transport empties the queue and `seq` restarts at 0. The mic stops the moment the stream stops or the transport closes. |
| BATTERY | `percent` is an estimate from the cell voltage and never jumps upward on battery. |
| TEMP | The chip's own temperature sensor, 0.25 degree steps, a few degrees accuracy. It reads above room temperature because the chip warms itself. It is not skin temperature. |
| MIC_LEVEL | Measured before Opus compression, so it follows `MIC_SET_GAIN`. Starting it turns the mic on even when AUDIO is off, and lights the mic indicator (section 6) like AUDIO does; no sound leaves the band on this stream. `STREAM_START` returns NOT_AVAILABLE for this stream if the mic or the LED is not working. AUDIO and MIC_LEVEL are independent: either, both or neither may run. |

## 5. Controls

### 5.1 LED (`LED_SET`)

| Field | Values | Default |
|---|---|---|
| r, g, b | 0..255 each | 0 |
| brightness | 0..255, scales all three | 0 |
| pattern | 0 off, 1 solid, 2 blink (half on, half off), 3 breathe, 4 flash (60 ms every period), 5 rainbow (ignores r, g, b) | 0 |
| period_ms | 100..10000; ignored for off and solid | 1000 |

LED priority: mic indicator (section 6), then critical battery (LED off), then `IDENTIFY`,
then the blue blink of an open connect window, then your LED setting. While the
microphone is on (AUDIO or MIC_LEVEL running) the indicator owns the LED and `LED_SET` and
`IDENTIFY` return DENIED; `LED_GET` still returns your stored setting, which comes back when
both streams have stopped.

### 5.2 Motion sensor (`IMU_SET`, `EVENT_SET`)

| Field | Allowed | Default |
|---|---|---|
| rate_hz | 10, 25, 50, 100 | 25 |
| accel_range_g | 2, 4, 8, 16 | 2 |
| gyro_range_dps | 0 (gyro off), 250, 500, 1000, 2000 | 2000 |
| event_mask | bit 0 single tap, 1 double tap, 2 free-fall, 3 tilt, 4 orientation, 5 motion start/stop | 0x3F |
| tap_threshold_mg | 200..8000 | 800 |

`tap_threshold_mg` applies to EVENT frames only. The double-tap that opens the connect window
always uses 800 mg. Above the 2 g range taps behave a little differently (less clipping).

**MOTION2 (`MOTION2_SET`).** `rate_hz` above belongs to MOTION only; MOTION2 has its
own rate. `accel_range_g` and `gyro_range_dps` apply to both motion streams.

| Field | Allowed | Default |
|---|---|---|
| rate_hz (u16) | 10, 25, 50, 100, 208, 416, 833, 1666, as far as the transport allows (below) | 25 |
| sensors | 1 accelerometer, 2 gyroscope, 3 both | 3 |

How much data each setting sends, and where it is allowed. Bytes per second count whole
frames (type byte, 14-byte header, samples) at `max_frame` 244.

| rate_hz | Samples per frame, one sensor / both | Bytes/s, one sensor | Bytes/s, both | Bluetooth | USB |
|---|---|---|---|---|---|
| 10 | 1 / 1 | 210 | 270 | yes | yes |
| 25 | 1 / 1 | 525 | 675 | yes | yes |
| 50 | 2 / 2 | 675 | 975 | yes | yes |
| 100 | 5 / 5 | 900 | 1,500 | yes | yes |
| 208 | 10 / 10 | 1,560 | 2,810 | yes | yes |
| 416 | 20 / 19 | 2,810 | 5,320 | yes | yes |
| 833 | 38 / 19 | 5,330 | 10,650 | one sensor only | yes |
| 1666 | 38 / not allowed | 10,650 | not allowed | no | accelerometer only |

For comparison: AUDIO is about 3,200 bytes a second, MOTION at 100 Hz is 1,270.

| Limit | Value | Why |
|---|---|---|
| Bluetooth | 416 Hz with both sensors, 833 Hz with one (about 5,400 bytes a second) | Bluetooth is shared by every band in the room. This much keeps one band's share of the air about as small as one audio stream, so a room full of bands still works |
| Bluetooth, together with AUDIO | Allowed up to the limit above, but stay at 208 Hz with both sensors or 416 Hz with one unless you see no Notice 3 | Audio and motion share one send queue. If the computer's radio cannot keep up, both lose frames (Notice 3, gaps in `seq` and `sample_no`) |
| USB | 833 Hz with both sensors or the gyroscope alone, 1666 Hz with the accelerometer alone (about 10,700 bytes a second) | The limit is the wiring between the motion sensor and the processor inside the band, not the cable. Any rate with the gyroscope also has to carry the accelerometer inside the band, so 1666 Hz only fits for the accelerometer alone |
| Over the limit | `STREAM_START` of MOTION2, or `MOTION2_SET` while it streams, answers DENIED and nothing changes | While MOTION2 is not streaming, `MOTION2_SET` accepts every value in the table; the limit is checked against the transport that starts the stream. `MOTION2_GET` tells a client the limits of its own transport |
| 1666 Hz and the gyroscope | Never at the same time. MOTION uses the gyroscope unless `gyro_range_dps` is 0, so: while MOTION2 streams at 1666 Hz, `STREAM_START` of MOTION answers DENIED unless `gyro_range_dps` is 0, and once MOTION streams beside it an `IMU_SET` with a `gyro_range_dps` other than 0 answers DENIED. While MOTION streams with `gyro_range_dps` not 0, `STREAM_START` of MOTION2 set to 1666 Hz answers DENIED, and so does `MOTION2_SET` to 1666 Hz while MOTION2 streams. A `STREAM_START` with both bits is judged the same way. On DENIED nothing changes and nothing in the mask starts | The same wiring limit as in the USB row: MOTION switches the gyroscope on. Over Bluetooth 1666 Hz is over the limit anyway |

### 5.3 Microphone (`MIC_SET_GAIN`)

| Field | Values | Default |
|---|---|---|
| gain_half_db | 0..80 in 0.5 dB steps; 40 = 0 dB, 80 = +20 dB | 80 |

A gain change applies within one audio frame. The Opus settings are fixed.

### 5.4 Radio (`RADIO_SET_TX_POWER`)

| Field | Values | Default |
|---|---|---|
| dbm | -20, -16, -12, -8, -4, 0, +4 | -8 |

Applies to the current connection and returns to -8 dBm on every Bluetooth disconnect. Advertising always uses -8 dBm.

### 5.5 Storage (`STORE_*`)

| Item | Value |
|---|---|
| Model | Key-value. `key` is a number 0..999; a value is 1..232 bytes and no more than `max_frame` - 5. A put replaces the old value |
| Capacity | `STORE_INFO.free_bytes` is 242,164 when the store is empty. Each key costs 8 bytes plus its value rounded up to a multiple of 4 bytes. 1000 keys of 232 bytes fit (240,000 bytes) |
| Limits | 10 writes (`STORE_PUT`, `STORE_DEL`) per second; one more returns BUSY and changes nothing: wait a moment and send it again. `key` above 999 returns BAD_ARG; a value over 232 bytes returns BAD_LENGTH |
| After the band starts | Storage commands return BUSY for a short time while the store gets ready. Send the command again |
| Wipe | `STORE_WIPE` returns OK when the erase starts. Until it finishes, every other storage command returns BUSY. Poll `STORE_INFO` until it stops returning BUSY: OK means the store is empty and ready. NOT_AVAILABLE afterwards means the erase failed: send `STORE_WIPE` again |
| Lifetime | Survives disconnect, restart and other users, until `STORE_WIPE`. **Anything stored can be read, changed or wiped by the next person who connects: do not store anything private.** |

### 5.6 Device

| Control | Entry |
|---|---|
| Band number get / set | `GET_INFO.band_no`, `SET_BAND_NUMBER` (USB only) |
| Name | `GET_NAME` |
| Uptime | `GET_INFO.uptime_ms` |
| Firmware version | `GET_INFO.fw_*` (1.1.0), Device Information firmware revision `open-1.1.0` |
| Restart | `RESTART` |
| Sleep | `SLEEP` |

### 5.7 Stream intervals (`STREAM_SET_INTERVAL`)

One command sets how often a stream sends, for every stream that is sent on a timer.
`stream` is the stream's bit number, `interval_ms` the time between two frames. The new
interval counts from the moment it is set (for BATTERY: from the last frame sent); it may be set before the stream starts or while
it runs. Like every setting in section 5 it returns to its default when no transport is open.

| Stream | `interval_ms` allowed | Default | What it changes |
|---|---|---|---|
| 2 ACTIVITY | 100..3,600,000 | 1000 | Time between frames. Steps, cadence and state are still worked out once a second; `motion_mg` is fresh in every frame |
| 4 BATTERY | 0, or 1000..3,600,000 | 0 | 0 = one frame at start, then only when something changes, at most once a second. Any other value = one frame at start, then one every interval whether or not anything changed. The battery is measured about once a second, so a frame goes out at the first measurement after the interval has passed |
| 5 TEMP | 100..3,600,000 | 5000 | Time between frames. `TEMP_SET_INTERVAL` sets the same value in whole seconds |
| 6 LINK | 100..3,600,000 | 1000 | Time between frames (one signal strength reading each) |
| 7 MIC_LEVEL | 20..10,000, a multiple of 20 | 100 | Time between frames **and** how much sound each frame measures: `peak_db` and `rms_db` cover the last interval |
| 0 MOTION, 1 EVENT, 3 AUDIO, 8 MOTION2 | none: BAD_ARG | | Use `IMU_SET` for MOTION, `MOTION2_SET` for MOTION2 and the `EVENT_SET` mask for EVENT. AUDIO is always 50 frames a second |

`min_ms` and `max_ms` in the response are the limits of that row (for BATTERY 1000 and
3,600,000; 0 is allowed as well), so a client can build a slider without this table.

### 5.8 Clock (`TIME_SET`, `TIME_GET`)

| Item | Value |
|---|---|
| What the band has | No clock with its own battery. It counts milliseconds since it started: that is `t_ms`, the stamp in every stream, and `GET_INFO.uptime_ms` is the same counter. `TIME_SET` tells it the real time once; from then on it knows real time = that value + the milliseconds counted since |
| Format | `unix_ms`: milliseconds since 1970-01-01 00:00:00 UTC, as a `u64` (JavaScript `Date.now()`, Python `int(time.time() * 1000)`). No time zone |
| How long it lasts | Kept across disconnects, for the next client and on both transports, for as long as the band stays switched on and running. Lost when the band restarts (`RESTART`, the reset button, a restart by the watchdog) or its battery runs empty: `TIME_GET` then answers `set` 0 and `unix_ms` 0. Closing every transport does not reset it |
| Who may set it | Any client, at any time; the last `TIME_SET` wins. Nothing protects it: trust the band's clock as much as the stored data in section 5.5 |
| Accuracy | As good as the client's own clock at `TIME_SET`, plus the time the command took to arrive (usually under 50 ms over Bluetooth, a few ms over USB), plus the drift of the band's counter (expect about 2 s a day). Set it on every connect |
| Streams without `t_ms` | AUDIO: note the time right after `STREAM_START`; frame time = that + about 140 ms + `seq` x 20 ms (section 4). BATTERY: the time the frame arrives |
| No clock set | Everything else works as before. A client can still convert on its own: take its own clock at the moment a `GET_INFO` or `TIME_GET` response arrives and pair it with the `uptime_ms` / `t_ms` in that response |
| MOTION2 | `t_ms + i x period_us / 1000` is band time with steps smaller than a millisecond; convert it the same way |

**Converting a stream timestamp to real time.** Send `TIME_GET` once (or use the `TIME_SET`
response). It gives `unix_ms` and `t_ms` for the same moment. For a frame stamped `T`:

    d = (T - t_ms) mod 2^32            (a number 0 .. 2^32 - 1)
    if d >= 2^31:  d = d - 2^32        (the frame is from before that moment)
    real time in ms = unix_ms + d

This is right for frames from before and after the `TIME_GET`, up to 24 days either way,
and across the wrap of `t_ms`. One `TIME_GET` per connection is enough: the difference
between `unix_ms` and `t_ms` only changes when someone sends `TIME_SET`.

## 6. Safety rules you cannot change

| Item | Rule |
|---|---|
| Mic indicator | Whenever the microphone is on, on either transport, the LED is solid blue at full brightness: while AUDIO streams, while MIC_LEVEL streams, or both. It is lit before the first audio or level frame leaves and goes off only after the microphone is off. `LED_SET` and `IDENTIFY` return DENIED while it is lit. If the LED is not working, neither stream starts (NOT_AVAILABLE). |
| Low battery | On battery below 3.5 V: LED brightness capped at 64 (also for `IDENTIFY` and the connect-window blink, not for the mic indicator), Notice 1. Ends at 3.6 V or on USB power |
| Critical battery | On battery below 3.3 V: streams stop, LED off, Notice 2. `LED_SET`, `IDENTIFY` and `STREAM_START` of BATTERY or TEMP return DENIED. A connected client stays connected. Once it has left, the band does not advertise and ignores double-taps until it is on USB power |
| Radio power | Capped at +4 dBm |
| Battery and charging circuit | Not controllable by any command |
| Generic access | No command reads or writes pins, registers, memory, or storage outside the key-value store |
| Firmware | Not writable by any command. No command and no serial setting enters the bootloader |
| On disconnect | That transport's streams stop. When no transport is left open: mic off, LED off, settings back to defaults (section 2.3) |
| Watchdog | A band that hangs restarts itself and opens a 60 s window |

## 7. What the band can do

| Capability | Read | Control |
|---|---|---|
| Accelerometer | MOTION stream, MOTION2 stream | `IMU_SET` rate, range; `MOTION2_SET` rate, sensors |
| Gyroscope | MOTION stream, MOTION2 stream | `IMU_SET` range, off; `MOTION2_SET` rate, sensors |
| Tap, double-tap | EVENT 1, 2 | `EVENT_SET` mask, threshold |
| Free-fall, tilt, orientation, motion | EVENT 3..7 | `EVENT_SET` mask |
| Steps | ACTIVITY stream | start / stop |
| Microphone | AUDIO stream (Opus), MIC_LEVEL stream | start / stop, `MIC_SET_GAIN` |
| RGB LED | `LED_GET` | `LED_SET`, `IDENTIFY` |
| Battery | BATTERY stream, `BATTERY_GET` | none |
| Chip temperature | TEMP stream | `TEMP_SET_INTERVAL` |
| Signal strength, radio power | LINK stream, `RADIO_GET` | `RADIO_SET_TX_POWER` |
| Storage | `STORE_GET`, `STORE_INFO` | `STORE_PUT`, `STORE_DEL`, `STORE_WIPE` |
| Identity | `GET_INFO`, `GET_NAME` | `SET_BAND_NUMBER` (USB only) |
| Device state | `GET_INFO`, `STREAM_GET` | `RESTART`, `SLEEP` |
| Stream intervals | `STREAM_GET_INTERVAL` | `STREAM_SET_INTERVAL`, `TEMP_SET_INTERVAL` |
| Clock | `TIME_GET` | `TIME_SET` |

Not on this band: heart rate, skin temperature, vibration, NFC, spare pins.

Shared test vectors for client authors: [`test-vectors.json`](test-vectors.json).
