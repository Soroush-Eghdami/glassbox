"""Glassbox - tiny live system monitor."""

import threading
import time

import psutil
from rich import box
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Static

import stats


def gig(n):
    # bytes -> short GB
    return f"{n / (1024 ** 3):.1f}G"


def lvl(pct):
    # color by how hot it is
    if pct >= 85:
        return "red"
    if pct >= 60:
        return "yellow"
    return "green"


def hbar(pct, width=14):
    # bar only, pct lives in its own col so it never drifts
    pct = max(0, min(100, float(pct or 0)))
    n = int(pct / 100 * width)
    t = Text()
    t.append("[", style="dim")
    t.append("#" * n, style=f"bold {lvl(pct)}")
    t.append("-" * (width - n), style="dim")
    t.append("]", style="dim")
    return t


def pct_txt(pct):
    # pinned pct col, btop style
    pct = max(0, min(100, float(pct or 0)))
    return Text(f"{pct:5.1f}%", style=f"bold {lvl(pct)}")


def hmini(pct, width=10):
    # tiny bar for table rows
    pct = max(0, min(100, float(pct or 0)))
    n = int(pct / 100 * width)
    t = Text()
    t.append("#" * n, style=lvl(pct))
    t.append("-" * (width - n), style="dim")
    return t


def card(title, body, accent, sub=None):
    # one rounded panel per box, name sits under it
    return Panel(body, title=f" {title} ", subtitle=f" {sub} " if sub else None,
                 subtitle_align="right", border_style=accent, box=box.ROUNDED, padding=(0, 1))


def grid():
    # tight table, no extra gaps so bars fit
    return Table(show_header=False, box=None, pad_edge=False, padding=(0, 1), expand=True)


class CpuBox(Static):
    def update_box(self, d, name="CPU"):
        freq = d["freq"]
        ghz = f"{freq.current / 1000:.2f}GHz" if freq else ""
        t = grid()
        t.add_column("c", width=8)
        t.add_column("bar", ratio=1)
        t.add_column("pct", width=6, justify="right")
        t.add_row(Text("TOTAL", style="bold cyan"), hbar(d["total"]), pct_txt(d["total"]))
        if ghz:
            t.add_row(Text(ghz, style="dim"), Text("", style="dim"), Text("", style="dim"))
        for i, c in enumerate(d["cores"]):
            t.add_row(Text(f"c{i}", style="dim"), hbar(c, width=12), pct_txt(c))
        self.update(card(f"CPU {d['total']:.0f}%", t, "cyan", sub=name[:30]))


class MemBox(Static):
    def update_box(self, m):
        v, s = m["virt"], m["swap"]
        t = grid()
        t.add_column("k", width=5)
        t.add_column("bar", ratio=1)
        t.add_column("pct", width=6, justify="right")
        # numbers on their own dim line, bar never fights for space
        t.add_row(Text("RAM", style="bold magenta"), hbar(v.percent, width=12), pct_txt(v.percent))
        t.add_row(Text("", style="dim"), Text(f"{gig(v.used)}/{gig(v.total)} used", style="dim"))
        t.add_row(Text("swap", style="dim"), hbar(s.percent, width=12), pct_txt(s.percent))
        t.add_row(Text("", style="dim"), Text(f"{gig(s.used)}/{gig(s.total)} used", style="dim"))
        self.update(card(f"MEM {gig(v.used)}/{gig(v.total)}", t, "magenta"))


class DiskBox(Static):
    def update_box(self, d):
        t = grid()
        t.add_column("m", width=6)
        t.add_column("bar", ratio=1)
        t.add_column("pct", width=6, justify="right")
        for mount, u in d["parts"][:3]:
            label = mount if len(mount) <= 6 else mount[-6:]
            t.add_row(Text(label, style="bold"), hbar(u.percent, width=12), pct_txt(u.percent))
            t.add_row(Text("", style="dim"), Text(f"{gig(u.used)}/{gig(u.total)} used", style="dim"))
        if not d["parts"]:
            t.add_row(Text("no disk", style="dim"))
        self.update(card("DISK", t, "blue"))


