# The Bluetooth contract, explained

How to use a Vexo Open band, what every packet looks like, and how to get every kind of data out of it. This page is for people: it explains, with real examples. The exact, complete rules are in [contract/contract.md](../contract/contract.md); every number here comes from there.

> **42 is only our example. Wherever you see 42 in a command, an address or a name, type the
> number written on your band's bag.** Band 7 is `Vexo-HACK-07`, and its dashboard address
> ends in `?band=7`.

There is a visual version of this page: [bluetooth-contract.html](bluetooth-contract.html). It draws every frame as coloured boxes and decodes any frame you paste into it. Open it from the kit's web server at <http://localhost:8000/vexo-open-kit/docs/bluetooth-contract.html> or by double-click.

You do not need this page to use the band. The kit's libraries ([api.md](api.md)) do all of it for you. Read on if you want to understand what happens underneath, or write your own Bluetooth code in any language.

## The band in one minute

| On the band | What you get from it |
|---|---|
| Motion sensor | Acceleration and turning speed, taps, steps |
| Microphone | Loudness, and the sound itself |
| Colour light (LED) | Any colour, six patterns |
| Battery, chip temperature, radio | Battery level, temperature, signal strength |
| A little storage | 1000 small values that survive a restart |

The rules that matter:

- **Your band has a number.** It is written on the bag the band came in. The band's Bluetooth name is `Vexo-HACK-` plus the number: 2 digits for 1 to 99 (`Vexo-HACK-07`, `Vexo-HACK-42`), 3 digits from 100 (`Vexo-HACK-123`).
- **Double-tap to connect.** A band that nobody is connected to is silent. Double-tap it: its light blinks blue once a second and it can be found for 60 seconds. Another double-tap starts the 60 seconds again.
- **One Bluetooth connection at a time.** While one program is connected, nobody else can find the band. Close the dashboard tab before you run your own program.
- **No pairing, no password.** Anyone nearby can connect to a band that was just double-tapped. So always connect to your own band, by name.
- **Everything is off after you connect.** You switch on the data you want. When you disconnect, all data stops, the light goes off and every setting goes back to its default. Only storage and the band number are kept.
- **The microphone shows a solid blue light.** While the band sends sound or loudness, its light is solid blue and locked: you cannot change it. People nearby can see that the microphone is on.
- **The band number is set over the USB cable only.** Over Bluetooth that command is refused.

The first connection with the kit, step by step, is in [Getting started](getting-started.md).

## Connecting with your own Bluetooth code

**Always filter for your own band's name. Never connect to the first Vexo band you see.** With about 100 bands in the room, a scan without a filter is slow, and you will grab someone else's band.

The kit's libraries already do this: `Band.connect(42)` looks only for `Vexo-HACK-42`. If you write the Bluetooth part yourself, in any language, these are the steps:

