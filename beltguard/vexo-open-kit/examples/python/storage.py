# Save a short text on the band, read it back, and remove it again. Without the last step
# it would stay there, also while the band is off, for the next team to find.
#   python3 storage.py 42        python3 storage.py --sim
import kit
from vexo_open.sync import Band

args = kit.args()
band = Band.connect(args.number, args.transport)
print("Careful: the next person who connects to this band can read, change or erase anything you store. Never store a secret.")
print("Found in box 7:", kit.plain(band.storage.get_text(7)))   # None if nobody saved anything yet. kit.plain:
# text on a band was written by someone else, so it is made safe to print (kit.py says how)
band.storage.put(7, "hello from %s" % band.name)       # boxes are numbered 0..999
print("Saved, and read back:", kit.plain(band.storage.get_text(7)))
print("Boxes in use:", band.storage.info().used_keys)
band.storage.delete(7)                                 # tidy up. To erase everything on a band: band.storage.wipe()
print("Removed it again: box 7 is empty for the next team. (Leave out the delete line and it stays.)")
band.disconnect()
