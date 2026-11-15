"""Band without async: every method is a plain function call that waits for the answer.

    from vexo_open.sync import Band

    band = Band.connect(42)
    band.on("tap", lambda e: print("tap!"))
    band.configure(events=True)
    band.wait(30)            # keep the program alive for 30 seconds
    band.disconnect()

A VexoError that ends your program is shown as one sentence, not as a traceback (see
short_errors below), and the band is disconnected properly when the program ends, also
after an error. A VexoError raised inside a handler is shown as the same sentence, and
the program goes on.

How it works: the real (async) Band lives on a background thread. Each call here hands the
work to that thread and waits for the result. Your handlers run on a second background
thread, one at a time, in the order the events arrived. So a handler may call the band and
use the answer (for example band.get_led() inside a "tap" handler), like the main program.
"""
import asyncio
import atexit
import collections
import inspect
import os
import sys
import threading
import time
import traceback
import weakref

from .band import Band as AsyncBand
from .band import Message, Storage as AsyncStorage  # noqa: F401  (Message is handy to import from here)
from .band import check_event
from .transport import VexoError

__all__ = ["Band", "Message", "VexoError", "short_errors"]
_PACKAGE = os.path.dirname(os.path.abspath(__file__))
_HELPERS = set()  # files outside the library that are not "your program" either: short_errors(helper=...)

# Events wait in a line until your handler has time for them. The line holds at most this
# many. The number is the one the USB transport uses for frames that wait (MAX_WAITING in
# transports/serial.py): about 6 seconds of motion at 833 samples a second, or 100 seconds
# of sound, and a few megabytes at most. When the line is full the OLDEST data event in it
# is thrown away to make room for the new one, so a slow handler sees recent data, late,
# and the program's memory does not grow. "disconnect", "reconnecting" and "reconnected"
# are never thrown away (the library makes them itself, a few at most), and a "notice"
# only when nothing else is left to throw away. band.dropped_events counts.
MAX_WAITING = 5000
KEPT_EVENTS = ("disconnect", "reconnecting", "reconnected", "stream-taken")
# With more than this many events in the line, wait() and disconnect() do not wait for the
# handlers to catch up (they could not, within their 2 seconds): they return on time.
FLOODED = 1000
DROP_HELP = ("Your handlers are slower than the band's data: %d events are waiting. From now on the oldest "
             "waiting events are dropped. Keep handlers short. band.dropped_events counts the dropped events.")


def _where(trace) -> str:
    """"line 7 of app.py": the line of the person's own program that called the library
    (or, for an error raised outside the library, the line that raised it)."""
    found = ""
    while trace is not None:
        path = trace.tb_frame.f_code.co_filename
        if os.path.abspath(path).startswith(_PACKAGE + os.sep) or os.path.abspath(path) in _HELPERS:
            break
        found = "line %d of %s" % (trace.tb_lineno, os.path.basename(path))
        trace = trace.tb_next
    return found


def short_errors(helper=None) -> None:
    """Show a band problem that ends the program as one sentence instead of a traceback:

        Problem (not-available): Decoding the band's sound needs the 'av' package. ...
          (line 7 of app.py)

    Band.connect switches this on by itself. Only VexoError is shown this way: its message
    is written for the person at the keyboard. Every other error (a bug in the program)
    keeps its full traceback. The program still ends with exit code 1, and a connected band
    is still disconnected first. For the full traceback of a VexoError too, set the
    environment variable VEXO_TRACEBACK=1, or catch the error yourself.

    helper: the path of a file of helpers that raises VexoError for the program that uses
    it (the kit's examples/python/speech.py does: short_errors(helper=__file__)). The line
    that is shown is then the one in the person's program, not one inside the helper."""
    if helper:
        _HELPERS.add(os.path.abspath(helper))
    before = sys.excepthook
    if getattr(before, "vexo_open", False):
        return

    def hook(kind, error, trace):
        if not isinstance(error, VexoError) or os.environ.get("VEXO_TRACEBACK"):
            return before(kind, error, trace)
        sys.stdout.flush()
        print("Problem (%s): %s" % (error.code, error), file=sys.stderr)
        where = _where(trace)
        if where:
            print("  (%s)" % where, file=sys.stderr)

    hook.vexo_open = True
    sys.excepthook = hook


