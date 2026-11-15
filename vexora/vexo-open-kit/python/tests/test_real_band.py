"""Things learned from the first real band: the pretend band behaves the same way, and the
library copes with them (storage that says "busy", sleep on the cable, the link stream)."""
import asyncio
import time
import unittest
from unittest import mock

from vexo_open import Band, SimTransport, codec
from vexo_open import band as band_module

from test_band import BandCase, Silent, connect, run, stepping

STORAGE_COMMANDS = (("STORE_INFO", {}), ("STORE_PUT", dict(key=1, value=b"x")), ("STORE_GET", dict(key=1)),
                    ("STORE_DEL", dict(key=1)), ("STORE_WIPE", dict(magic=codec.MAGIC)))


async def status(band, name, **args):
    """The band's answer to one raw command, as the library's error code ("ok" if none)."""
    try:
        await band.command(name, **args)
        return "ok"
    except band_module.VexoError as e:
        return e.code


class SimStorageTest(BandCase):
    def test_what_a_key_costs(self):
        async def main():
            band, sim = await connect()
            free = []
            for value in (b"1", b"1234", b"12345", bytes(232)):
                sim.store = {5: value}
                free.append(242164 - (await band.storage.info()).free_bytes)
            self.assertEqual(free, [12, 12, 16, 240])  # 8 + the value rounded up to a multiple of 4
            sim.store = {key: bytes(232) for key in range(1000)}  # the fullest it can get
            info = await band.storage.info()
            self.assertEqual(info, dict(keys_max=1000, value_max=232, free_bytes=2164, used_keys=1000))
            await band.storage.put(3, bytes(232))  # replacing: the old value's room counts as free
            sim.store[1000] = bytes(2160)          # (not a real key) leaves 242164 - 242168 < 0 for a new put
            self.assertEqual(await status(band, "STORE_PUT", key=3, value=bytes(232)), "no-space")
            self.assertEqual(await status(band, "STORE_PUT", key=3, value=bytes(228)), "ok")
        run(main())

    def test_ten_writes_a_second(self):
        async def main():
            band, sim = await connect()
            for key in range(10):
                self.assertEqual(await status(band, "STORE_PUT", key=key, value=b"v"), "ok")
            # The eleventh write in the same second: BUSY, and nothing changes.
            self.assertEqual(await status(band, "STORE_PUT", key=10, value=b"v"), "busy")
            self.assertEqual(await status(band, "STORE_DEL", key=0), "busy")
            self.assertEqual(sorted(sim.store), list(range(10)))
            # A wrong argument is reported before BUSY; reading is not limited.
            self.assertEqual(await status(band, "STORE_PUT", key=1000, value=b"v"), "bad-arg")
            self.assertEqual(await status(band, "STORE_PUT", key=1, value=b""), "bad-length")
            self.assertEqual(await status(band, "STORE_DEL", key=1000), "bad-arg")
            self.assertEqual(await status(band, "STORE_GET", key=0), "ok")
            self.assertEqual(await status(band, "STORE_INFO"), "ok")
            sim.step(999)
            self.assertEqual(await status(band, "STORE_PUT", key=10, value=b"v"), "busy")
            sim.step(1)  # the ten writes are now a full second ago
            # A refused write did not count: ten more fit. Deletes count like puts;
            # one that finds nothing (NOT_FOUND) is not a write.
            self.assertEqual(await status(band, "STORE_DEL", key=500), "not-found")
            for key in range(5):
                self.assertEqual(await status(band, "STORE_DEL", key=key), "ok")
            sim.step(600)
            for key in range(5):
                self.assertEqual(await status(band, "STORE_PUT", key=key, value=b"w"), "ok")
            self.assertEqual(await status(band, "STORE_PUT", key=20, value=b"w"), "busy")
            sim.step(400)  # "any 1000 ms": the five deletes have left the window, the five puts not
            for key in range(20, 25):
                self.assertEqual(await status(band, "STORE_PUT", key=key, value=b"w"), "ok")
            self.assertEqual(await status(band, "STORE_PUT", key=30, value=b"w"), "busy")
        run(main())

    def test_wipe_takes_time_and_everything_is_busy_meanwhile(self):
        async def main():
            band, sim = await connect()
            await band.storage.put(1, "kept until the wipe ends")
            self.assertEqual(await status(band, "STORE_WIPE", magic=1), "bad-arg")
            self.assertEqual(await status(band, "STORE_WIPE", magic=codec.MAGIC), "ok")  # answers at once
            for ms in (0, 999):
                sim.step(ms)
                for name, args in STORAGE_COMMANDS:
                    self.assertEqual(await status(band, name, **args), "busy", name)
                self.assertEqual(await status(band, "STORE_WIPE", magic=1), "bad-arg")  # wrong argument first
            self.assertEqual(await band.get_name(), "Vexo-HACK-42")  # only storage is busy
            sim.step(1)
            self.assertEqual(sim.store, {})
            self.assertEqual(await band.storage.info(), dict(keys_max=1000, value_max=232, free_bytes=242164,
                                                             used_keys=0))
            self.assertEqual(await status(band, "STORE_PUT", key=1, value=b"x"), "ok")
        run(main())

    def test_wipe_goes_on_when_the_link_closes_and_wipe_ms(self):
        async def main():
            band, sim = await connect(wipe_ms=5000)
            await band.storage.put(1, "x")
            await band.command("STORE_WIPE", magic=codec.MAGIC)
            await band.disconnect()
            band = await Band.connect(42, transport=sim)
            self.assertEqual(await status(band, "STORE_INFO"), "busy")  # still erasing on the new link
            await band.disconnect()
            sim.step(5000)  # it finishes with nobody connected
            band = await Band.connect(42, transport=sim)
            self.assertEqual((await band.storage.info()).used_keys, 0)

            band, sim = await connect(wipe_ms=0)  # 0: done at once
            await band.storage.put(1, "x")
            await band.storage.wipe()
            self.assertIsNone(await band.storage.get(1))
        run(main())


