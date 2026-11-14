"""Runs the step diary against the pretend band and checks what it saves and shows.

    python3 -m unittest discover -s projects/test -p 'test_step_diary.py'
"""
import json
import os
import re
import stat
import tempfile
import unittest

import running

PROGRAM = os.path.join("step-diary", "diary.py")
BAND = 913
ENTRY = re.compile(r"^\d\d:\d\d:\d\d +(\d+) steps   still (\d+)s, walk (\d+)s, run (\d+)s   -> box (\d+)(   \(link dropped: steps complete, seconds away not counted\))?$")
ROW = re.compile(r"^  \d\d:\d\d:\d\d +(\d+) +(\d+)s +(\d+)s +(\d+)s( +#*)?(  \(link dropped: steps complete, seconds away not counted\))?$")


class StepDiary(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.home = os.path.join(folder.name, "home")
        self.work = os.path.join(folder.name, "work")       # the folder the program is started in
        os.mkdir(self.home)
        os.mkdir(self.work)
        self.memory = running.store_file(self.home, BAND)

    def ok(self, *args):
        done = running.run(PROGRAM, self.home, BAND, "--sim", *args, cwd=self.work)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stderr, "")
        return done.stdout.splitlines()

    def put(self, store):
        """Fill the pretend band's storage: {box: bytes}."""
        os.makedirs(os.path.dirname(self.memory), exist_ok=True)
        with open(self.memory, "w", encoding="utf-8") as f:
            json.dump({str(box): bytes(value).hex() for box, value in store.items()}, f)

    def stored(self):
        with open(self.memory, encoding="utf-8") as f:
            return {int(box): bytes.fromhex(value) for box, value in json.load(f).items()}

    def test_record_through_a_lost_link_then_show_then_clear(self):
        lines = self.ok("--every", "2")
        self.assertEqual(lines[0], "Connected to Vexo-HACK-913. 0 entries are already on the band (see them with --show).")
        self.assertEqual(lines[1], "Recording one entry every 2 seconds. Stop with Ctrl+C.")
        saved = [ENTRY.match(line).groups() for line in lines if ENTRY.match(line)]
        self.assertGreaterEqual(len(saved), 5, lines)
        self.assertEqual([int(entry[4]) for entry in saved], list(range(400, 400 + len(saved))))
        self.assertGreater(sum(int(entry[0]) for entry in saved), 0)
        for state in (1, 2, 3):                                 # the wearer was still, walked and ran
            self.assertGreater(sum(int(entry[state]) for entry in saved), 0, lines)
        lost = lines.index("Link lost. Trying to get it back; the diary goes on.")
        back = [i for i, line in enumerate(lines) if line.startswith("Link is back after ")][0]
        self.assertLess(lost, back)
        self.assertTrue(any(ENTRY.match(line) for line in lines[back:]), "no entry after the link came back")
        # An entry with the link away for a part of it has too few seconds for its steps
        # ("12 steps, still 1s"). It says why; the entries around it do not.
        marked = [bool(entry[5]) for entry in saved]
        self.assertIn(True, marked, lines)
        self.assertFalse(marked[0] or marked[1] or marked[-1], lines)
        between = [ENTRY.match(line).group(6) for line in lines[lost:] if ENTRY.match(line)]
        self.assertTrue(between[0], "the first entry saved after the link was lost is marked")
        for entry in saved:
            if not entry[5]:                # no drop: every second of the entry is in a column (2 seconds, give or take)
                self.assertGreaterEqual(sum(int(part) for part in entry[1:4]), 1, lines)
        self.assertEqual(lines[-1], "Stopped. %d entries are on the band. See them with --show." % len(saved))

        # The pretend band's storage is in a folder and a file that only this user can open.
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(self.memory)).st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(os.stat(self.memory).st_mode), 0o600)

        # A second run sees what the first one saved.
        shown = self.ok("--show")
        self.assertEqual(shown[0], "Diary on Vexo-HACK-913: %d entries." % len(saved))
        rows = [ROW.match(line).groups()[:4] for line in shown if ROW.match(line)]
        self.assertEqual(rows, [entry[:4] for entry in saved])
        self.assertEqual([bool(ROW.match(line).group(6)) for line in shown if ROW.match(line)], marked, "--show marks the same entries")
        self.assertIn("#" * 30, "\n".join(shown))               # the biggest entry has the full bar
        self.assertEqual(shown[-1].split()[:2], ["total", str(sum(int(entry[0]) for entry in saved))])

        again = self.ok("--every", "1", "--seconds", "3.5")
        self.assertIn("%d entries are already on the band" % len(saved), again[0])
        self.assertIn("-> box %d" % (400 + len(saved)), "\n".join(again))

        self.assertEqual(self.ok("--clear"), ["Diary deleted from Vexo-HACK-913."])
        self.assertEqual(self.ok("--show")[0], "Diary on Vexo-HACK-913: 0 entries.")

    def test_the_seconds_of_an_entry_add_up_to_its_length(self):
        # On a real band every entry read one second short ("still 14s" in a 15 second
        # entry): 4 readings, one a second, are only 3 gaps. The first entry shows it best.
        lines = self.ok("--every", "4", "--seconds", "9.5")     # two entries; the story's lost link comes later
        saved = [ENTRY.match(line).groups() for line in lines if ENTRY.match(line)]
        self.assertEqual(len(saved), 2, lines)
        for entry in saved:
            self.assertFalse(entry[5], lines)
            self.assertEqual(sum(int(part) for part in entry[1:4]), 4, lines)
        self.assertEqual(saved[0][1:4], ("4", "0", "0"), lines)     # still all the time: the wearer walks from second 5 on
        shown = self.ok("--show")
        self.assertEqual(shown[-1].split()[2:], ["%ds" % sum(int(entry[n]) for entry in saved) for n in (1, 2, 3)])
        self.assertEqual(sum(int(part[:-1]) for part in shown[-1].split()[2:]), 8, shown)

    def test_clear_leaves_other_boxes_alone_and_the_ring_shows_the_newest_60(self):
        # 61 entries were written: number 61 went round the ring into box 400 again.
        store = {400 + n % 60: ("%d,%d,0,1,0" % (1794484800 + n, n)).encode() for n in range(1, 61)}
        store[460] = b"61"
        store[7] = b"not ours"
        self.put(store)
        shown = self.ok("--show")
        self.assertEqual(shown[0], "Diary on Vexo-HACK-913: 60 entries.")
        steps = [int(ROW.match(line).group(1)) for line in shown if ROW.match(line)]
        self.assertEqual(steps, list(range(1, 61)))             # oldest first, newest last
        self.ok("--clear")
        self.assertEqual(self.stored(), {7: b"not ours"})

    def test_an_entry_saved_with_the_link_dropped_is_marked_in_show_and_older_entries_still_read(self):
        self.put({400: b"1794484800,3,2,0,0", 401: b"1794484800,12,1,0,0,1", 402: b"1794484803,4,0,2,0", 460: b"3"})
        shown = self.ok("--show")
        self.assertEqual(shown[0], "Diary on Vexo-HACK-913: 3 entries.")
        rows = [ROW.match(line) for line in shown[2:5]]
        self.assertEqual([(row.group(1), bool(row.group(6))) for row in rows], [("3", False), ("12", True), ("4", False)])
        self.assertTrue(shown[3].endswith("  (link dropped: steps complete, seconds away not counted)"), shown[3])
        self.assertEqual(shown[-1].split(), ["total", "19", "3s", "2s", "0s"])

    def test_whatever_somebody_else_left_in_the_boxes_is_skipped_never_shown_and_never_a_crash(self):
        good = b"1794484800,12,1,2,3"
        nasty = [
            b"\xff\xfe\x00 not text",                               # not UTF-8
            "\u00b2\u00b3".encode(),                                # digits to str.isdigit(), not to int()
            "\u0661\u0662,1,1,1,1".encode(),                        # digits of another script
            b"1e400,1,1,1,1", b"nan,1,1,1,1", b"-1,-5,1,1,1",
            b"99999999999999999999999999999999,1,1,1,1",            # a time no calendar has
            b"9999999999,1,1,1,1",                                  # the year 2286
            b"0,1,1,1,1",                                           # 1970
            b"1794484800,1,1,1", b"1794484800,1,1,1,1,1,1",         # too few, too many
            b"1794484800,1,1,1,1,2", b"1794484800,1,1,1,1,", b"1794484800,1,1,1,1,x",   # the sixth part is ",1" or nothing
            b"1794484800,99999999999999999999,1,1,1",               # a bar of 10^20 characters
            b"../../../../etc/passwd", b"__import__('os').system('id')", b"\x1b[2J\x1b[31mowned",
            b"<script>alert(1)</script>", b" 1794484800,1,1,1,1\n", b"1" * 232, b"\x00",
        ]
        for count in (b"25", b"\xff", "\u00b2".encode(), b"-3", b"99999999999999999999", b"999999999", b"1e9", b" 7", b"7\n"):
            store = {400 + n: value for n, value in enumerate(nasty)}
            store[400 + len(nasty)] = good
            store[460] = count
            self.put(store)
            shown = self.ok("--show")
            text = "\n".join(shown)
            for word in ("passwd", "import", "script", "owned", "\x1b"):
                self.assertNotIn(word, text)
            self.assertRegex(shown[0], r"^Diary on Vexo-HACK-913: [01] entries\.$")
            if count == b"25":
                self.assertEqual(shown[0], "Diary on Vexo-HACK-913: 1 entries.")
                self.assertEqual(shown[-1].split(), ["total", "12", "1s", "2s", "3s"])
            # Recording on top of it works too, and --clear removes it.
            lines = self.ok("--every", "1", "--seconds", "2.6")
            self.assertTrue(any(ENTRY.match(line) for line in lines), lines)
            self.ok("--clear")
            self.assertEqual(self.stored(), {})
        self.assertEqual(os.listdir(self.work), [], "nothing from the band was used as a path")
        self.assertEqual(os.listdir(os.path.dirname(self.memory)), [os.path.basename(self.memory)])

    def test_a_real_band_needs_its_number(self):
        done = running.run(PROGRAM, self.home)
        self.assertEqual(done.returncode, 2)
        self.assertIn("give your band number", done.stderr)


if __name__ == "__main__":
    unittest.main()