1. **Build the name from your number.** `Vexo-HACK-` + the number with at least 2 digits: 7 gives `Vexo-HACK-07`, 42 gives `Vexo-HACK-42`, 123 gives `Vexo-HACK-123`.
2. **Double-tap the band.** Its light blinks blue; you have 60 seconds.
3. **Scan with a filter**: the service UUID `254f0001-df82-4a9c-a2b5-563a0f6b2021` and the exact name. Scan for at least 5 seconds before you say "not found": the name arrives in a second packet (the scan response), a moment after the first.
4. **Connect.** No pairing: the band rejects a pairing request.
5. **Find the service and its two characteristics** (the table in [The link](#the-link)).
6. **Switch on notifications on TX**, once, before your first command. Every answer and all data arrive there.
7. **Write your first command to RX**: `01 00 01` is `GET_INFO`. Check that `band_no` in the answer is your number. Send one command, wait for its response (up to 2 seconds), then send the next.
8. **When the link drops**: all streams have stopped and every setting is back at its default. After a dropped link the band can be found again for 120 seconds without a tap. Scan for the same name, connect, switch on notifications, and set everything up again. After that time, double-tap.

**In a web page (Web Bluetooth, Chrome or Edge).** The page must come from `localhost` or `https`, and the connect must start from a click.

```js
const BAND = 42;                                        // your band number
const NAME = 'Vexo-HACK-' + String(BAND).padStart(2, '0');   // 7 -> Vexo-HACK-07, 123 -> Vexo-HACK-123
const SERVICE = '254f0001-df82-4a9c-a2b5-563a0f6b2021';
const RX = '254f0002-df82-4a9c-a2b5-563a0f6b2021';      // you write commands here
const TX = '254f0003-df82-4a9c-a2b5-563a0f6b2021';      // the band notifies here

async function connect() {                              // call this from a click on a button
  const device = await navigator.bluetooth.requestDevice({
    filters: [{ name: NAME, services: [SERVICE] }],     // only your own band can appear in the list
    optionalServices: [SERVICE],
  });
  device.addEventListener('gattserverdisconnected', () => console.log('the link dropped'));
  const server = await device.gatt.connect();
  const service = await server.getPrimaryService(SERVICE);
  const rx = await service.getCharacteristic(RX);
  const tx = await service.getCharacteristic(TX);
  tx.addEventListener('characteristicvaluechanged', (event) => {
    const v = event.target.value;
    const frame = new Uint8Array(v.buffer, v.byteOffset, v.byteLength);
    console.log('frame', Array.from(frame, (b) => b.toString(16).padStart(2, '0')).join(' '));
  });
  await tx.startNotifications();                        // listen first, then ask
  await rx.writeValueWithResponse(Uint8Array.of(0x01, 0x00, 0x01));   // GET_INFO with seq 0
  return { device, rx, tx };
}
```

**In Python (bleak).** Install it with `python3 -m pip install bleak`.

```python
import asyncio
from bleak import BleakClient, BleakScanner

BAND = 42                                      # your band number
NAME = "Vexo-HACK-%02d" % BAND                 # 7 -> Vexo-HACK-07, 123 -> Vexo-HACK-123
SERVICE = "254f0001-df82-4a9c-a2b5-563a0f6b2021"
RX = "254f0002-df82-4a9c-a2b5-563a0f6b2021"    # you write commands here
TX = "254f0003-df82-4a9c-a2b5-563a0f6b2021"    # the band notifies here

async def main():
    # only your own band: the Vexo Open service and exactly your band's name
    device = await BleakScanner.find_device_by_filter(
        lambda d, adv: adv.local_name == NAME, timeout=10, service_uuids=[SERVICE])
    if device is None:
        raise SystemExit("Could not see %s. Double-tap it and try again." % NAME)
    async with BleakClient(device) as client:
        await client.start_notify(TX, lambda _, data: print("frame", bytes(data).hex(" ")))   # listen first
        await client.write_gatt_char(RX, bytes([0x01, 0x00, 0x01]), response=True)            # GET_INFO with seq 0
        await asyncio.sleep(1)

asyncio.run(main())
```

**Other platforms.** The idea is the same everywhere: scan for the service UUID, accept only your exact name.

- **Android (Kotlin or Java):** give the scanner a `ScanFilter` with the device name `Vexo-HACK-42` (and the service UUID), then `connectGatt`, discover services, enable notifications on TX, write to RX.
- **iPhone, iPad, Mac (Swift, CoreBluetooth):** scan with `scanForPeripherals(withServices:)` for the service UUID, and connect only to the peripheral whose advertised local name is exactly `Vexo-HACK-42`.
- **Anything else** (Unity, Flutter, Rust, an ESP32): filter on the name the same way. If the name has not arrived yet, the band number is also in the first packet as manufacturer data (company `0xFFFF`): the bytes `4F 01` and then the band number as a `u16`.

The two code examples above have been run against a pretend Bluetooth layer, not against a real band. The details of what the band sends while it can be found are in [1. Finding and connecting](../contract/contract.md#1-finding-and-connecting).

## The link

    your computer  -- writes commands to RX (254f0002) -->  band
    your computer  <-- TX (254f0003) notifies: responses, notices, data --  band

The band offers one Bluetooth service with two characteristics. Think of them as two pipes: one into the band, one out of it.

| What | UUID | Direction | Use |
|---|---|---|---|
| Vexo Open service | `254f0001-df82-4a9c-a2b5-563a0f6b2021` |  | The service to look for |
| RX | `254f0002-df82-4a9c-a2b5-563a0f6b2021` | you write | You to the band: one frame (a command) per write |
| TX | `254f0003-df82-4a9c-a2b5-563a0f6b2021` | the band notifies | The band to you: one frame per notification (responses, notices, data) |

The three UUIDs differ only in the fourth digit: 1, 2, 3. The band also has the standard Device Information service (`0x180A`) with its firmware version, and nothing else.

**The same frames over the USB cable.** Plugged in, the band shows up as a serial port. It speaks exactly the same frames, with no double-tap needed. Because a serial port is one long line of bytes, each frame is wrapped so that the end can be found: it is COBS-encoded (a way to remove every `00` byte from it) and followed by one `00`. Example: the frame `01 00 01` travels as `02 01 02 01 00`. Everything you need to write your own USB client is in [2.2 USB cable](../contract/contract.md#22-usb-cable). A program on the cable and one on Bluetooth can use the band at the same time: [2.3 Both transports at once](../contract/contract.md#23-both-transports-at-once).

## How a frame is built

Everything that travels is a **frame**: one type byte, then a body. A frame is 1 to 244 bytes long.

    [type] [body ...]

| Type | Frame | Body | Who sends it |
|---|---|---|---|
| `0x01` | Command | `seq`, `opcode`, then the arguments | you |
| `0x02` | Response | `seq`, `opcode`, `status`, then the data | the band, exactly one for every command |
| `0x03` | Notice | `code`, `arg` | the band, by itself when something happens |
| `0x10` to `0x18` | Data (a stream frame) | depends on the stream | the band, while that stream is on |

- **`seq`** is any number from 0 to 255 that you choose. The response repeats it, so you know which command it answers. Use a counter.
- **`opcode`** says which command. The response repeats it too.
- **`status`** is 0 for OK. Anything else is an error, and then no data follows ([Errors and refusals](#errors-and-refusals)).
- **Byte order: little-endian.** A number of more than one byte is sent lowest byte first. The bytes `e8 03` are the number `0x03e8` = 1000. The bytes `40 e2 01 00` are `0x0001e240` = 123456.
- **Types.** `u8`, `u16`, `u32`, `u64` are whole numbers of 1, 2, 4 and 8 bytes that cannot be negative. `i8`, `i16`, `i32` can be negative (two's complement): the bytes `18 fc` as an `i16` are -1000.
- **No padding.** The fields follow each other with nothing in between.
- **Ignore what you do not know.** A frame type or an event code you have never seen is not an error: skip it.

**A worked example: make the light green.** You send this command (10 bytes):

| Offset | Bytes | Field | Value | Meaning |
|---|---|---|---|---|
| 0 | `01` | `type` | 1 | a command |
| 1 | `07` | `seq` | 7 | our counter is at 7 |
| 2 | `50` | `opcode` | "LED_SET" | `0x50` is `LED_SET` |
| 3 | `00` | `r` | 0 | no red |
| 4 | `ff` | `g` | 255 | full green |
| 5 | `00` | `b` | 0 | no blue |
| 6 | `80` | `brightness` | 128 | 128 of 255: half |
| 7 | `01` | `pattern` | 1 | 1 = solid |
| 8 | `e8 03` | `period_ms` | 1000 | `e8 03` = `0x03e8` = 1000 ms (not used for solid) |

Example. The command: `01075000ff008001e803` decodes to: `seq` 7, `opcode` "LED_SET", `r` 0, `g` 255, `b` 0, `brightness` 128, `pattern` 1 (solid), `period_ms` 1000 (1000 ms).

The band answers with 4 bytes: a response (`02`), to our command number 7 (`07`), which was `LED_SET` (`50`), status OK (`00`). `LED_SET` has no data, so that is all.

Example. The response: `02075000` decodes to: `seq` 7, `opcode` "LED_SET", `status` "OK".

The visual page has a box where you paste any frame as hex and see it decoded.

## How to get each kind of data

One block per kind of data. Each has the same parts: what it is, how to switch it on, the layout of the frame, a real example, and the short way with the kit's library (42 = your band number; the library lines assume `band = Band.connect(42)` as in [api.md](api.md)).

### Switching data on and off

Every kind of data is a **stream**. All streams are off after you connect. `STREAM_START` switches streams on, `STREAM_STOP` switches them off, `STREAM_GET` tells you which ones are on. Each stream has a bit number; the `mask` is the sum of the bits you want: bit 0 motion (1), bit 1 event (2), bit 2 activity (4), bit 3 audio (8), bit 4 battery (16), bit 5 temp (32), bit 6 link (64), bit 7 mic_level (128), bit 8 motion2 (256). Stream bit n arrives as frame type `0x10` + n.

**Switch it on.** Send `STREAM_START` with the mask. The answer `active` shows everything that is on now.

**`STREAM_START` command (you send this)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x01: a command |
| 1 | 1 | u8 | `seq` | any number you choose; use a counter |
| 2 | 1 | u8 | `opcode` | 0x10 = STREAM_START |
| 3 | 2 | u16 | `mask` | one bit per stream |

Example. Start motion + event + battery + mic_level: 1 + 2 + 16 + 128 = 147 = `0x0093`: `0108109300` decodes to: `seq` 8, `opcode` "STREAM_START", `mask` 147 (motion + event + battery + mic_level).

**`STREAM_START` response (the band answers)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x02: a response |
| 1 | 1 | u8 | `seq` | the `seq` of the command it answers |
| 2 | 1 | u8 | `opcode` | 0x10 = STREAM_START |
| 3 | 1 | u8 | `status` | 0 = OK; anything else is an error and no data follows |
| 4 | 2 | u16 | `active` | one bit per stream that is on |

Example. The answer: those four are on: `020810009300` decodes to: `seq` 8, `opcode` "STREAM_START", `status` "OK", `active` 147 (motion + event + battery + mic_level).

**`STREAM_STOP` command (you send this)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x01: a command |
| 1 | 1 | u8 | `seq` | any number you choose; use a counter |
| 2 | 1 | u8 | `opcode` | 0x11 = STREAM_STOP |
| 3 | 2 | u16 | `mask` | one bit per stream |

Example. Stop motion: `010a110100` decodes to: `seq` 10, `opcode` "STREAM_STOP", `mask` 1 (motion).

**`STREAM_STOP` response (the band answers)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x02: a response |
| 1 | 1 | u8 | `seq` | the `seq` of the command it answers |
| 2 | 1 | u8 | `opcode` | 0x11 = STREAM_STOP |
| 3 | 1 | u8 | `status` | 0 = OK; anything else is an error and no data follows |
| 4 | 2 | u16 | `active` | one bit per stream that is on |

Example. The answer: three are still on: `020a11009200` decodes to: `seq` 10, `opcode` "STREAM_STOP", `status` "OK", `active` 146 (event + battery + mic_level).

**With the kit's library.** JavaScript, then Python:

```js
await band.configure({ motion: 50, events: true, battery: true });
console.log(band.streams);
```

```python
band.configure(motion=50, events=True, battery=True)
print(band.streams)
```

- The library call `configure` sends the right `STREAM_START` and `STREAM_STOP` for you.
- A bit for a part that is not working, or the link bit over USB, gives NOT_AVAILABLE and nothing starts.

More detail: [4. Streams](../contract/contract.md#4-streams) in the contract.

### Motion

*MOTION, type 0x10, bit 0*

The accelerometer (which way is down, and how hard the band is pushed) and the gyroscope (how fast it turns). Rates: 10, 25 (default), 50 or 100 samples per second. A frame carries up to 10 samples. Acceleration is in milli-g (1000 = 1 g, the pull of the earth); turning speed is in tenths of a degree per second. Sample i was taken at `t_ms` + i x `period_ms`.

**Switch it on.** Optional: `IMU_SET` to choose the rate and the ranges. Then `STREAM_START` with bit 0 (mask 1).

**`MOTION` frame (the band sends this)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x10: MOTION |
| 1 | 4 | u32 | `t_ms` | ms since the band started |
| 5 | 1 | u8 | `period_ms` | ms between two samples |
| 6 | 1 | u8 | `count` | samples in this frame |
| 7 + 12 x i | 12 | 6 x i16 | `ax, ay, az, gx, gy, gz` | sample i: milli-g, then tenths of a degree per second |

Example. Two samples, 40 ms apart (25 samples per second): `1040e201002802f40118fcfa007d0030f8000080c1803e0100204ee0b1ffff` decodes to: `t_ms` 123456 (123456 ms), `period_ms` 40 (40 ms), `count` 2, `sample[0].ax` 500 (0.5 g), `sample[0].ay` -1000 (-1 g), `sample[0].az` 250 (0.25 g), `sample[0].gx` 125 (12.5 degrees per second), `sample[0].gy` -2000 (-200 degrees per second), `sample[0].gz` 0 (0 degrees per second), `sample[1].ax` -16000 (-16 g), `sample[1].ay` 16000 (16 g), `sample[1].az` 1 (0.001 g), `sample[1].gx` 20000 (2000 degrees per second), `sample[1].gy` -20000 (-2000 degrees per second), `sample[1].gz` -1 (-0.1 degrees per second).

**`IMU_SET` command (you send this)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x01: a command |
| 1 | 1 | u8 | `seq` | any number you choose; use a counter |
| 2 | 1 | u8 | `opcode` | 0x20 = IMU_SET |
| 3 | 1 | u8 | `rate_hz` | samples per second: 10, 25, 50 or 100 |
| 4 | 1 | u8 | `accel_range_g` | g: 2, 4, 8 or 16 |
| 5 | 2 | u16 | `gyro_range_dps` | degrees per second: 0 (off), 250, 500, 1000 or 2000 |

Example. Set 100 samples per second, 8 g range, 500 degrees per second range: `010c206408f401` decodes to: `seq` 12, `opcode` "IMU_SET", `rate_hz` 100, `accel_range_g` 8, `gyro_range_dps` 500.

**`IMU_SET` response (the band answers)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x02: a response |
| 1 | 1 | u8 | `seq` | the `seq` of the command it answers |
| 2 | 1 | u8 | `opcode` | 0x20 = IMU_SET |
| 3 | 1 | u8 | `status` | 0 = OK; anything else is an error and no data follows |
| 4 | 1 | u8 | `rate_hz` | samples per second: 10, 25, 50 or 100 |
| 5 | 1 | u8 | `accel_range_g` | g: 2, 4, 8 or 16 |
| 6 | 2 | u16 | `gyro_range_dps` | degrees per second: 0 (off), 250, 500, 1000 or 2000 |

Example. The answer repeats what was applied: `020c20006408f401` decodes to: `seq` 12, `opcode` "IMU_SET", `status` "OK", `rate_hz` 100, `accel_range_g` 8, `gyro_range_dps` 500.

**With the kit's library.** JavaScript, then Python:

```js
band.on('motion', (m) => console.log(m.ax, m.ay, m.az, m.gx, m.gy, m.gz));
await band.configure({ motion: 50 });
```

```python
band.on("motion", lambda m: print(m.ax, m.ay, m.az, m.gx, m.gy, m.gz))
band.configure(motion=50)
```

- The library gives you one event per sample, already in g and in degrees per second.
- After every connect the ranges are 2 g and 2000 degrees per second. A harder push reads exactly 2 g until you widen the range.
- The library uses the faster stream below for `motion`; the events look the same.

More detail: [5.2 Motion sensor](../contract/contract.md#52-motion-sensor-imu_set-event_set) in the contract.

### Fast motion

*MOTION2, type 0x18, bit 8*

A second motion stream. You choose the sensors (accelerometer, gyroscope or both) and the rate: 10, 25 (default), 50, 100, 208, 416, 833 or 1666 samples per second. Over Bluetooth the limit is 416 with both sensors and 833 with one; 1666 works only over USB with the accelerometer alone. Every sample has a number: sample i is number `sample_no` + i and was taken at `t_ms` + i x `period_us` / 1000. A jump in `sample_no` is exactly the number of samples lost.

**Switch it on.** `MOTION2_SET` with the rate and the sensors, then `STREAM_START` with bit 8 (mask 256).

**`MOTION2` frame (the band sends this)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x18: MOTION2 |
| 1 | 4 | u32 | `t_ms` | ms since the band started |
| 5 | 4 | u32 | `sample_no` | number of the first sample in this frame |
| 9 | 4 | u32 | `period_us` | microseconds between two samples, measured |
| 13 | 1 | u8 | `sensors` | 1 accelerometer, 2 gyroscope, 3 both |
| 14 | 1 | u8 | `count` | samples in this frame |
| 15 + size x i | 6 or 12 | 3 or 6 x i16 | `ax, ay, az and/or gx, gy, gz` | sample i: 6 bytes with one sensor, 12 with both; units as MOTION |

Example. Both sensors at 25 samples per second: one sample of 12 bytes: `188813000000000000409c00000301f40118fcfa007d0030f80000` decodes to: `t_ms` 5000 (5000 ms), `sample_no` 0, `period_us` 40000, `sensors` 3 (both), `count` 1, `sample[0].ax` 500 (0.5 g), `sample[0].ay` -1000 (-1 g), `sample[0].az` 250 (0.25 g), `sample[0].gx` 125 (12.5 degrees per second), `sample[0].gy` -2000 (-200 degrees per second), `sample[0].gz` 0 (0 degrees per second).

Example. Accelerometer only at 416 samples per second: three samples of 6 bytes: `1840e20100400300006409000001030a00ecffe8030b00ebffe90380c1803e0100` decodes to: `t_ms` 123456 (123456 ms), `sample_no` 832, `period_us` 2404, `sensors` 1 (accelerometer), `count` 3, `sample[0].ax` 10 (0.01 g), `sample[0].ay` -20 (-0.02 g), `sample[0].az` 1000 (1 g), `sample[1].ax` 11 (0.011 g), `sample[1].ay` -21 (-0.021 g), `sample[1].az` 1001 (1.001 g), `sample[2].ax` -16000 (-16 g), `sample[2].ay` 16000 (16 g), `sample[2].az` 1 (0.001 g).

**`MOTION2_SET` command (you send this)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x01: a command |
| 1 | 1 | u8 | `seq` | any number you choose; use a counter |
| 2 | 1 | u8 | `opcode` | 0x24 = MOTION2_SET |
| 3 | 2 | u16 | `rate_hz` | samples per second |
| 5 | 1 | u8 | `sensors` | 1 accelerometer, 2 gyroscope, 3 both |

Example. Set 416 samples per second, accelerometer only: `013724a00101` decodes to: `seq` 55, `opcode` "MOTION2_SET", `rate_hz` 416, `sensors` 1 (accelerometer).

**`MOTION2_SET` response (the band answers)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x02: a response |
| 1 | 1 | u8 | `seq` | the `seq` of the command it answers |
| 2 | 1 | u8 | `opcode` | 0x24 = MOTION2_SET |
| 3 | 1 | u8 | `status` | 0 = OK; anything else is an error and no data follows |
| 4 | 2 | u16 | `rate_hz` | samples per second |
| 6 | 1 | u8 | `sensors` | 1 accelerometer, 2 gyroscope, 3 both |
| 7 | 2 | u16 | `max_accel_hz` | fastest rate, accelerometer alone |
| 9 | 2 | u16 | `max_gyro_hz` | fastest rate, gyroscope alone |
| 11 | 2 | u16 | `max_both_hz` | fastest rate, both |

Example. The answer also gives the fastest rates of this link (here Bluetooth): `02372400a0010141034103a001` decodes to: `seq` 55, `opcode` "MOTION2_SET", `status` "OK", `rate_hz` 416, `sensors` 1 (accelerometer), `max_accel_hz` 833, `max_gyro_hz` 833, `max_both_hz` 416.

**With the kit's library.** JavaScript, then Python:

```js
band.on('motion', (m) => console.log(m.n, m.t, m.ax, m.ay, m.az));
await band.configure({ motion: 416, sensors: 'accel' });
```

```python
band.on("motion", lambda m: print(m.n, m.t, m.ax, m.ay, m.az))
band.configure(motion=416, sensors="accel")
```

- Place samples in time with `t_ms` and `period_us`, never with the nominal rate: `period_us` is measured by the band.
- A rate that is too fast for the link is answered with DENIED and nothing changes.

More detail: [5.2 Motion sensor (the MOTION2 tables)](../contract/contract.md#52-motion-sensor-imu_set-event_set) in the contract.

### Taps and events

*EVENT, type 0x11, bit 1*

The band recognises seven things by itself and sends one small frame when one happens: 1 single tap, 2 double tap, 3 free-fall, 4 tilt, 5 orientation changed, 6 motion started, 7 motion stopped. For orientation, `arg` is the face pointing up: 0 +X, 1 -X, 2 +Y, 3 -Y, 4 +Z, 5 -Z. For the others `arg` is 0.

**Switch it on.** `STREAM_START` with bit 1 (mask 2). Optional: `EVENT_SET` to choose which events you get and how hard a tap must be (`tap_threshold_mg`, 200..8000, default 800).

**`EVENT` frame (the band sends this)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x11: EVENT |
| 1 | 4 | u32 | `t_ms` | ms since the band started |
| 5 | 1 | u8 | `event` | 1 tap, 2 double tap, 3 free-fall, 4 tilt, 5 orientation, 6 motion started, 7 motion stopped |
| 6 | 1 | u8 | `arg` | for orientation: the face pointing up; otherwise 0 |

Example. A double tap, 5.002 s after the band started: `118a1300000200` decodes to: `t_ms` 5002 (5002 ms), `event` 2 (double tap), `arg` 0.

Example. Orientation changed: face 4 (+Z) points up: `118d1300000504` decodes to: `t_ms` 5005 (5005 ms), `event` 5 (orientation changed), `arg` 4.

**`EVENT_SET` command (you send this)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x01: a command |
| 1 | 1 | u8 | `seq` | any number you choose; use a counter |
| 2 | 1 | u8 | `opcode` | 0x22 = EVENT_SET |
| 3 | 1 | u8 | `event_mask` | bit 0 single tap, 1 double tap, 2 free-fall, 3 tilt, 4 orientation, 5 motion start/stop |
| 4 | 2 | u16 | `tap_threshold_mg` | milli-g, 200..8000 |

Example. Only single and double taps (mask 3), threshold 1200 milli-g: `010f2203b004` decodes to: `seq` 15, `opcode` "EVENT_SET", `event_mask` 3, `tap_threshold_mg` 1200.

**`EVENT_SET` response (the band answers)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x02: a response |
| 1 | 1 | u8 | `seq` | the `seq` of the command it answers |
| 2 | 1 | u8 | `opcode` | 0x22 = EVENT_SET |
| 3 | 1 | u8 | `status` | 0 = OK; anything else is an error and no data follows |
| 4 | 1 | u8 | `event_mask` | bit 0 single tap, 1 double tap, 2 free-fall, 3 tilt, 4 orientation, 5 motion start/stop |
| 5 | 2 | u16 | `tap_threshold_mg` | milli-g, 200..8000 |

Example. The answer repeats what was applied: `020f220003b004` decodes to: `seq` 15, `opcode` "EVENT_SET", `status` "OK", `event_mask` 3, `tap_threshold_mg` 1200.

**With the kit's library.** JavaScript, then Python:

```js
band.on('double-tap', (e) => console.log('double tap at', e.t));
await band.configure({ events: true });
```

```python
band.on("double-tap", lambda e: print("double tap at", e.t))
band.configure(events=True)
```

- Library event names: `tap`, `double-tap`, `free-fall`, `tilt`, `orientation`, `motion-start`, `motion-stop`, or `event` for all of them.
- The double tap that opens the connect window always uses 800 milli-g, whatever you set.

More detail: [4. Streams (the EVENT row under "Details")](../contract/contract.md#4-streams) in the contract.

### Steps and activity

*ACTIVITY, type 0x12, bit 2*

A step counter, the walking pace, and whether the wearer is still, walking or running. One frame a second by default. `steps_total` is a lifetime count that survives restarts: subtract the first value you see to get the steps since you connected.

**Switch it on.** `STREAM_START` with bit 2 (mask 4).

**`ACTIVITY` frame (the band sends this)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x12: ACTIVITY |
| 1 | 4 | u32 | `t_ms` | ms since the band started |
| 5 | 4 | u32 | `steps_total` | steps, lifetime count |
| 9 | 1 | u8 | `cadence_spm` | steps per minute |
| 10 | 1 | u8 | `state` | 0 still, 1 walk, 2 run |
| 11 | 2 | u16 | `motion_mg` | milli-g |

Example. 123456 steps in total, 112 steps a minute, walking: `1260ea000040e2010070015401` decodes to: `t_ms` 60000 (60000 ms), `steps_total` 123456, `cadence_spm` 112, `state` 1 (walk), `motion_mg` 340 (0.34 g).

**With the kit's library.** JavaScript, then Python:

```js
band.on('activity', (a) => console.log(a.steps, a.cadence, a.state));
await band.configure({ activity: true });
```

```python
band.on("activity", lambda a: print(a.steps, a.cadence, a.state))
band.configure(activity=True)
```

More detail: [4. Streams](../contract/contract.md#4-streams) in the contract.

### Battery

*BATTERY, type 0x14, bit 4*

How full the battery is, in percent and in millivolts, and whether the band is charging. As a stream you get one frame at the start and then one when something changes. To read it once, send `BATTERY_GET`: its answer carries the same three fields. This frame has no time stamp.

**Switch it on.** `STREAM_START` with bit 4 (mask 16), or `BATTERY_GET` for one reading.

**`BATTERY` frame (the band sends this)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x14: BATTERY |
| 1 | 1 | u8 | `percent` | % |
| 2 | 1 | u8 | `state` | 0 on battery, 1 charging, 2 charged |
| 3 | 2 | u16 | `millivolts` | mV |

Example. 87 percent, on battery, 3.987 V: `145700930f` decodes to: `percent` 87, `state` 0 (on battery), `millivolts` 3987.

**`BATTERY_GET` command (you send this)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x01: a command |
| 1 | 1 | u8 | `seq` | any number you choose; use a counter |
| 2 | 1 | u8 | `opcode` | 0x61 = BATTERY_GET |

Example. Read the battery once: `011b61` decodes to: `seq` 27, `opcode` "BATTERY_GET".

**`BATTERY_GET` response (the band answers)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x02: a response |
| 1 | 1 | u8 | `seq` | the `seq` of the command it answers |
| 2 | 1 | u8 | `opcode` | 0x61 = BATTERY_GET |
| 3 | 1 | u8 | `status` | 0 = OK; anything else is an error and no data follows |
| 4 | 1 | u8 | `percent` | % |
| 5 | 1 | u8 | `state` | 0 on battery, 1 charging, 2 charged |
| 6 | 2 | u16 | `millivolts` | mV |

Example. The answer: 87 percent, charging, 4.012 V: `021b61005701ac0f` decodes to: `seq` 27, `opcode` "BATTERY_GET", `status` "OK", `percent` 87, `state` 1 (charging), `millivolts` 4012.

**With the kit's library.** JavaScript, then Python:

```js
band.on('battery', (b) => console.log(b.percent, b.millivolts, b.state));
await band.configure({ battery: true });
```

```python
band.on("battery", lambda b: print(b.percent, b.millivolts, b.state))
band.configure(battery=True)
```

- Once, without a stream: `await band.getBattery()` in JavaScript, `band.get_battery()` in Python.

More detail: [4. Streams](../contract/contract.md#4-streams) in the contract.

### Temperature

*TEMP, type 0x15, bit 5*

The temperature of the chip inside the band, in thousandths of a degree C. It reads a few degrees above room temperature because the chip warms itself. It is not skin temperature. One frame every 5 seconds by default.

**Switch it on.** `STREAM_START` with bit 5 (mask 32). Optional: `TEMP_SET_INTERVAL` with the seconds between two readings (1..255).

**`TEMP` frame (the band sends this)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x15: TEMP |
| 1 | 4 | u32 | `t_ms` | ms since the band started |
| 5 | 4 | i32 | `temp_mc` | thousandths of a degree C |

Example. 31.25 degrees C: `1588130000127a0000` decodes to: `t_ms` 5000 (5000 ms), `temp_mc` 31250 (31.25 degrees C).

Example. A negative value: -4.75 degrees C: `158813000072edffff` decodes to: `t_ms` 5000 (5000 ms), `temp_mc` -4750 (-4.75 degrees C).

**`TEMP_SET_INTERVAL` command (you send this)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x01: a command |
| 1 | 1 | u8 | `seq` | any number you choose; use a counter |
| 2 | 1 | u8 | `opcode` | 0x60 = TEMP_SET_INTERVAL |
| 3 | 1 | u8 | `seconds` | s, 1..255 |

Example. One reading every 2 seconds: `011a6002` decodes to: `seq` 26, `opcode` "TEMP_SET_INTERVAL", `seconds` 2.

**`TEMP_SET_INTERVAL` response (the band answers)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x02: a response |
| 1 | 1 | u8 | `seq` | the `seq` of the command it answers |
| 2 | 1 | u8 | `opcode` | 0x60 = TEMP_SET_INTERVAL |
| 3 | 1 | u8 | `status` | 0 = OK; anything else is an error and no data follows |
| 4 | 1 | u8 | `seconds` | s |

Example. The answer: `021a600002` decodes to: `seq` 26, `opcode` "TEMP_SET_INTERVAL", `status` "OK", `seconds` 2.

**With the kit's library.** JavaScript, then Python:

```js
band.on('temperature', (t) => console.log(t.celsius));
await band.configure({ temperature: true });
```

```python
band.on("temperature", lambda t: print(t.celsius))
band.configure(temperature=True)
```

More detail: [4. Streams](../contract/contract.md#4-streams) in the contract.

### Link quality

*LINK, type 0x16, bit 6, Bluetooth only*

How strong the band hears your computer (`rssi_dbm`: closer to 0 is stronger, 127 means no reading), how strongly the band sends (`tx_dbm`) and the radio mode (`phy`: 1 = 1M, 2 = 2M). One frame a second. Bluetooth only: over USB this stream answers NOT_AVAILABLE. `RADIO_GET` reads the same once, with the connection interval and the largest packet size; `RADIO_SET_TX_POWER` changes how strongly the band sends.

**Switch it on.** `STREAM_START` with bit 6 (mask 64), or `RADIO_GET` for one reading.

**`LINK` frame (the band sends this)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x16: LINK |
| 1 | 4 | u32 | `t_ms` | ms since the band started |
| 5 | 1 | i8 | `rssi_dbm` | dBm; 127 = no reading |
| 6 | 1 | i8 | `tx_dbm` | dBm |
| 7 | 1 | u8 | `phy` | 1 = 1M, 2 = 2M |

Example. Signal -63 dBm, sending at -8 dBm, 2M: `16e8030000c1f802` decodes to: `t_ms` 1000 (1000 ms), `rssi_dbm` -63 (-63 dBm), `tx_dbm` -8 (-8 dBm), `phy` 2 (2M).

**`RADIO_GET` command (you send this)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x01: a command |
| 1 | 1 | u8 | `seq` | any number you choose; use a counter |
| 2 | 1 | u8 | `opcode` | 0x41 = RADIO_GET |

Example. Read the radio once: `011541` decodes to: `seq` 21, `opcode` "RADIO_GET".

**`RADIO_GET` response (the band answers)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x02: a response |
| 1 | 1 | u8 | `seq` | the `seq` of the command it answers |
| 2 | 1 | u8 | `opcode` | 0x41 = RADIO_GET |
| 3 | 1 | u8 | `status` | 0 = OK; anything else is an error and no data follows |
| 4 | 1 | i8 | `tx_dbm` | dBm |
| 5 | 1 | i8 | `rssi_dbm` | dBm; 127 = no reading |
| 6 | 1 | u8 | `phy` | 1 = 1M, 2 = 2M |
| 7 | 2 | u16 | `interval` | units of 1.25 ms |
| 9 | 2 | u16 | `mtu` | bytes |

Example. The answer: `02154100f8c7021800f700` decodes to: `seq` 21, `opcode` "RADIO_GET", `status` "OK", `tx_dbm` -8 (-8 dBm), `rssi_dbm` -57 (-57 dBm), `phy` 2 (2M), `interval` 24 (30 ms), `mtu` 247.

**`RADIO_SET_TX_POWER` command (you send this)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x01: a command |
| 1 | 1 | u8 | `seq` | any number you choose; use a counter |
| 2 | 1 | u8 | `opcode` | 0x40 = RADIO_SET_TX_POWER |
| 3 | 1 | i8 | `dbm` | dBm: -20, -16, -12, -8, -4, 0 or 4 |

Example. Send at -20 dBm (the weakest): `011340ec` decodes to: `seq` 19, `opcode` "RADIO_SET_TX_POWER", `dbm` -20 (-20 dBm).

**`RADIO_SET_TX_POWER` response (the band answers)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x02: a response |
| 1 | 1 | u8 | `seq` | the `seq` of the command it answers |
| 2 | 1 | u8 | `opcode` | 0x40 = RADIO_SET_TX_POWER |
| 3 | 1 | u8 | `status` | 0 = OK; anything else is an error and no data follows |
| 4 | 1 | i8 | `dbm` | dBm |

Example. The answer: `02134000ec` decodes to: `seq` 19, `opcode` "RADIO_SET_TX_POWER", `status` "OK", `dbm` -20 (-20 dBm).

**With the kit's library.** JavaScript, then Python:

```js
band.on('link', (l) => console.log(l.rssi, l.txPower, l.phy));
await band.configure({ link: true });
```

```python
band.on("link", lambda l: print(l.rssi, l.tx_power, l.phy))
band.configure(link=True)
```

- `rssi` is `null` in JavaScript and `None` in Python when the band has no reading. Check before you do sums with it.
- Radio power: -20, -16, -12, -8 (default), -4, 0 or +4 dBm. It goes back to -8 when you disconnect.

More detail: [5.4 Radio](../contract/contract.md#54-radio-radio_set_tx_power) in the contract.

### Microphone level

*MIC_LEVEL, type 0x17, bit 7*

How loud it is, without any sound leaving the band. Ten frames a second by default. Both numbers are whole decibels below the loudest sound the microphone can measure: 0 = as loud as it can measure, 96 = silence. A smaller number is louder. `peak_db` is the loudest instant and `rms_db` the average over the last 100 ms. **This switches the microphone on: the light goes solid blue and cannot be changed until you stop.**

**Switch it on.** `STREAM_START` with bit 7 (mask 128).

**`MIC_LEVEL` frame (the band sends this)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x17: MIC_LEVEL |
| 1 | 4 | u32 | `t_ms` | ms since the band started |
| 5 | 1 | u8 | `peak_db` | dB below full scale: 0 loudest, 96 silence |
| 6 | 1 | u8 | `rms_db` | dB below full scale: 0 loudest, 96 silence |

Example. Loud: peak 12, average 31: `17bc0200000c1f` decodes to: `t_ms` 700 (700 ms), `peak_db` 12, `rms_db` 31.

Example. Silence: both 96: `17200300006060` decodes to: `t_ms` 800 (800 ms), `peak_db` 96, `rms_db` 96.

**With the kit's library.** JavaScript, then Python:

```js
band.on('mic-level', (m) => console.log(m.rmsDb, m.peakDb));
await band.configure({ micLevel: true });
```

```python
band.on("mic-level", lambda m: print(m.rms_db, m.peak_db))
band.configure(mic_level=True)
```

- The level follows the microphone gain (`MIC_SET_GAIN`, below).

More detail: [4. Streams (the MIC_LEVEL row under "Details")](../contract/contract.md#4-streams) in the contract.

### Microphone sound

*AUDIO, type 0x13, bit 3*

The sound itself, squeezed with Opus: 16 kHz (16000 samples a second), mono, 20 ms of sound per packet, 50 packets a second, about 24 kbit/s. One frame carries one complete Opus packet. `seq` starts at 0 each time the stream starts and wraps after 65535; a gap in `seq` means lost packets. There is no time stamp: packet time = start time + `seq` x 20 ms. **While it runs the light is solid blue and cannot be changed.**

**Switch it on.** `STREAM_START` with bit 3 (mask 8). Optional: `MIC_SET_GAIN` (0..80 in half dB steps; 40 = 0 dB, 80 = +20 dB, the default). `MIC_GET` reads the gain and the fixed Opus settings.

**`AUDIO` frame (the band sends this)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x13: AUDIO |
| 1 | 2 | u16 | `seq` | packet counter, starts at 0 |
| 3 | 1 | u8 | `len` | bytes of Opus that follow |
| 4 | len | bytes | `opus` | one complete Opus packet: 20 ms of sound |

Example. Packet number 65535 with 40 bytes of Opus (the bytes here are a made-up pattern, not real sound): `13ffff28b80102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f2021222324252627` decodes to: `seq` 65535, `len` 40, `opus` "b80102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f2021222324252627".

**`MIC_SET_GAIN` command (you send this)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x01: a command |
| 1 | 1 | u8 | `seq` | any number you choose; use a counter |
| 2 | 1 | u8 | `opcode` | 0x30 = MIC_SET_GAIN |
| 3 | 1 | u8 | `gain_half_db` | half dB steps, 0..80; 40 = 0 dB, 80 = +20 dB |

Example. Set the gain to 60 half dB = +10 dB: `0111303c` decodes to: `seq` 17, `opcode` "MIC_SET_GAIN", `gain_half_db` 60 (10 dB).

**`MIC_SET_GAIN` response (the band answers)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x02: a response |
| 1 | 1 | u8 | `seq` | the `seq` of the command it answers |
| 2 | 1 | u8 | `opcode` | 0x30 = MIC_SET_GAIN |
| 3 | 1 | u8 | `status` | 0 = OK; anything else is an error and no data follows |
| 4 | 1 | u8 | `gain_half_db` | half dB steps |

Example. The answer: `021130003c` decodes to: `seq` 17, `opcode` "MIC_SET_GAIN", `status` "OK", `gain_half_db` 60 (10 dB).

**`MIC_GET` command (you send this)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x01: a command |
| 1 | 1 | u8 | `seq` | any number you choose; use a counter |
| 2 | 1 | u8 | `opcode` | 0x31 = MIC_GET |

Example. Read the microphone settings: `011231` decodes to: `seq` 18, `opcode` "MIC_GET".

**`MIC_GET` response (the band answers)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x02: a response |
| 1 | 1 | u8 | `seq` | the `seq` of the command it answers |
| 2 | 1 | u8 | `opcode` | 0x31 = MIC_GET |
| 3 | 1 | u8 | `status` | 0 = OK; anything else is an error and no data follows |
| 4 | 1 | u8 | `gain_half_db` | half dB steps |
| 5 | 2 | u16 | `sample_rate` | samples per second, always 16000 |
| 7 | 1 | u8 | `frame_ms` | ms per packet, always 20 |
| 8 | 1 | u8 | `channels` | always 1 |
| 9 | 2 | u16 | `bitrate` | bits per second, always 24000 |

Example. The answer: gain +20 dB, 16000 samples a second, 20 ms, 1 channel, 24000 bits a second: `0212310050803e1401c05d` decodes to: `seq` 18, `opcode` "MIC_GET", `status` "OK", `gain_half_db` 80 (20 dB), `sample_rate` 16000, `frame_ms` 20 (20 ms), `channels` 1, `bitrate` 24000.

**With the kit's library.** JavaScript, then Python:

```js
const sound = new BandAudio((samples) => console.log(samples.length, 'samples'));   // 320 numbers from -1 to 1
band.on('audio', (a) => sound.push(a));
await band.configure({ audio: true });
```

```python
decoder = PcmDecoder()                       # from vexo_open.audio import PcmDecoder
band.on("audio", lambda a: print(len(decoder.feed(a)), "bytes of samples"))
band.configure(audio=True)
```

- **Turning it into plain sound.** An Opus packet is not a list of samples: it has to go through an Opus decoder. Give the decoder the bytes of `opus` exactly as they arrive, one packet at a time, in the order of `seq`. Out come 320 samples per packet (20 ms at 16000 samples a second), one channel. For a lost packet, ask the decoder to fill the hole so the timing stays right.
- The kit has this ready: `BandAudio` in a web page (Chrome or Edge, nothing to install) and `PcmDecoder` in Python (needs `python3 -m pip install av`). Both fill in lost packets. See [Sound: from Opus packets to samples](api.md#sound-from-opus-packets-to-samples) and [speech.md](speech.md) for sound to text.
- The quickest proof: `python3 vexo-open-kit/examples/python/audio_to_text.py 42 --text no` saves what the microphone hears as a sound file.

More detail: [5.3 Microphone](../contract/contract.md#53-microphone-mic_set_gain) in the contract.

### Clock and time

*TIME_SET, TIME_GET*

The band has no clock with its own battery. It counts milliseconds since it started: that is `t_ms`, the stamp in most data frames (it wraps after 49.7 days). You can tell the band the real time once with `TIME_SET` (`unix_ms`: milliseconds since 1970-01-01 UTC). `TIME_GET` then gives `unix_ms` and `t_ms` for the same moment, and that pair turns any `t_ms` into real time: real time = `unix_ms` + (frame `t_ms` - `t_ms` of the answer).

**Switch it on.** `TIME_SET` once after you connect, or `TIME_GET` to read the pair.

**`TIME_SET` command (you send this)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x01: a command |
| 1 | 1 | u8 | `seq` | any number you choose; use a counter |
| 2 | 1 | u8 | `opcode` | 0x07 = TIME_SET |
| 3 | 8 | u64 | `unix_ms` | ms since 1970-01-01 UTC |

Example. Set the clock: `0132077b0aa1cfa1010000` decodes to: `seq` 50, `opcode` "TIME_SET", `unix_ms` 1794484800123 (2026-11-12T12:00:00.123Z).

**`TIME_SET` response (the band answers)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x02: a response |
| 1 | 1 | u8 | `seq` | the `seq` of the command it answers |
| 2 | 1 | u8 | `opcode` | 0x07 = TIME_SET |
| 3 | 1 | u8 | `status` | 0 = OK; anything else is an error and no data follows |
| 4 | 1 | u8 | `set` | 0 not set, 1 set |
| 5 | 8 | u64 | `unix_ms` | ms since 1970-01-01 UTC |
| 13 | 4 | u32 | `t_ms` | ms since the band started |

Example. The answer: the real time and the band counter for the same moment: `02320700017b0aa1cfa101000040e20100` decodes to: `seq` 50, `opcode` "TIME_SET", `status` "OK", `set` 1, `unix_ms` 1794484800123 (2026-11-12T12:00:00.123Z), `t_ms` 123456 (123456 ms).

**`TIME_GET` command (you send this)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x01: a command |
| 1 | 1 | u8 | `seq` | any number you choose; use a counter |
| 2 | 1 | u8 | `opcode` | 0x08 = TIME_GET |

Example. Read the clock: `013308` decodes to: `seq` 51, `opcode` "TIME_GET".

**`TIME_GET` response (the band answers)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x02: a response |
| 1 | 1 | u8 | `seq` | the `seq` of the command it answers |
| 2 | 1 | u8 | `opcode` | 0x08 = TIME_GET |
| 3 | 1 | u8 | `status` | 0 = OK; anything else is an error and no data follows |
| 4 | 1 | u8 | `set` | 0 not set, 1 set |
| 5 | 8 | u64 | `unix_ms` | ms since 1970-01-01 UTC |
| 13 | 4 | u32 | `t_ms` | ms since the band started |

Example. A band whose clock was never set: `set` 0, `unix_ms` 0: `02330800000000000000000000faffffff` decodes to: `seq` 51, `opcode` "TIME_GET", `status` "OK", `set` 0, `unix_ms` 0 (not set), `t_ms` 4294967290 (4294967290 ms).

**With the kit's library.** JavaScript, then Python:

```js
console.log(await band.time());              // { time, set, t, source }
band.on('tap', (e) => console.log('tap at', new Date(e.time)));
```

```python
print(band.time())
band.on("tap", lambda e: print("tap at", e.time))
```

- The library sets the clock when it connects and gives every event a real `time` next to the band counter `t`.
- The clock is lost when the band restarts. A time outside 2020 to 2100 (for example seconds instead of milliseconds) gives BAD_ARG.
- The exact rule, also right when `t_ms` wraps, is in the contract.

More detail: [5.8 Clock](../contract/contract.md#58-clock-time_set-time_get) in the contract.

## How to control the band

### Light

*LED_SET, LED_GET, IDENTIFY*

One colour light. `LED_SET` takes a colour (`r`, `g`, `b`, each 0..255), a `brightness` (0..255) and a `pattern`: 0 off, 1 solid, 2 blink (half on, half off), 3 breathe, 4 flash (60 ms every period), 5 rainbow (ignores the colour). `period_ms` is the length of one cycle, 100..10000 (ignored for off and solid). `LED_GET` reads your setting back. `IDENTIFY` blinks white 4 times a second for 1..30 seconds so you can find your band, then your light comes back.

**Switch it on.** Send `LED_SET`. **While the microphone is on (sound or level) the light is solid blue and `LED_SET` and `IDENTIFY` answer DENIED.**

**`LED_SET` command (you send this)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x01: a command |
| 1 | 1 | u8 | `seq` | any number you choose; use a counter |
| 2 | 1 | u8 | `opcode` | 0x50 = LED_SET |
| 3 | 1 | u8 | `r` | red, 0..255 |
| 4 | 1 | u8 | `g` | green, 0..255 |
| 5 | 1 | u8 | `b` | blue, 0..255 |
| 6 | 1 | u8 | `brightness` | 0..255, scales all three |
| 7 | 1 | u8 | `pattern` | 0 off, 1 solid, 2 blink, 3 breathe, 4 flash, 5 rainbow |
| 8 | 2 | u16 | `period_ms` | ms per cycle, 100..10000 |

Example. Solid green at half brightness: `01075000ff008001e803` decodes to: `seq` 7, `opcode` "LED_SET", `r` 0, `g` 255, `b` 0, `brightness` 128, `pattern` 1 (solid), `period_ms` 1000 (1000 ms).

**`LED_SET` response (the band answers)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x02: a response |
| 1 | 1 | u8 | `seq` | the `seq` of the command it answers |
| 2 | 1 | u8 | `opcode` | 0x50 = LED_SET |
| 3 | 1 | u8 | `status` | 0 = OK; anything else is an error and no data follows |

Example. The answer: OK, no data: `02075000` decodes to: `seq` 7, `opcode` "LED_SET", `status` "OK".

Example. Breathe in pink, one cycle every 2.5 s: `011850ff0040ff03c409` decodes to: `seq` 24, `opcode` "LED_SET", `r` 255, `g` 0, `b` 64, `brightness` 255, `pattern` 3 (breathe), `period_ms` 2500 (2500 ms).

**`LED_GET` response (the band answers)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x02: a response |
| 1 | 1 | u8 | `seq` | the `seq` of the command it answers |
| 2 | 1 | u8 | `opcode` | 0x51 = LED_GET |
| 3 | 1 | u8 | `status` | 0 = OK; anything else is an error and no data follows |
| 4 | 1 | u8 | `r` | red, 0..255 |
| 5 | 1 | u8 | `g` | green, 0..255 |
| 6 | 1 | u8 | `b` | blue, 0..255 |
| 7 | 1 | u8 | `brightness` | 0..255, scales all three |
| 8 | 1 | u8 | `pattern` | 0 off, 1 solid, 2 blink, 3 breathe, 4 flash, 5 rainbow |
| 9 | 2 | u16 | `period_ms` | ms per cycle, 100..10000 |

Example. Answer to `LED_GET`: the setting: `0219510000ff008001e803` decodes to: `seq` 25, `opcode` "LED_GET", `status` "OK", `r` 0, `g` 255, `b` 0, `brightness` 128, `pattern` 1 (solid), `period_ms` 1000 (1000 ms).

**`IDENTIFY` command (you send this)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x01: a command |
| 1 | 1 | u8 | `seq` | any number you choose; use a counter |
| 2 | 1 | u8 | `opcode` | 0x04 = IDENTIFY |
| 3 | 1 | u8 | `seconds` | s, 1..30 |

Example. Identify for 5 seconds: `01040405` decodes to: `seq` 4, `opcode` "IDENTIFY", `seconds` 5.

Example. Refused: the microphone is on: `02175006` decodes to: `seq` 23, `opcode` "LED_SET", `status` "DENIED".

**With the kit's library.** JavaScript, then Python:

```js
await band.setLed('#00ff00', { brightness: 0.5 });
await band.identify(5);
```

```python
band.set_led("#00ff00", brightness=0.5)
band.identify(5)
```

- In the library `brightness` is 0..1 and `pattern` is a name: `solid`, `blink`, `breathe`, `flash`, `rainbow`, `off`.
- The light goes off when you disconnect.

More detail: [5.1 LED](../contract/contract.md#51-led-led_set) in the contract.

### Storage

*STORE_INFO, STORE_PUT, STORE_GET, STORE_DEL, STORE_WIPE*

A small notebook on the band that survives disconnects and restarts. A `key` is a number 0..999; a value is 1..232 bytes of anything. `STORE_PUT` writes (and replaces), `STORE_GET` reads, `STORE_DEL` deletes one key, `STORE_WIPE` erases everything, `STORE_INFO` says how much room is left. **Anything stored can be read, changed or wiped by the next person who connects: do not store anything private.**

**Switch it on.** Nothing to switch on.

**`STORE_PUT` command (you send this)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x01: a command |
| 1 | 1 | u8 | `seq` | any number you choose; use a counter |
| 2 | 1 | u8 | `opcode` | 0x71 = STORE_PUT |
| 3 | 2 | u16 | `key` | 0..999 |
| 5 | the rest | bytes | `value` | 1..232 bytes |

Example. Store 6 bytes under key 7: `011d71070068692000ff21` decodes to: `seq` 29, `opcode` "STORE_PUT", `key` 7, `value` "68692000ff21".

**`STORE_PUT` response (the band answers)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x02: a response |
| 1 | 1 | u8 | `seq` | the `seq` of the command it answers |
| 2 | 1 | u8 | `opcode` | 0x71 = STORE_PUT |
| 3 | 1 | u8 | `status` | 0 = OK; anything else is an error and no data follows |

Example. The answer: OK: `021d7100` decodes to: `seq` 29, `opcode` "STORE_PUT", `status` "OK".

**`STORE_GET` command (you send this)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x01: a command |
| 1 | 1 | u8 | `seq` | any number you choose; use a counter |
| 2 | 1 | u8 | `opcode` | 0x72 = STORE_GET |
| 3 | 2 | u16 | `key` | 0..999 |

Example. Read key 999: `012072e703` decodes to: `seq` 32, `opcode` "STORE_GET", `key` 999.

**`STORE_GET` response (the band answers)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x02: a response |
| 1 | 1 | u8 | `seq` | the `seq` of the command it answers |
| 2 | 1 | u8 | `opcode` | 0x72 = STORE_GET |
| 3 | 1 | u8 | `status` | 0 = OK; anything else is an error and no data follows |
| 4 | the rest | bytes | `value` | 1..232 bytes |

Example. An answer with a value: the stored bytes: `0220720068692000ff21` decodes to: `seq` 32, `opcode` "STORE_GET", `status` "OK", `value` "68692000ff21".

Example. An empty key: NOT_FOUND: `02217207` decodes to: `seq` 33, `opcode` "STORE_GET", `status` "NOT_FOUND".

**`STORE_DEL` command (you send this)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x01: a command |
| 1 | 1 | u8 | `seq` | any number you choose; use a counter |
| 2 | 1 | u8 | `opcode` | 0x73 = STORE_DEL |
| 3 | 2 | u16 | `key` | 0..999 |

Example. Delete key 7: `0122730700` decodes to: `seq` 34, `opcode` "STORE_DEL", `key` 7.

**`STORE_INFO` command (you send this)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x01: a command |
| 1 | 1 | u8 | `seq` | any number you choose; use a counter |
| 2 | 1 | u8 | `opcode` | 0x70 = STORE_INFO |

Example. Ask how full it is: `011c70` decodes to: `seq` 28, `opcode` "STORE_INFO".

**`STORE_INFO` response (the band answers)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x02: a response |
| 1 | 1 | u8 | `seq` | the `seq` of the command it answers |
| 2 | 1 | u8 | `opcode` | 0x70 = STORE_INFO |
| 3 | 1 | u8 | `status` | 0 = OK; anything else is an error and no data follows |
| 4 | 2 | u16 | `keys_max` | keys |
| 6 | 2 | u16 | `value_max` | bytes per value |
| 8 | 4 | u32 | `free_bytes` | bytes |
| 12 | 2 | u16 | `used_keys` | keys |

Example. The answer: `021c7000e803e80008bd03000300` decodes to: `seq` 28, `opcode` "STORE_INFO", `status` "OK", `keys_max` 1000, `value_max` 232, `free_bytes` 245000, `used_keys` 3.

**`STORE_WIPE` command (you send this)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x01: a command |
| 1 | 1 | u8 | `seq` | any number you choose; use a counter |
| 2 | 1 | u8 | `opcode` | 0x74 = STORE_WIPE |
| 3 | 2 | u16 | `magic` | always 0x5856, so it cannot happen by accident |

Example. Erase everything (needs the safety number): `01ff745658` decodes to: `seq` 255, `opcode` "STORE_WIPE", `magic` 22614 (0x5856).

**With the kit's library.** JavaScript, then Python:

```js
await band.storage.put(7, 'hello');
console.log(await band.storage.getText(7));
```

```python
band.storage.put(7, "hello")
print(band.storage.get_text(7))
```

- The band accepts 10 writes a second; one more answers BUSY: wait a moment and send it again. The library does that for you.
- `STORE_WIPE` answers OK when the erase starts. Until it has finished, the other storage commands answer BUSY.

More detail: [5.5 Storage](../contract/contract.md#55-storage-store_) in the contract.

### Restart and sleep

*RESTART, SLEEP*

`RESTART` restarts the band 200 ms after its answer; its light then blinks blue and the connect window is open for 60 seconds. The band does not close the Bluetooth link first, so your side notices a few seconds later that the link is gone. `SLEEP` turns streams and the light off and closes the Bluetooth link about 0.4 seconds after its answer; the band cannot be found until someone double-taps it. Neither command has arguments or data. To restart a band by hand, press the small RESET button on the board once (once only: two quick presses start the board's loader instead, and one more press brings the band back). A band with no battery also restarts when you unplug the USB cable and plug it in again. After a restart the light blinks blue once a second for 60 seconds, or until you connect.

**Switch it on.** Nothing to switch on.

**`RESTART` command (you send this)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x01: a command |
| 1 | 1 | u8 | `seq` | any number you choose; use a counter |
| 2 | 1 | u8 | `opcode` | 0x05 = RESTART |

Example. Restart: `010505` decodes to: `seq` 5, `opcode` "RESTART".

**`RESTART` response (the band answers)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x02: a response |
| 1 | 1 | u8 | `seq` | the `seq` of the command it answers |
| 2 | 1 | u8 | `opcode` | 0x05 = RESTART |
| 3 | 1 | u8 | `status` | 0 = OK; anything else is an error and no data follows |

Example. The answer; the band restarts 200 ms later: `02050500` decodes to: `seq` 5, `opcode` "RESTART", `status` "OK".

**`SLEEP` command (you send this)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x01: a command |
| 1 | 1 | u8 | `seq` | any number you choose; use a counter |
| 2 | 1 | u8 | `opcode` | 0x06 = SLEEP |

Example. Sleep: `010606` decodes to: `seq` 6, `opcode` "SLEEP".

**`SLEEP` response (the band answers)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x02: a response |
| 1 | 1 | u8 | `seq` | the `seq` of the command it answers |
| 2 | 1 | u8 | `opcode` | 0x06 = SLEEP |
| 3 | 1 | u8 | `status` | 0 = OK; anything else is an error and no data follows |

Example. The answer: `02060600` decodes to: `seq` 6, `opcode` "SLEEP", `status` "OK".

**With the kit's library.** JavaScript, then Python:

```js
await band.restart();
// or: await band.sleep();
```

```python
band.restart()
# or: band.sleep()
```

- Over USB the cable stays usable after `SLEEP`.

More detail: [5.6 Device](../contract/contract.md#56-device) in the contract.

### Intervals

*STREAM_SET_INTERVAL, STREAM_GET_INTERVAL*

How often a stream sends, for the five streams that are sent on a timer. `stream` is the bit number, `interval_ms` the time between two frames: activity (2) 100..3,600,000, default 1000; battery (4) 0 or 1000..3,600,000, default 0 = only when it changes; temp (5) 100..3,600,000, default 5000; link (6) 100..3,600,000, default 1000; mic_level (7) 20..10,000 in steps of 20, default 100. The answer also gives the limits (`min_ms`, `max_ms`). Any other stream answers BAD_ARG.

**Switch it on.** Send `STREAM_SET_INTERVAL` before or after the stream starts.

**`STREAM_SET_INTERVAL` command (you send this)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x01: a command |
| 1 | 1 | u8 | `seq` | any number you choose; use a counter |
| 2 | 1 | u8 | `opcode` | 0x13 = STREAM_SET_INTERVAL |
| 3 | 1 | u8 | `stream` | stream bit number |
| 4 | 4 | u32 | `interval_ms` | ms between two frames |

Example. Battery every minute: `0134130460ea0000` decodes to: `seq` 52, `opcode` "STREAM_SET_INTERVAL", `stream` 4 (battery), `interval_ms` 60000 (60000 ms).

**`STREAM_SET_INTERVAL` response (the band answers)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x02: a response |
| 1 | 1 | u8 | `seq` | the `seq` of the command it answers |
| 2 | 1 | u8 | `opcode` | 0x13 = STREAM_SET_INTERVAL |
| 3 | 1 | u8 | `status` | 0 = OK; anything else is an error and no data follows |
| 4 | 1 | u8 | `stream` | stream bit number |
| 5 | 4 | u32 | `interval_ms` | ms between two frames |
| 9 | 4 | u32 | `min_ms` | shortest allowed |
| 13 | 4 | u32 | `max_ms` | longest allowed |

Example. The answer with the limits: 1 s to 1 hour: `023413000460ea0000e803000080ee3600` decodes to: `seq` 52, `opcode` "STREAM_SET_INTERVAL", `status` "OK", `stream` 4 (battery), `interval_ms` 60000 (60000 ms), `min_ms` 1000 (1000 ms), `max_ms` 3600000 (3600000 ms).

**`STREAM_GET_INTERVAL` command (you send this)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x01: a command |
| 1 | 1 | u8 | `seq` | any number you choose; use a counter |
| 2 | 1 | u8 | `opcode` | 0x14 = STREAM_GET_INTERVAL |
| 3 | 1 | u8 | `stream` | stream bit number |

Example. Ask for the interval of activity: `01361402` decodes to: `seq` 54, `opcode` "STREAM_GET_INTERVAL", `stream` 2 (activity).

**`STREAM_GET_INTERVAL` response (the band answers)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x02: a response |
| 1 | 1 | u8 | `seq` | the `seq` of the command it answers |
| 2 | 1 | u8 | `opcode` | 0x14 = STREAM_GET_INTERVAL |
| 3 | 1 | u8 | `status` | 0 = OK; anything else is an error and no data follows |
| 4 | 1 | u8 | `stream` | stream bit number |
| 5 | 4 | u32 | `interval_ms` | ms between two frames |
| 9 | 4 | u32 | `min_ms` | shortest allowed |
| 13 | 4 | u32 | `max_ms` | longest allowed |

Example. The answer: every 1000 ms: `0236140002e80300006400000080ee3600` decodes to: `seq` 54, `opcode` "STREAM_GET_INTERVAL", `status` "OK", `stream` 2 (activity), `interval_ms` 1000 (1000 ms), `min_ms` 100 (100 ms), `max_ms` 3600000 (3600000 ms).

**With the kit's library.** JavaScript, then Python:

```js
await band.setInterval('battery', 60);       // seconds
console.log(await band.getInterval('battery'));
```

```python
band.set_interval("battery", 60)             # seconds
print(band.get_interval("battery"))
```

- The motion rate is set with `IMU_SET` or `MOTION2_SET`, not here. Sound is always 50 packets a second.
- Like every setting, intervals go back to their defaults when you disconnect.

More detail: [5.7 Stream intervals](../contract/contract.md#57-stream-intervals-stream_set_interval) in the contract.

### Band information

*GET_INFO, GET_NAME, SET_BAND_NUMBER*

`GET_INFO` is the first thing to ask after connecting: the contract version (`proto_major` 1, `proto_minor` 1), the firmware version (1.1.0 on every event band), the band number, which parts work (`caps`, one bit each: 0 accelerometer, 1 gyroscope, 2 motion events, 3 step counter, 4 microphone, 5 RGB LED, 6 battery, 7 chip temperature, 8 storage, 9 radio power control, 10 USB transport) and the largest frame it can send. `GET_NAME` returns the name as text. `SET_BAND_NUMBER` changes the number, **over USB only**: over Bluetooth the answer is DENIED.

**Switch it on.** Nothing to switch on.

**`GET_INFO` command (you send this)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x01: a command |
| 1 | 1 | u8 | `seq` | any number you choose; use a counter |
| 2 | 1 | u8 | `opcode` | 0x01 = GET_INFO |

Example. Ask who the band is: `010001` decodes to: `seq` 0, `opcode` "GET_INFO".

**`GET_INFO` response (the band answers)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x02: a response |
| 1 | 1 | u8 | `seq` | the `seq` of the command it answers |
| 2 | 1 | u8 | `opcode` | 0x01 = GET_INFO |
| 3 | 1 | u8 | `status` | 0 = OK; anything else is an error and no data follows |
| 4 | 1 | u8 | `proto_major` | always 1 |
| 5 | 1 | u8 | `proto_minor` | 1 on an event band (contract v1.1) |
| 6 | 1 | u8 | `fw_major` | firmware version |
| 7 | 1 | u8 | `fw_minor` | firmware version |
| 8 | 1 | u8 | `fw_patch` | firmware version |
| 9 | 1 | u8 | `hw` | 1 = XIAO nRF52840 Sense |
| 10 | 2 | u16 | `band_no` | band number |
| 12 | 2 | u16 | `caps` | one bit per working part |
| 14 | 2 | u16 | `max_frame` | largest frame in bytes |
| 16 | 4 | u32 | `uptime_ms` | ms since the band started |

Example. A band with firmware 1.1.0, number 42: `023101000101010100012a00ff07f40040e20100` decodes to: `seq` 49, `opcode` "GET_INFO", `status` "OK", `proto_major` 1, `proto_minor` 1, `fw_major` 1, `fw_minor` 1, `fw_patch` 0, `hw` 1, `band_no` 42, `caps` 2047 (11 of 11 parts work), `max_frame` 244, `uptime_ms` 123456 (123456 ms).

**`GET_NAME` response (the band answers)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x02: a response |
| 1 | 1 | u8 | `seq` | the `seq` of the command it answers |
| 2 | 1 | u8 | `opcode` | 0x02 = GET_NAME |
| 3 | 1 | u8 | `status` | 0 = OK; anything else is an error and no data follows |
| 4 | the rest | text | `name` | text, 12 or 13 characters |

Example. Answer to `GET_NAME`: the text Vexo-HACK-42: `020102005665786f2d4841434b2d3432` decodes to: `seq` 1, `opcode` "GET_NAME", `status` "OK", `name` "Vexo-HACK-42".

**`SET_BAND_NUMBER` command (you send this)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x01: a command |
| 1 | 1 | u8 | `seq` | any number you choose; use a counter |
| 2 | 1 | u8 | `opcode` | 0x03 = SET_BAND_NUMBER |
| 3 | 2 | u16 | `number` | 1..999 |
| 5 | 2 | u16 | `magic` | always 0x5856, so it cannot happen by accident |

Example. Set band number 42 (USB only): `0102032a005658` decodes to: `seq` 2, `opcode` "SET_BAND_NUMBER", `number` 42, `magic` 22614 (0x5856).

**`SET_BAND_NUMBER` response (the band answers)**

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x02: a response |
| 1 | 1 | u8 | `seq` | the `seq` of the command it answers |
| 2 | 1 | u8 | `opcode` | 0x03 = SET_BAND_NUMBER |
| 3 | 1 | u8 | `status` | 0 = OK; anything else is an error and no data follows |
| 4 | 2 | u16 | `number` | the number now stored |

Example. Over Bluetooth: DENIED: `02030306` decodes to: `seq` 3, `opcode` "SET_BAND_NUMBER", `status` "DENIED".

**With the kit's library.** JavaScript, then Python:

```js
console.log(band.info.firmware, band.info.capabilities);
console.log(await band.getName());
```

```python
print(band.info.firmware, band.info.capabilities)
print(band.get_name())
```

- Decide what a band can do from `proto_minor` (in the library: `band.info.features`), not from the firmware version.

More detail: [Opcode table](../contract/contract.md#opcode-table) in the contract.

## Errors and refusals

Every response has a `status` byte. 0 is OK. With any other status the command changed nothing, and the response carries no data.

| Status | Name | Meaning |
|---|---|---|
| 0 | `OK` | The command worked. |
| 1 | `UNKNOWN_OPCODE` | The band does not know this command. |
| 2 | `BAD_LENGTH` | The argument bytes do not have the size the command needs. |
| 3 | `BAD_ARG` | A value is outside the allowed set. Nothing changed. |
| 4 | `BUSY` | Storage is not ready (still starting, still erasing, or more than 10 writes in a second), or more than 4 commands were sent without waiting for responses. |
| 5 | `NOT_AVAILABLE` | That part failed its start-up check on this band, or it does not exist on this link (the LINK stream over USB). |
| 6 | `DENIED` | Blocked by a safety rule: the light while the microphone is on, the band number over Bluetooth, a motion rate too fast for this link. |
| 7 | `NOT_FOUND` | The storage key is empty. |
| 8 | `NO_SPACE` | Storage is full. |
| 9 | `IO_ERROR` | A sensor or storage error. |

The refusals you are most likely to meet:

| You did this | The band answers | Example response | What to do |
|---|---|---|---|
| Changed the light (`LED_SET`, `IDENTIFY`) while the microphone is on | `DENIED` | `02175006` | Stop the sound and the loudness stream first |
| Sent `SET_BAND_NUMBER` over Bluetooth | `DENIED` | `02030306` | It works over the USB cable only |
| Started motion faster than this link allows | `DENIED` | `023a1006` | Choose a lower rate or one sensor ([Fast motion](#fast-motion)) |
| Gave a value that is not allowed, for example a rate that does not exist | `BAD_ARG` | `020d2003` | Check the allowed values in the block for that command |
| Started a stream whose part is not working, or the link stream over USB | `NOT_AVAILABLE` | `02091005` | Look at `caps` in `GET_INFO` |
| Sent an opcode the band does not have | `UNKNOWN_OPCODE` | `02290901` | Check the opcode against the cheat sheet below |
| Wrote to storage more than 10 times in a second, or during an erase | `BUSY` | `021f7104` | Wait a moment and send it again |
| Read a storage key that is empty | `NOT_FOUND` | `02217207` | Nothing was stored there |
| Wrote to storage when it is full | `NO_SPACE` | `021e7108` | Delete keys you do not need |

**Notices.** The band can also tell you something without being asked: a frame of type `0x03` with a code and one argument.

| Offset | Size | Type | Field | Unit or meaning |
|---|---|---|---|---|
| 0 | 1 | u8 | `type` | always 0x03: a notice |
| 1 | 1 | u8 | `code` | 1 LOW_BATTERY, 2 CRITICAL_BATTERY, 3 STREAM_DROPPED, 4 USB_POWER |
| 2 | 1 | u8 | `arg` | depends on the code |

| Code | Name | Meaning | `arg` |
|---|---|---|---|
| 1 | `LOW_BATTERY` | The battery is low: the light is dimmed. | percent |
| 2 | `CRITICAL_BATTERY` | The battery is almost empty: streams stop and the light goes off. Charge the band. | percent |
| 3 | `STREAM_DROPPED` | Frames were lost because the link was too slow or a frame was larger than `max_frame`. | stream bit number, 0..8 |
| 4 | `USB_POWER` | The USB cable was plugged in or pulled out. | 0 unplugged, 1 plugged |

Example. Low battery at 14 percent: `03010e` decodes to: `code` "LOW_BATTERY", `arg` 14.

Example. Frames of stream 3 (sound) were lost: `030303` decodes to: `code` "STREAM_DROPPED", `arg` 3.

In the library these arrive as the `notice` event. No response within 2 seconds is a problem too: the library reports it as `timeout`. The safety rules behind the refusals are in [6. Safety rules you cannot change](../contract/contract.md#6-safety-rules-you-cannot-change).

## Cheat sheet

**Frames:** `01` command = `[01][seq][opcode][args]`, `02` response = `[02][seq][opcode][status][data]`, `03` notice = `[03][code][arg]`, `10` to `18` data. Numbers are little-endian. Write to RX `254f0002-...`, listen on TX `254f0003-...`, service `254f0001-df82-4a9c-a2b5-563a0f6b2021`.

**Every command.**

| Opcode | Command | You send (after `01 seq opcode`) | You get back (after `02 seq opcode 00`) | Explained in |
|---|---|---|---|---|
| `0x01` | `GET_INFO` | none | `proto_major` u8, `proto_minor` u8, `fw_major` u8, `fw_minor` u8, `fw_patch` u8, `hw` u8, `band_no` u16, `caps` u16, `max_frame` u16, `uptime_ms` u32 | [Band information](#band-information) |
| `0x02` | `GET_NAME` | none | `name` text | [Band information](#band-information) |
| `0x03` | `SET_BAND_NUMBER` | `number` u16, `magic` u16 | `number` u16 | [Band information](#band-information) |
| `0x04` | `IDENTIFY` | `seconds` u8 | none | [Light](#light) |
| `0x05` | `RESTART` | none | none | [Restart and sleep](#restart-and-sleep) |
| `0x06` | `SLEEP` | none | none | [Restart and sleep](#restart-and-sleep) |
| `0x07` | `TIME_SET` | `unix_ms` u64 | `set` u8, `unix_ms` u64, `t_ms` u32 | [Clock and time](#clock-and-time) |
| `0x08` | `TIME_GET` | none | `set` u8, `unix_ms` u64, `t_ms` u32 | [Clock and time](#clock-and-time) |
| `0x10` | `STREAM_START` | `mask` u16 | `active` u16 | [Switching data on and off](#switching-data-on-and-off) |
| `0x11` | `STREAM_STOP` | `mask` u16 | `active` u16 | [Switching data on and off](#switching-data-on-and-off) |
| `0x12` | `STREAM_GET` | none | `active` u16 | [Switching data on and off](#switching-data-on-and-off) |
| `0x13` | `STREAM_SET_INTERVAL` | `stream` u8, `interval_ms` u32 | `stream` u8, `interval_ms` u32, `min_ms` u32, `max_ms` u32 | [Intervals](#intervals) |
| `0x14` | `STREAM_GET_INTERVAL` | `stream` u8 | `stream` u8, `interval_ms` u32, `min_ms` u32, `max_ms` u32 | [Intervals](#intervals) |
| `0x20` | `IMU_SET` | `rate_hz` u8, `accel_range_g` u8, `gyro_range_dps` u16 | `rate_hz` u8, `accel_range_g` u8, `gyro_range_dps` u16 | [Motion](#motion) |
| `0x21` | `IMU_GET` | none | `rate_hz` u8, `accel_range_g` u8, `gyro_range_dps` u16 | [Motion](#motion) |
| `0x22` | `EVENT_SET` | `event_mask` u8, `tap_threshold_mg` u16 | `event_mask` u8, `tap_threshold_mg` u16 | [Taps and events](#taps-and-events) |
| `0x23` | `EVENT_GET` | none | `event_mask` u8, `tap_threshold_mg` u16 | [Taps and events](#taps-and-events) |
| `0x24` | `MOTION2_SET` | `rate_hz` u16, `sensors` u8 | `rate_hz` u16, `sensors` u8, `max_accel_hz` u16, `max_gyro_hz` u16, `max_both_hz` u16 | [Fast motion](#fast-motion) |
| `0x25` | `MOTION2_GET` | none | `rate_hz` u16, `sensors` u8, `max_accel_hz` u16, `max_gyro_hz` u16, `max_both_hz` u16 | [Fast motion](#fast-motion) |
| `0x30` | `MIC_SET_GAIN` | `gain_half_db` u8 | `gain_half_db` u8 | [Microphone sound](#microphone-sound) |
| `0x31` | `MIC_GET` | none | `gain_half_db` u8, `sample_rate` u16, `frame_ms` u8, `channels` u8, `bitrate` u16 | [Microphone sound](#microphone-sound) |
| `0x40` | `RADIO_SET_TX_POWER` | `dbm` i8 | `dbm` i8 | [Link quality](#link-quality) |
| `0x41` | `RADIO_GET` | none | `tx_dbm` i8, `rssi_dbm` i8, `phy` u8, `interval` u16, `mtu` u16 | [Link quality](#link-quality) |
| `0x50` | `LED_SET` | `r` u8, `g` u8, `b` u8, `brightness` u8, `pattern` u8, `period_ms` u16 | none | [Light](#light) |
| `0x51` | `LED_GET` | none | `r` u8, `g` u8, `b` u8, `brightness` u8, `pattern` u8, `period_ms` u16 | [Light](#light) |
| `0x60` | `TEMP_SET_INTERVAL` | `seconds` u8 | `seconds` u8 | [Temperature](#temperature) |
| `0x61` | `BATTERY_GET` | none | `percent` u8, `state` u8, `millivolts` u16 | [Battery](#battery) |
| `0x70` | `STORE_INFO` | none | `keys_max` u16, `value_max` u16, `free_bytes` u32, `used_keys` u16 | [Storage](#storage) |
| `0x71` | `STORE_PUT` | `key` u16, `value` bytes | none | [Storage](#storage) |
| `0x72` | `STORE_GET` | `key` u16 | `value` bytes | [Storage](#storage) |
| `0x73` | `STORE_DEL` | `key` u16 | none | [Storage](#storage) |
| `0x74` | `STORE_WIPE` | `magic` u16 | none | [Storage](#storage) |

**Every data frame.** Start one with `STREAM_START` and its mask; add masks to start several.

| Type | Bit | Mask | Stream | Body (after the type byte) | Explained in |
|---|---|---|---|---|---|
| `0x10` | 0 | 1 | `MOTION` | `t_ms` u32, `period_ms` u8, `count` u8, then `ax, ay, az, gx, gy, gz` | [Motion](#motion) |
| `0x11` | 1 | 2 | `EVENT` | `t_ms` u32, `event` u8, `arg` u8 | [Taps and events](#taps-and-events) |
| `0x12` | 2 | 4 | `ACTIVITY` | `t_ms` u32, `steps_total` u32, `cadence_spm` u8, `state` u8, `motion_mg` u16 | [Steps and activity](#steps-and-activity) |
| `0x13` | 3 | 8 | `AUDIO` | `seq` u16, `len` u8, then `opus` | [Microphone sound](#microphone-sound) |
| `0x14` | 4 | 16 | `BATTERY` | `percent` u8, `state` u8, `millivolts` u16 | [Battery](#battery) |
| `0x15` | 5 | 32 | `TEMP` | `t_ms` u32, `temp_mc` i32 | [Temperature](#temperature) |
| `0x16` | 6 | 64 | `LINK` | `t_ms` u32, `rssi_dbm` i8, `tx_dbm` i8, `phy` u8 | [Link quality](#link-quality) |
| `0x17` | 7 | 128 | `MIC_LEVEL` | `t_ms` u32, `peak_db` u8, `rms_db` u8 | [Microphone level](#microphone-level) |
| `0x18` | 8 | 256 | `MOTION2` | `t_ms` u32, `sample_no` u32, `period_us` u32, `sensors` u8, `count` u8, then `ax, ay, az and/or gx, gy, gz` | [Fast motion](#fast-motion) |

**Notices:** 1 `LOW_BATTERY`, 2 `CRITICAL_BATTERY`, 3 `STREAM_DROPPED`, 4 `USB_POWER`. **Statuses:** 0 `OK`, 1 `UNKNOWN_OPCODE`, 2 `BAD_LENGTH`, 3 `BAD_ARG`, 4 `BUSY`, 5 `NOT_AVAILABLE`, 6 `DENIED`, 7 `NOT_FOUND`, 8 `NO_SPACE`, 9 `IO_ERROR`. Both are explained in [Errors and refusals](#errors-and-refusals).

The full contract, with every limit and every special case: [contract/contract.md](../contract/contract.md). Real example frames for testing your own code: `contract/test-vectors.json`.
