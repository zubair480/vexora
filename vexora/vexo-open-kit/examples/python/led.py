# Light the band's LED: red, green, blue, then blink white.
#   python3 led.py 42        (your band number)      python3 led.py --sim   (no band)
import time

import kit
from vexo_open.sync import Band

args = kit.args(seconds=3)
band = Band.connect(args.number, args.transport)
print("Connected to", band.name)
for colour in ["#ff0000", "#00ff00", "#0000ff"]:
    print("LED", colour)
    band.set_led(colour)
    time.sleep(1)
print("LED #ffffff, blinking for %g seconds, then off" % args.seconds)
band.set_led("#ffffff", pattern="blink", period=500)   # blink white twice a second
band.wait(args.seconds)
band.disconnect()                                      # the LED goes off, unless another program is still connected