class NetBox(Static):
    _prev = None
    _at = 0.0

    def update_box(self, n):
        now = time.time()
        up_s = down_s = 0.0
        if n["total"] and self._prev:
            dt = max(now - self._at, 0.1)
            down_s = (n["total"].bytes_recv - self._prev.bytes_recv) / dt
            up_s = (n["total"].bytes_sent - self._prev.bytes_sent) / dt
        if n["total"]:
            self._prev, self._at = n["total"], now

        def rate(v):
            # bytes/s -> nice string
            if v > 1024 ** 2:
                return f"{v / 1024 ** 2:.1f}MB/s"
            return f"{v / 1024:.0f}KB/s"

        t = grid()
        t.add_column("k", width=5)
        t.add_column("v", ratio=1)
        t.add_row(Text("down", style="bold green"), Text(rate(down_s), style="green"))
        t.add_row(Text("up", style="bold yellow"), Text(rate(up_s), style="yellow"))
        self.update(card("NET", t, "green"))


class GpuBox(Static):
    def update_box(self, nv, win, npu):
        t = grid()
        t.add_column("k", width=5)
        t.add_column("bar", ratio=1)
        t.add_column("pct", width=6, justify="right")
        top = 0.0
        for c in nv:
            top = max(top, c["load"])
            mem_pct = c["mem_used"] / c["mem_total"] * 100 if c["mem_total"] else 0
            t.add_row(Text("load", style="bold"), hbar(c["load"], width=12), pct_txt(c["load"]))
            t.add_row(Text("vram", style="dim"), hbar(mem_pct, width=12), pct_txt(mem_pct))
            t.add_row(Text("", style="dim"), Text(f"{gig(c['mem_used'])}/{gig(c['mem_total'])} {c['name'][:18]}", style="dim"))
        for c in win:
            top = max(top, c["load"])
            tag = c.get("tag", "gpu")
            style = "bold green" if tag == "igpu" else ("bold red" if tag == "dgpu" else "bold")
            t.add_row(Text(tag, style=style), hbar(c["load"], width=12), pct_txt(c["load"]))
            if c["mem_total"]:
                mem_pct = c["mem_used"] / c["mem_total"] * 100
                t.add_row(Text("vram", style="dim"), hbar(mem_pct, width=12), pct_txt(mem_pct))
                t.add_row(Text("", style="dim"), Text(f"{gig(c['mem_used'])}/{gig(c['mem_total'])} {c['name'][:18]}", style="dim"))
            else:
                t.add_row(Text("", style="dim"), Text(f"{gig(c['mem_used'])} {c['name'][:18]}", style="dim"))
        for c in npu:
            top = max(top, c["load"])
            t.add_row(Text("npu", style="bold purple"), hbar(c["load"], width=12), pct_txt(c["load"]))
            t.add_row(Text("", style="dim"), Text(c["name"][:30], style="dim"))
        if len(t.rows) == 0:
            self.update(card("GPU / NPU", Text("no gpu found", style="dim"), "dim"))
            return
        self.update(card(f"GPU {top:.0f}%", t, "red"))


class SensorBox(Static):
    def update_box(self, s):
        t = grid()
        t.add_column("k", width=5)
        t.add_column("bar", ratio=1)
        t.add_column("pct", width=6, justify="right")
        if s["battery"]:
            b = s["battery"]
            icon = "+" if b.power_plugged else "o"
            t.add_row(Text("batt", style="bold"), hbar(b.percent, width=12), pct_txt(b.percent))
            t.add_row(Text("", style="dim"), Text(f"{icon} {'plugged' if b.power_plugged else 'on batt'}", style="dim"))
        for name, entries in list(s["temps"].items())[:3]:
            for e in entries[:2]:
                t.add_row(Text((e.label or name)[:5], style="bold orange1"), Text(f"{e.current:.0f}C", style="orange1"))
        for name, entries in list(s["fans"].items())[:2]:
            for e in entries[:2]:
                t.add_row(Text((e.label or name)[:5], style="dim"), Text(f"{e.current:.0f}rpm", style="dim"))
        if len(t.rows) == 0:
            # windows hides most sensors
            up = int(time.time() - psutil.boot_time())
            h, rem = divmod(up, 3600)
            m, _ = divmod(rem, 60)
            t.add_row(Text("up", style="bold"), Text(f"{h}h {m}m", style="yellow"))
        self.update(card("SENSORS", t, "yellow"))