class SimLinkTest(BandCase):
    def test_reachable(self):
        async def main():
            band, sim = await connect()
            self.assertTrue(sim.reachable)
            await band.storage.put(1, "kept")
            await band.set_led("#ff0000")
            sim.step(700)
            sim.drop_link()
            self.assertTrue(sim.reachable)  # a dropped link: the band can be found again
            sim.reachable = False
            async with self.assertVexoError("not-found-nearby"):
                await Band.connect(42, transport=sim)
            self.assertFalse(sim.is_open)
            sim.reachable = True
            sim.number = 43
            band = await Band.connect(43, transport=sim)
            # storage, the band number and the clock survive; settings are back to default
            self.assertEqual(await band.storage.get_text(1), "kept")
            self.assertEqual(band.info.uptime_ms, 700)
            self.assertEqual((await band.get_led()).pattern, "off")
        run(main())

    def test_sleep_on_the_cable_keeps_the_session(self):
        async def main():
            shown = []
            band, sim = await connect(via="usb", on_led=shown.append)
            reasons, got = [], []
            band.on("disconnect", lambda e: reasons.append(e.reason))
            band.on("motion", got.append)
            await band.set_led("#00ff00")
            await band.configure(motion=50, battery=True)
            await band.sleep()
            self.assertFalse(any(band.streams.values()))  # the library knows the streams are going off
            sim.step(199)
            self.assertEqual((sim.active, sim.user_led["g"]), (0x11, 255))  # 200 ms after the answer...
            sim.step(1)
            self.assertEqual((sim.active, sim.user_led["g"], shown[-1]["pattern"]), (0, 0, 0))
            got.clear()
            sim.step(3000)
            self.assertEqual(got, [])
            # ...but the session stays open and keeps answering.
            self.assertTrue(band.connected and sim.is_open)
            self.assertEqual(reasons, [])
            self.assertEqual(await band.get_name(), "Vexo-HACK-42")
            self.assertEqual((await band.command("STREAM_GET")).active, 0)
            self.assertEqual((await band.get_led()).pattern, "off")
            self.assertEqual((await band.configure(motion=True))["motion"], 50)  # and can be used again
            sim.step(1000)
            self.assertEqual(len(got), 50)
        run(main())

    def test_sleep_and_restart_over_bluetooth_still_close_the_link(self):
        async def main():
            for action in ("sleep", "restart"):
                for via in (("ble",) if action == "sleep" else ("ble", "usb")):
                    band, sim = await connect(via=via)
                    reasons = []
                    band.on("disconnect", lambda e: reasons.append(e.reason))
                    await getattr(band, action)()
                    sim.step(200)
                    self.assertEqual(reasons, [action])
                    self.assertFalse(sim.is_open)
        run(main())


