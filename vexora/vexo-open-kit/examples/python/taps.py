# Tap the band: one tap turns the LED green, a double tap turns it pink.
#   python3 taps.py 42        python3 taps.py --sim   (the pretend band taps itself)
import kit
from vexo_open.sync import Band

args = kit.args()
band = Band.connect(args.number, args.transport)
band.on("tap", lambda e: band.set_led("#00ff00"))
band.on("double-tap", lambda e: band.set_led("#ff00aa"))
band.on("event", lambda e: print("The band felt:", e.kind))
band.configure(events=True)
print("Tap the band. Stop with Ctrl+C.")
band.wait(args.seconds)
band.disconnect()