class Glassbox(App):
    CSS = """
    Screen { background: #0f1116; }
    #left { width: 44; }
    #right { width: 1fr; }
    CpuBox { height: auto; max-height: 45%; }
    MemBox, DiskBox, NetBox, GpuBox, SensorBox { height: auto; }
    #proc-head { height: 3; border: round #2a2f3a; background: #161b24; color: #9aa4b2; }
    DataTable { height: 1fr; border: round #2a2f3a; background: #11151c; }
    DataTable > .datatable--header { background: #1e2530; color: #7dd3fc; text-style: bold; }
    DataTable > .datatable--cursor { background: #1e293b; color: white; }
    DataTable > .datatable--hover { background: #1a2230; }
    """
    BINDINGS = [
        ("q", "quit", "quit"),
        ("r", "refresh", "refresh"),
        ("c", "sort_cpu", "sort cpu"),
        ("m", "sort_mem", "sort mem"),
        ("n", "sort_name", "sort name"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            with Vertical(id="left"):
                yield CpuBox()
                yield MemBox()
                yield GpuBox()
                yield SensorBox()
            with Vertical(id="right"):
                yield DiskBox()
                yield NetBox()
                yield Static("", id="proc-head")
                yield DataTable(id="procs", zebra_stripes=True)
        yield Footer()

    def on_mount(self):
        t = self.query_one("#procs", DataTable)
        t.add_column("PID", width=8)
        t.add_column("NAME", width=24)
        t.add_column("CPU", width=20)
        t.add_column("MEM", width=20)
        t.cursor_type = "row"
        self._sort = "cpu"
        self._stop = threading.Event()
        self._wake = threading.Event()
        psutil.cpu_percent(interval=None, percpu=True)  # warm up, first read is 0
        stats.top_processes(1)  # warm up proc cache
        threading.Thread(target=self._poller, daemon=True).start()  # stats off UI thread

    def on_unmount(self):
        self._stop.set()
        self._wake.set()

    def _poller(self):
        # heavy lifting here, UI never blocks
        try:
            import pythoncom
            pythoncom.CoInitialize()  # WMI needs this per thread
        except Exception:
            pass
        while not self._stop.is_set():
            t0 = time.time()
            try:
                snap = stats.snapshot(sort_by=self._sort)
                self.call_from_thread(self._apply, snap)
            except Exception:
                pass
            # 1s cadence, wake early on manual refresh
            wait = max(0.0, 1.0 - (time.time() - t0))
            self._wake.wait(wait)
            self._wake.clear()

    def _apply(self, s):
        # cheap paints only, runs on UI thread
        for w in self.query(CpuBox):
            w.update_box(s["cpu"], s.get("cpu_name", "CPU"))
        for w in self.query(MemBox):
            w.update_box(s["mem"])
        for w in self.query(DiskBox):
            w.update_box(s["disks"])
        for w in self.query(NetBox):
            w.update_box(s["net"])
        for w in self.query(GpuBox):
            w.update_box(s["gpu_nv"], s["gpu_win"], s["npu"])
        for w in self.query(SensorBox):
            w.update_box(s["sensors"])
        self.query_one("#proc-head", Static).update(
            Text(f"  PROCS  sort:{self._sort}   [c]pu [m]em [n]ame", style="bold #7dd3fc")
        )
        t = self.query_one("#procs", DataTable)
        t.clear()
        for p in s["procs"]:
            cpu = max(0.0, min(100.0, p.get("cpu_percent") or 0))
            mem = max(0.0, min(100.0, p.get("memory_percent") or 0))
            cpu_cell = Text.assemble((f"{cpu:5.1f} ", f"bold {lvl(cpu)}"), hmini(cpu))
            mem_cell = Text.assemble((f"{mem:5.1f} ", f"bold {lvl(mem)}"), hmini(mem))
            name = str(p.get("name", "?"))[:24]
            t.add_row(
                Text(str(p.get("pid", "?")), style="dim"),
                Text(name, style="bold white" if cpu > 20 else "white"),
                cpu_cell,
                mem_cell,
            )

    def action_refresh(self):
        self._wake.set()  # skip the wait, poll now

    def action_sort_cpu(self):
        self._sort = "cpu"
        self._wake.set()

    def action_sort_mem(self):
        self._sort = "mem"
        self._wake.set()

    def action_sort_name(self):
        self._sort = "name"
        self._wake.set()


def main():
    # console entry point (glassbox command)
    Glassbox().run()


if __name__ == "__main__":
    main()
