# Draw a live graph of the band's motion. Needs one extra package:  python3 -m pip install matplotlib
#   python3 motion_graph.py 42        python3 motion_graph.py --sim
#   python3 motion_graph.py 42 --rate 100      a smoother line: 10, 25, 50 or 100 readings a second
# Close the window to stop.
import collections
import time

import kit
from vexo_open.sync import Band

try:
    import matplotlib.pyplot as plt
except ImportError:
    raise SystemExit("The graph needs matplotlib. Run:  python3 -m pip install matplotlib  "
                     "(on Windows: python -m pip install matplotlib)")

args = kit.args(more=[("--rate", "50", "readings a second: 10, 25, 50 or 100")])
band = Band.connect(args.number, args.transport)
history = collections.deque([(0, 0, 0)] * 200, maxlen=200)       # the last 200 readings (4 seconds at 50)
band.on("motion", lambda m: history.append((m.ax, m.ay, m.az)))
band.configure(motion=int(args.rate))

figure, axes = plt.subplots()
lines = axes.plot(list(history))                                 # three lines: x, y, z
axes.set_ylim(-2, 2)
axes.set_ylabel("g")
axes.legend(lines, ["x", "y", "z"], loc="upper left")
end = time.time() + args.seconds if args.seconds else None
while band.connected and plt.fignum_exists(figure.number) and (end is None or time.time() < end):
    for line, values in zip(lines, zip(*list(history))):
        line.set_ydata(values)
    plt.pause(0.05)                                              # redraw 20 times a second
band.disconnect()
print("Drew the graph. Last reading: x %+.2f  y %+.2f  z %+.2f" % history[-1])
