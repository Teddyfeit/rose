"""A tiny stand-in for tkinter, so the desktop widgets can be tested headless.

It records what would be drawn instead of drawing it.  That is enough to catch
the bugs that actually happen in GUI code - a misnamed method, a coordinate
that falls outside the card, a label that never gets updated, a config file that
is not written - without needing a display (or even a Tk build) on the machine
running the tests.
"""

from __future__ import annotations

import sys
import types


class TclError(Exception):
    pass


class _Item:
    def __init__(self, kind, args, kwargs):
        self.kind = kind
        self.args = args
        self.kwargs = kwargs
        self.tags = str(kwargs.get("tags", "")).split()

    def __repr__(self):                      # pragma: no cover - debugging aid
        return f"<{self.kind} {self.kwargs.get('text', '')!r}>"


class Canvas:
    def __init__(self, master=None, **kwargs):
        self.master = master
        self.options = kwargs
        self.items = []
        self.bindings = {}
        self.width = kwargs.get("width", 200)
        self.height = kwargs.get("height", 120)

    def pack(self, **_kw):
        return None

    def bind(self, sequence, func):
        self.bindings[sequence] = func

    def _add(self, kind, *args, **kwargs):
        item = _Item(kind, args, kwargs)
        self.items.append(item)
        return item

    def create_text(self, *a, **kw):
        return self._add("text", *a, **kw)

    def create_rectangle(self, *a, **kw):
        return self._add("rect", *a, **kw)

    def create_oval(self, *a, **kw):
        return self._add("oval", *a, **kw)

    def create_polygon(self, *a, **kw):
        return self._add("poly", *a, **kw)

    def create_line(self, *a, **kw):
        return self._add("line", *a, **kw)

    def delete(self, tag):
        tag = str(tag)
        self.items = [i for i in self.items if tag not in i.tags]

    # convenience for the tests
    def texts(self):
        return [i.kwargs.get("text", "") for i in self.items if i.kind == "text"]

    def find(self, kind):
        return [i for i in self.items if i.kind == kind]


class Toplevel:
    def __init__(self, master=None, **kwargs):
        self.master = master
        self.attributes_ = {}
        self.overrideredirect_ = False
        self._x = 0
        self._y = 0
        self._w = 200
        self._h = 120
        self.destroyed = False
        self.children = []
        self.options = kwargs

    # -- window management -------------------------------------------------- #
    def overrideredirect(self, value=None):
        if value is None:
            return self.overrideredirect_
        self.overrideredirect_ = bool(value)
        return None

    def attributes(self, *args):
        if len(args) == 2:
            self.attributes_[args[0]] = args[1]
            return None
        if len(args) == 1:
            if args[0] not in self.attributes_:
                raise TclError("no such attribute")
            return self.attributes_[args[0]]
        return dict(self.attributes_)

    def geometry(self, spec=None):
        if spec is None:
            return "%dx%d+%d+%d" % (self._w, self._h, self._x, self._y)
        spec = spec.replace(" ", "")
        if spec.startswith("+"):
            _, x, y = spec.split("+")
            self._x, self._y = int(x), int(y)
            return None
        size, x, y = spec.split("+")
        w, h = size.split("x")
        self._w, self._h, self._x, self._y = int(w), int(h), int(x), int(y)
        return None

    def winfo_x(self):
        return self._x

    def winfo_y(self):
        return self._y

    def winfo_width(self):
        return self._w

    def winfo_height(self):
        return self._h

    def configure(self, **kw):
        self.options.update(kw)

    def destroy(self):
        self.destroyed = True

    def bind(self, *_a, **_kw):
        return None

    def lift(self):
        return None


class Menu:
    def __init__(self, master=None, **kwargs):
        self.master = master
        self.options = kwargs
        self.entries = []

    def add_command(self, label=None, command=None, **_kw):
        self.entries.append(("command", label, command))

    def add_separator(self, **_kw):
        self.entries.append(("separator", None, None))

    def add_cascade(self, label=None, menu=None, **_kw):
        self.entries.append(("cascade", label, menu))

    def tk_popup(self, *_a, **_kw):
        return None

    def grab_release(self):
        return None

    def invoke(self, label):
        for kind, name, payload in self.entries:
            if kind == "command" and name == label:
                payload()
                return True
        raise KeyError(label)


class _Fonts:
    @staticmethod
    def families():
        return ("Segoe UI", "DejaVu Sans", "TkDefaultFont")


class Tk:
    def __init__(self, **kwargs):
        self.options = kwargs
        self.withdrawn = False
        self.title_ = ""
        self.scheduled = []
        self.destroyed = False
        self._toplevels = []
        self._screen = (1920, 1080)
        self.bound = {}

    def withdraw(self):
        self.withdrawn = True

    def title(self, text=None):
        if text is not None:
            self.title_ = text

    def winfo_screenwidth(self):
        return self._screen[0]

    def winfo_screenheight(self):
        return self._screen[1]

    def after(self, ms, func=None):
        self.scheduled.append((ms, func))
        return len(self.scheduled)

    def after_cancel(self, handle):
        return None

    def protocol(self, name, func=None):
        self.protocols = getattr(self, "protocols", {})
        if func is not None:
            self.protocols[name] = func

    def createcommand(self, name, func):
        raise TclError("not supported in the stub")

    def bind_all(self, sequence, func):
        self.bound[sequence] = func

    def mainloop(self, n=0):
        return None

    def destroy(self):
        self.destroyed = True

    # simulate one timer tick, like the event loop would
    def run_next(self):
        if self.scheduled:
            _ms, func = self.scheduled.pop(0)
            func()
            return True
        return False


def install():
    """Put the stub into ``sys.modules`` as ``tkinter`` (+ ``tkinter.font``)."""
    module = types.ModuleType("tkinter")
    module.Tk = Tk
    module.Toplevel = Toplevel
    module.Canvas = Canvas
    module.Menu = Menu
    module.TclError = TclError
    module.font = _Fonts
    fonts = types.ModuleType("tkinter.font")
    fonts.families = _Fonts.families
    module.font = fonts
    sys.modules["tkinter"] = module
    sys.modules["tkinter.font"] = fonts
    return module


def uninstall():
    sys.modules.pop("tkinter", None)
    sys.modules.pop("tkinter.font", None)