class ExpectedCloseWindowTest(BandCase):
    def test_a_close_long_after_sleep_or_restart_gets_its_real_reason(self):
        async def go(method, wait):
            band, sim = await connect(via="usb")
            reasons = []
            band.on("disconnect", lambda e: reasons.append(e.reason))
            await band.set_led("#ff0000")
            await band.configure(battery=True)
            await getattr(band, method)()
            self.assertEqual((band._restore, band._restore_mask), ({}, 0))  # nothing to set again later
            await asyncio.sleep(wait)
            sim.drop_link()  # reports "lost", as a real link does
            return reasons
        with mock.patch.object(band_module, "EXPECT_CLOSE_SECONDS", 0.05):
            for method in ("sleep", "restart"):
                self.assertEqual(run(go(method, 0)), [method])     # within the window: the expected close
                self.assertEqual(run(go(method, 0.08)), ["lost"])  # later: something else happened
        self.assertEqual(band_module.EXPECT_CLOSE_SECONDS, 3.0)


class LinkStreamMessageTest(BandCase):
    def test_only_a_refused_link_stream_gets_the_usb_text(self):
        async def main():
            band, sim = await connect(via="usb")
            async with self.assertVexoError("not-available") as caught:
                await band.command("STREAM_START", mask=1 << codec.STREAM_BITS["link"])
            self.assertIn("only exists over Bluetooth", str(caught.error))
            band, sim = await connect()
            async with self.assertVexoError("not-available") as caught:
                await band.command("STREAM_START", mask=0x100)  # a stream no band has
            self.assertNotIn("USB", str(caught.error))
            self.assertTrue((await band.configure(link=True))["link"])  # over Bluetooth link works
        run(main())


class RssiTest(BandCase):
    def test_rssi_is_signed_and_127_means_no_reading(self):
        async def main():
            band, sim = await connect()
            links = []
            band.on("link", links.append)
            for raw, value in ((-60, -60), (-128, -128), (0, 0), (126, 126), (127, None)):
                sim._rssi = lambda raw=raw: raw
                self.assertEqual((await band.get_radio()).rssi, value)
                sim._send("link", t_ms=1, rssi_dbm=raw, tx_dbm=-8, phy=2)
                self.assertEqual(links[-1].rssi, value)
            self.assertEqual(len(links), 5)
        run(main())


