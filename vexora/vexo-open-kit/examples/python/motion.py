# Print how the band moves. x, y, z are in g (lying flat: z is about 1).
#   python3 motion.py 42        python3 motion.py --sim        Stop with Ctrl+C.
#   python3 motion.py 42 --rate 100      more readings a second: 10, 25, 50, 100 or "fastest"
import datetime

import kit
from vexo_open.sync import Band

args = kit.args(more=[("--rate", "25", "readings a second: 10, 25, 50, 100 or fastest")])
band = Band.connect(args.number, args.transport)


def show(m):                     # m.time is when the reading was taken, like time.time()
    clock = datetime.datetime.fromtimestamp(m.time).strftime("%H:%M:%S.%f")[:-3]
    print("%s   x %+.2f   y %+.2f   z %+.2f" % (clock, m.ax, m.ay, m.az))


band.on("motion", show)
band.configure(motion=args.rate if args.rate == "fastest" else int(args.rate))
band.wait(args.seconds)          # keep running; show() prints each reading
band.disconnect()