class _Runner:
    """A private asyncio event loop on a background thread."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, name="vexo-open", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()
        for task in asyncio.all_tasks(self.loop):  # stop whatever is left (e.g. a simulator clock)
            task.cancel()
        self.loop.run_until_complete(asyncio.sleep(0))
        self.loop.close()

    @property
    def running(self) -> bool:
        return self.thread.is_alive() and not self.loop.is_closed()

    def on_loop_thread(self) -> bool:
        return threading.current_thread() is self.thread

    def call(self, make_coroutine):
        """Run a coroutine on the loop and wait for its result.

        Called from the loop thread itself waiting would freeze everything, so there the
        work is only scheduled and None is returned. That only happens to an `async def`
        handler; plain handlers run on their own thread (see _Handlers) and do wait.
        """
        if not self.running:
            raise VexoError("not-connected", "The band is disconnected. Connect again first.")
        if self.on_loop_thread():
            self.loop.create_task(make_coroutine()).add_done_callback(_report)
            return None
        future = asyncio.run_coroutine_threadsafe(make_coroutine(), self.loop)
        done = threading.Event()
        future.add_done_callback(lambda _f: done.set())
        try:
            while not done.wait(0.1):  # short waits, so Ctrl+C is noticed on every system
                if not self.running:
                    raise VexoError("not-connected", "The band is disconnected. Connect again first.")
        except BaseException:
            future.cancel()
            raise
        return future.result()

    def stop(self) -> None:
        if not self.running:
            return
        self.loop.call_soon_threadsafe(self.loop.stop)
        if not self.on_loop_thread():
            self.thread.join(2)


def _short(error, trace, event=None) -> bool:
    """Show a band problem (VexoError) raised in a handler the way short_errors shows one
    that ends the program: one sentence, then the line of the person's program. Returns
    False for any other error and with VEXO_TRACEBACK set: those keep their traceback."""
    if not isinstance(error, VexoError) or os.environ.get("VEXO_TRACEBACK"):
        return False
    sys.stdout.flush()
    where = [_where(trace)] if _where(trace) else []
    if event:
        where.append('in your "%s" handler' % event)
    lines = ["Problem (%s): %s" % (error.code, error)]
    if where:
        lines.append("  (%s)" % ", ".join(where))
    print("\n".join(lines), file=sys.stderr)  # one print: lines of two threads do not mix
    return True


def _report(task) -> None:
    """Print the error of a call made from inside a handler (nobody is waiting for it)."""
    if not task.cancelled() and task.exception() is not None:
        e = task.exception()
        if _short(e, None):
            return
        print("Error in a band call made from a handler:", file=sys.stderr)
        traceback.print_exception(type(e), e, e.__traceback__)


class _Handlers:
    """Runs your handlers on a thread of their own, one at a time, in the order the events
    arrived. The band's thread only puts each event in a line and goes on, so a handler
    may call the band and wait for the answer without freezing anything."""

    def __init__(self, runner: _Runner) -> None:
        self._runner = runner
        self._line = collections.deque()  # events (tuples), flush markers and the final None, oldest first
        self._ready = threading.Condition()  # guards _line, _events and dropped
        self._events = 0  # how many items of _line are events: never more than MAX_WAITING (see _make_room)
        self.dropped = 0  # events thrown away because the handlers did not keep up
        self._busy = 0  # `async def` handlers that have started and not finished
        self._warned = False  # the one line about dropped events was printed
        self._thread = None
        self._wrapped = {}  # (event, handler) -> the functions given to the real band for it

    def _put(self, item) -> None:
        """Add to the line. An event (a tuple) may push the oldest data event out."""
        first_drop = False
        with self._ready:
            if isinstance(item, tuple):
                if self._events >= MAX_WAITING and self._make_room() and not self._warned:
                    first_drop = self._warned = True
                self._events += 1
            self._line.append(item)
            self._ready.notify()
        if first_drop:  # one line, the first time only
            print(DROP_HELP % MAX_WAITING, file=sys.stderr)

    def _make_room(self) -> bool:
        """Throw away the oldest data event; a notice if there is no other. (Lock held.)"""
        for droppable in (lambda event: event not in KEPT_EVENTS and event != "notice",
                          lambda event: event == "notice"):
            for at, item in enumerate(self._line):
                if isinstance(item, tuple) and droppable(item[1]):
                    del self._line[at]
                    self._events -= 1
                    self.dropped += 1
                    return True
        return False  # only link events wait: the library makes those itself, they cannot pile up

    def _get(self):
        with self._ready:
            while not self._line:
                self._ready.wait()
            item = self._line.popleft()
            if isinstance(item, tuple):
                self._events -= 1
            return item

    def flooded(self) -> bool:
        return self._events > FLOODED

    def _forget_data(self) -> None:
        """Throw away every data event that waits (they count as dropped); link events stay."""
        with self._ready:
            keep = [item for item in self._line if not isinstance(item, tuple) or item[1] in KEPT_EVENTS]
            gone = len(self._line) - len(keep)
            self._line = collections.deque(keep)
            self._events -= gone
            self.dropped += gone

    def here(self) -> bool:
        return threading.current_thread() is self._thread

    def wrap(self, event: str, handler):
        """The function the real band calls instead of `handler`: it only queues the event."""
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="vexo-open-handlers", daemon=True)
            self._thread.start()

        def queued(msg):
            if self._thread.is_alive():
                self._put((queued, event, handler, msg))
        queued.active = True
        self._wrapped.setdefault((event, handler), []).append(queued)
        return queued

    def unwrap(self, event: str, handler):
        """Forget one registration of `handler`; events of it still in the line are dropped."""
        waiting = self._wrapped.get((event, handler))
        if not waiting:
            return None
        queued = waiting.pop()
        queued.active = False
        return queued

    def _run(self) -> None:
        while True:
            item = self._get()
            if item is None:
                return
            if isinstance(item, threading.Event):  # a marker from flush(): everything before it is done
                item.set()
                continue
            queued, event, handler, msg = item
            if not queued.active:
                continue
            try:
                if (self._busy >= MAX_WAITING and event not in KEPT_EVENTS
                        and inspect.iscoroutinefunction(handler)):
                    with self._ready:  # that many `async def` handlers still run: do not start more
                        self.dropped += 1
                    continue
                result = handler(msg)
                if inspect.isawaitable(result) and self._runner.running:  # an `async def` handler
                    with self._ready:
                        self._busy += 1
                    asyncio.run_coroutine_threadsafe(result, self._runner.loop).add_done_callback(
                        lambda future, event=event: self._async_done(event, future))
            except Exception as e:
                # The program goes on after an error in a handler. A band problem is shown
                # as one sentence; a bug in the handler keeps its traceback.
                if not _short(e, e.__traceback__.tb_next, event):  # tb_next: skip this function
                    print('Error in your "%s" handler:' % event, file=sys.stderr)
                    traceback.print_exc()

    def _async_done(self, event: str, future) -> None:
        """An `async def` handler has ended: show its error, if any, like any handler's."""
        with self._ready:
            self._busy -= 1
        if future.cancelled() or future.exception() is None:
            return
        e = future.exception()
        if not _short(e, e.__traceback__, event):
            print('Error in your "%s" handler:' % event, file=sys.stderr)
            traceback.print_exception(type(e), e, e.__traceback__)

    def flush(self, seconds: float = 2.0) -> None:
        """Wait until the handlers of every event that has arrived so far have run. With a
        long line of events waiting (flooded) there is no point: return at once."""
        if self._thread is None or self.here() or not self._thread.is_alive() or self.flooded():
            return
        done = threading.Event()
        self._put(done)
        until = time.monotonic() + seconds
        while time.monotonic() < until and not done.wait(0.1) and self._thread.is_alive():
            pass

    def stop(self, seconds: float = 2.0) -> None:
        """Run what is still in the line, then end the thread. With a long line (flooded),
        or when that takes more than `seconds`, the data events that still wait are thrown
        away, so no handler runs long after the program moved on; link events still run."""
        if self._thread is None or not self._thread.is_alive():
            return
        if self.flooded():
            self._forget_data()
        self._put(None)
        if not self.here():
            self._thread.join(seconds)
            if self._thread.is_alive():
                self._forget_data()


