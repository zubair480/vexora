# Step diary

**Medium. Python.**

A diary of steps and activity that lives on the band itself. Every minute the program saves
one small entry in the band's storage: when it began, how many steps, and how many seconds
the wearer was still, walking and running. Close the laptop, come back tomorrow, run it
with `--show`: the diary is still there, as a table with a bar for every entry.

## Run it

In `my-project/`, the folder that holds the kit (on Windows type `python` for `python3`):

- **Pretend band:** two commands, one after the other:

      python3 vexo-open-kit/projects/step-diary/diary.py --sim --every 3
      python3 vexo-open-kit/projects/step-diary/diary.py --sim --show

  In the first the pretend wearer walks, runs, loses the link for 2 seconds, sits down and
  walks again, and an entry is saved every 3 seconds (it stops after about 25 seconds).
  The second prints the diary the first one saved: this pretend band keeps its storage
  between runs, like a real band (in `.vexo-open/` in your home folder, readable by you only).
- **Real band:** `python3 vexo-open-kit/projects/step-diary/diary.py 42` with your own
  number in place of 42 (needs `python3 -m pip install bleak`; double-tap the band first).
  Add `--usb` for the cable. Walk around, stop it with Ctrl+C, then run it again with `--show`.

Options: `--every 60` (seconds in one entry), `--show`, `--clear`, `--seconds 600` (stop by itself).

## Read the code

`diary.py` (about 200 lines). The numbers to tune are at the top (`EVERY_SECONDS`, the
boxes, `GAP_SECONDS`, `WAITING_MOST` = at most 60 entries wait for the link); nothing here was tried on a real band. `read_diary()` is the storage
format: 60 boxes used as a ring, plus one that counts. The loop at the bottom takes in the
readings, closes an entry when its time is up, and saves what is waiting. The
`if args.pretend:` block is the story, told with the kit's `sim.set_activity(...)` and `sim.drop_link(2)`.

## Kit features it uses

Activity (`steps`, `still` / `walk` / `run`), storage (`put`, `get`, `delete`), the real time on every
event, auto-reconnect (entries wait in the computer while the link is down and are saved when it is back).

## Good to know

- Storage is not private. Anyone who connects can read, erase or rewrite the diary, so it
  holds numbers only, and `read_box()` skips whatever is not exactly the numbers this
  program writes. `--clear` deletes boxes 400 to 460 and nothing else.
- `steps` is a total the band counts by itself, so steps taken while the link is down
  are not lost, but those seconds are in no column. Such an entry ("12 steps, still 1s") is marked
  `(link dropped: steps complete, seconds away not counted)`. Steps taken while the program is off are not in the diary.
- The diary holds the newest 60 entries: one hour at `--every 60`. An entry that is not
  finished when you stop the program is not saved.

## Try next

- Save the step total in the count box too, so steps taken while the program was off show
  up as one "while you were away" entry on the next start.
- Set a goal: light the LED green when today's entries add up to 500 steps.
- Pack an entry into 8 bytes with `struct.pack` and put 20 entries in one box: a diary of
  a whole day.
