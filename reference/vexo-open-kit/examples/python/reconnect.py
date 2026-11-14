# Keep going when the link drops: the library connects again and sets everything again.
#   python3 reconnect.py 42      python3 reconnect.py --sim   (the pretend band drops the link)
import kit
from vexo_open.sync import Band

args = kit.args()
band = Band.connect(args.number, args.transport, auto_reconnect=True)
band.on("reconnecting", lambda e: print("Link lost. Trying again for", e.seconds, "seconds."))
band.on("reconnected", lambda e: print("Back after %.1f seconds. The LED is green again." % e.seconds))
band.on("disconnect", lambda e: print("Disconnected:", e.reason))
band.set_led("#00ff00")
band.configure(events=True)
if args.transport == "sim":      # the pretend band taps itself after 4 seconds: lose the link then
    band.on("tap", lambda e: band.transport.drop_link())
print("Walk away with the band until the link drops, then come back. Stop with Ctrl+C.")
band.wait(args.seconds)
band.disconnect()