class _OnBandThread:
    """band.transport for plain programs: the transport itself, but every method of it runs
    on the band's own thread and the caller waits for the result. So a line like
    band.transport.drop_link() (a pretend band losing its link) is safe in the main program
    and in a handler. Reading and setting plain values goes straight through."""

    def __init__(self, target, runner: _Runner) -> None:
        object.__setattr__(self, "_target", target)
        object.__setattr__(self, "_runner", runner)

    def __getattr__(self, name: str):
        target, runner = self._target, self._runner
        value = getattr(target, name)
        if not inspect.ismethod(value):
            return value

        def call(*args, **kwargs):
            if runner.on_loop_thread() or not runner.running:
                return value(*args, **kwargs)

            async def work():
                result = value(*args, **kwargs)
                return await result if inspect.isawaitable(result) else result
            return runner.call(work)
        call.__name__ = name
        call.__doc__ = value.__doc__
        return call

    def __setattr__(self, name: str, value) -> None:
        setattr(self._target, name, value)

    def __repr__(self) -> str:
        return repr(self._target)


def _blocking(name: str, get_target):
    """Make the plain-call version of the async method `name`."""
    def method(self, *args, **kwargs):
        return self._runner.call(lambda: getattr(get_target(self), name)(*args, **kwargs))
    return method