class StorageRetryTest(BandCase):
    def test_thirty_puts_just_take_three_seconds(self):
        async def main():
            band, sim = await connect()
            band.storage._busy_pause = 0.001

            async def fill():
                for key in range(30):
                    await band.storage.put(key, "value %d" % key)
            await stepping(sim, fill(), ms=20)
            self.assertEqual(len(sim.store), 30)
            self.assertEqual(await band.storage.get_text(29), "value 29")
            self.assertTrue(2000 <= sim.now_ms < 3000, sim.now_ms)  # 10 at once, 10 more each second
        run(main())

    def test_every_call_waits_for_busy(self):
        async def main():
            band, sim = await connect()
            store = band.storage
            store._busy_pause = 0.001
            self.assertEqual((store._busy_seconds, band_module.Storage(band)._busy_pause), (3.0, 0.1))
            calls = (lambda: store.put(1, "a"), lambda: store.get(1), lambda: store.get_text(1),
                     lambda: store.delete(1), lambda: store.info())
            for call in calls:
                sim.store = {1: b"old"}
                await band.command("STORE_WIPE", magic=codec.MAGIC)  # raw: everything is BUSY for a second
                before = sim.now_ms
                try:
                    await stepping(sim, call(), ms=50)  # not "busy": the call waited for the wipe
                except band_module.VexoError as e:
                    self.assertEqual((call, e.code), (calls[3], "not-found"))  # delete: the wipe took it
                self.assertGreaterEqual(sim.now_ms - before, 1000)
        run(main())

    def test_busy_for_too_long(self):
        async def main():
            band, sim = await connect()
            store = band.storage
            store._busy_pause, store._busy_seconds = 0.005, 0.05
            await band.command("STORE_WIPE", magic=codec.MAGIC)  # and the band's clock stands still
            sent = []
            real = sim._handle
            sim._handle = lambda frame: (sent.append(frame), real(frame))[1]
            for call in (lambda: store.put(1, "a"), lambda: store.get(1), lambda: store.delete(1), store.info):
                started = time.time()
                async with self.assertVexoError("busy") as caught:
                    await call()
                self.assertTrue(0.04 <= time.time() - started < 0.5)
                for words in ("still busy", "10 writes a second", "Try again later"):
                    self.assertIn(words, str(caught.error))
            self.assertGreater(len(sent), 4 * 5)  # it really kept sending
        run(main())

    def test_wipe_returns_when_the_band_is_done(self):
        async def main():
            band, sim = await connect()
            band.storage._busy_pause = 0.001
            for key in range(5):
                await band.storage.put(key, "x")
            before = sim.now_ms
            self.assertIsNone(await stepping(sim, band.storage.wipe(), ms=50))
            self.assertGreaterEqual(sim.now_ms - before, 1000)
            await band.storage.put(1, "straight after the wipe")  # no step, no error
            self.assertEqual(sim.store, {1: b"straight after the wipe"})
        run(main())

    def test_wipe_that_does_not_finish_or_fails(self):
        async def main():
            band, sim = await connect()
            band.storage._busy_pause = 0.005
            started = time.time()
            async with self.assertVexoError("timeout") as caught:
                await band.storage.wipe(timeout=0.05)  # the band's clock stands still
            self.assertIn("storage is still erasing", str(caught.error))
            self.assertTrue(0.04 <= time.time() - started < 0.5)
            for bad in (0, -1, "30", True, None):
                async with self.assertVexoError("bad-arg"):
                    await band.storage.wipe(timeout=bad)

            # The band reports that the erase failed.
            band, sim = await connect()
            band.storage._busy_pause = 0.005
            sim._do_store_info = lambda a: "NOT_AVAILABLE"
            async with self.assertVexoError("not-available") as caught:
                await band.storage.wipe()
            self.assertIn("could not erase its storage", str(caught.error))
            self.assertIn("wipe() again", str(caught.error))

            # The link drops while waiting.
            band, sim = await connect()
            band.storage._busy_pause = 0.005
            waiting = asyncio.ensure_future(band.storage.wipe())
            await asyncio.sleep(0.02)
            self.assertFalse(waiting.done())
            sim.drop_link()
            async with self.assertVexoError("not-connected"):
                await waiting
        run(main())

    def test_wipe_has_no_longer_timeout_than_other_commands(self):
        async def main():
            band = Band(Silent(), timeout=0.05)
            band.connected = band._linked = True
            started = time.time()
            async with self.assertVexoError("timeout") as caught:
                await band.storage.wipe()
            self.assertLess(time.time() - started, 0.12)  # once the timeout, not three times
            self.assertIn("0.05 seconds", str(caught.error))
        run(main())

    def test_realtime_sim(self):
        async def main():
            sim = SimTransport(42, wipe_ms=150)
            band = await Band.connect(42, transport=sim)
            band.storage._busy_pause = 0.02
            started = time.time()
            for key in range(12):  # two more than fit in one second
                await band.storage.put(key, "x")
            self.assertGreater(time.time() - started, 0.7)
            await band.storage.wipe()
            await band.storage.put(1, "after")
            self.assertEqual(sim.store, {1: b"after"})
            await band.disconnect()
        run(main())


if __name__ == "__main__":
    unittest.main()