def _copy_methods(sync_class, async_class, get_target, skip=()) -> None:
    """Give sync_class a blocking twin of every public async method of async_class.
    They are generated from the async class, so the two can never drift apart."""
    for name, fn in inspect.getmembers(async_class, inspect.iscoroutinefunction):
        if name.startswith("_") or name in skip:
            continue
        method = _blocking(name, get_target)
        method.__name__ = name
        method.__doc__ = fn.__doc__
        try:
            method.__signature__ = inspect.signature(fn)
        except (TypeError, ValueError):
            pass
        setattr(sync_class, name, method)


class Storage:
    """band.storage with plain calls: put, get, get_text, delete, info, wipe."""

    def __init__(self, band: AsyncBand, runner: _Runner) -> None:
        self._storage = band.storage
        self._runner = runner


_copy_methods(Storage, AsyncStorage, lambda self: self._storage)


class Band:
    """One Vexo Open band, without async. Same methods as vexo_open.Band.

    Handlers run on a background thread, one at a time, in the order the events arrived.
    A handler may call the band like the main program does: band.set_led("#ff0000"),
    value = band.get_led(), band.storage.put(1, "x") and band.wait(2) all work and wait
    for the answer. While one handler runs, the next events wait in line, so keep handlers
    short when data arrives fast.
    """

    @classmethod
    def connect(cls, number=None, transport="ble", auto_reconnect=False, reconnect_seconds=120,
                **options) -> "Band":
        """Connect and return the band. Same arguments as the async Band.connect:
        Band.connect(42), Band.connect(42, transport="serial", port="COM5"),
        Band.connect(42, transport="sim").

        auto_reconnect=True: when the link drops, keep trying to get it back for
        reconnect_seconds. You get a "reconnecting" event, then "reconnected" or
        "disconnect"; wait() keeps waiting meanwhile.

        More options: set_clock=False (do not set the band's clock to this computer's
        time) and protocol="1.0" (treat the band as one with firmware 1.0)."""
        short_errors()  # a VexoError nobody catches: one sentence, not a traceback
        runner = _Runner()
        try:
            band = runner.call(lambda: AsyncBand.connect(
                number, transport, auto_reconnect=auto_reconnect, reconnect_seconds=reconnect_seconds,
                **options))
        except BaseException:
            runner.stop()
            raise
        return cls(band, runner)

    def __init__(self, band: AsyncBand, runner: _Runner) -> None:
        self._band = band
        self._runner = runner
        self._gone = threading.Event()  # set when the band disconnects for good (not while reconnecting)
        self._handlers = _Handlers(runner)
        self.storage = Storage(band, runner)
        band.on("disconnect", lambda _event: self._gone.set())
        if not band.connected:
            self._gone.set()
        # If the program just ends, still say goodbye to the band properly. The exit handler
        # is taken away again in disconnect(), so a program that makes many bands does not
        # collect one handler for each.
        ref = weakref.ref(self)
        self._at_exit = lambda: ref() and ref().disconnect()
        atexit.register(self._at_exit)

    # Plain values, read straight from the real band.
    number = property(lambda self: self._band.number)
    name = property(lambda self: self._band.name)
    info = property(lambda self: self._band.info)
    connected = property(lambda self: self._band.connected)
    reconnecting = property(lambda self: self._band.reconnecting)
    streams = property(lambda self: self._band.streams)
    taken_streams = property(lambda self: self._band.taken_streams)  # streams another program took (see take_back)
    clock = property(lambda self: self._band.clock)  # "band" or "computer": where event times come from
    transport = property(lambda self: _OnBandThread(self._band.transport, self._runner))  # safe from any thread
    transport_kind = property(lambda self: self._band.transport_kind)
    # How many events were thrown away because the handlers did not keep up (see MAX_WAITING).
    dropped_events = property(lambda self: self._handlers.dropped + self._band.dropped_events)

    def on(self, event: str, handler=None):
        """Listen for an event: band.on("tap", fn), or as a decorator: @band.on("tap").
        Handlers run on a background thread, one at a time. They may call band methods
        and use the answers (see the class text). A name that is not an event raises
        VexoError "bad-event" and lists the events."""
        check_event(event)
        if handler is None:
            return lambda fn: self.on(event, fn)
        self._band.on(event, self._handlers.wrap(event, handler))
        return handler

    def off(self, event: str, handler) -> None:
        """Stop calling handler for event. A name that is not an event raises VexoError
        "bad-event", as in on(). (A handler that was never added: nothing happens.)"""
        check_event(event)
        queued = self._handlers.unwrap(event, handler)
        if queued is not None:
            self._band.off(event, queued)

    def wait(self, seconds=None) -> bool:
        """Keep the program alive so handlers can run: for `seconds`, or with no number
        until you press Ctrl+C. Returns early if the band disconnects. With
        auto_reconnect it keeps waiting while the library gets a dropped link back.
        Returns True if the band is connected afterwards. `seconds` is measured on the
        computer's clock, however much data arrives. Before it returns, wait() gives the
        handlers of what has arrived up to 2 seconds more to finish; not when more than
        1000 events are waiting, then it returns at once.
        Inside a handler it works too; the next events wait until the handler is done."""
        if self._runner.on_loop_thread():
            return self.connected  # an `async def` handler: waiting here would freeze the band's data
        try:
            until = None if seconds is None else time.monotonic() + seconds  # the computer's clock
            while not self._gone.is_set():
                left = 0.1 if until is None else until - time.monotonic()
                if left <= 0:
                    break
                self._gone.wait(min(0.1, left))
        except KeyboardInterrupt:
            pass  # Ctrl+C is the normal way to end wait()
        self._handlers.flush()  # so the handlers of what has arrived have run before we return
        return self.connected

    def disconnect(self) -> None:
        """Close the link and stop the background thread. Safe to call twice.
        Also stops a reconnect that is going on."""
        atexit.unregister(self._at_exit)
        if not self._runner.running:
            return
        if self._runner.on_loop_thread():  # from an `async def` handler: cannot wait for ourselves
            task = self._runner.loop.create_task(self._band.disconnect())
            task.add_done_callback(lambda _t: (self._runner.stop(), self._handlers.stop()))
            return
        try:
            self._runner.call(self._band.disconnect)
        finally:
            self._handlers.stop()  # the handlers of the last events (also "disconnect") run first
            self._runner.stop()
            self._gone.set()

    def __enter__(self) -> "Band":
        return self

    def __exit__(self, *exc) -> None:
        self.disconnect()

    def __repr__(self) -> str:
        state = "connected" if self.connected else "reconnecting" if self.reconnecting else "disconnected"
        return "<Band %s %s>" % (self.name, state)


_copy_methods(Band, AsyncBand, lambda self: self._band, skip=("connect", "disconnect"))
