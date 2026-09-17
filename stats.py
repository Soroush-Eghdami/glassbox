"""Live system stats, one place."""

import re

import psutil

try:
    import pynvml
    _HAS_NVML = True
except ImportError:
    _HAS_NVML = False

_nvml_on = False


def _nvml_start():
    # init once, skip if no nvidia card
    global _nvml_on
    if not _HAS_NVML or _nvml_on:
        return _nvml_on
    try:
        pynvml.nvmlInit()
        _nvml_on = True
    except Exception:
        _nvml_on = False
    return _nvml_on


def _clean_name(name, limit=0):
    # full device name, just collapse whitespace + strip (R)/(TM) noise
    n = " ".join((name or "").replace("(R)", "").replace("(TM)", "").split())
    if limit and len(n) > limit:
        n = n[: max(0, limit - 1)].rstrip() + "…"
    return n or "GPU"


def cpu():
    # one call only, total = avg of cores
    cores = psutil.cpu_percent(interval=None, percpu=True)
    if not cores:
        return {"total": 0.0, "cores": [], "freq": psutil.cpu_freq(), "count": psutil.cpu_count(logical=True)}
    total = sum(cores) / len(cores)
    return {
        "total": max(0.0, min(100.0, total)),
        "cores": [max(0.0, min(100.0, c)) for c in cores],
        "freq": psutil.cpu_freq(),
        "count": len(cores),
    }


def memory():
    # ram + swap, plain numbers
    return {
        "virt": psutil.virtual_memory(),
        "swap": psutil.swap_memory(),
    }


def disks():
    # partitions + io counters
    parts = []
    for p in psutil.disk_partitions(all=False):
        try:
            parts.append((p.mountpoint, psutil.disk_usage(p.mountpoint)))
        except (PermissionError, OSError):
            continue
    try:
        io = psutil.disk_io_counters()
    except Exception:
        io = None
    return {"parts": parts, "io": io}


def network():
    # total + per-nic io + link state, for wifi/ethernet split
    try:
        total = psutil.net_io_counters()
    except Exception:
        total = None
    try:
        per_nic = psutil.net_io_counters(pernic=True)
    except Exception:
        per_nic = {}
    try:
        if_stats = psutil.net_if_stats()
    except Exception:
        if_stats = {}
    return {"total": total, "per_nic": per_nic or {}, "if_stats": if_stats or {}}


def sensors():
    # temps + fans + battery, may be empty on windows
    try:
        temps = psutil.sensors_temperatures()
    except Exception:
        temps = {}
    try:
        fans = psutil.sensors_fans()
    except Exception:
        fans = {}
    try:
        batt = psutil.sensors_battery()
    except Exception:
        batt = None
    return {"temps": temps or {}, "fans": fans or {}, "battery": batt}


def gpu():
    # nvidia only, empty list if none
    if not _nvml_start():
        return []
    cards = []
    try:
        n = pynvml.nvmlDeviceGetCount()
    except Exception:
        return []
    for i in range(n):
        try:
            h = pynvml.nvmlDeviceGetHandleByIndex(i)
            name = pynvml.nvmlDeviceGetName(h)
            if isinstance(name, bytes):
                name = name.decode("utf-8", "ignore")
            name = _clean_name(name)
            util = pynvml.nvmlDeviceGetUtilizationRates(h)
            mem = pynvml.nvmlDeviceGetMemoryInfo(h)
            temp = pynvml.nvmlDeviceGetTemperature(h, pynvml.NVML_TEMPERATURE_GPU)
            cards.append({
                "name": name, "tag": "gpu",
                "load": util.gpu,
                "mem_used": mem.used,
                "mem_total": mem.total,
                "temp": temp,
            })
        except Exception:
            continue
    return cards


_PROC_CACHE = {}
_PROC_NAMES = {}
_PDH = {"q": None, "eng": None, "ded": None, "sh": None, "ok": False}
_VC = None  # cached video controller names
_NPU_NAME = None
_DXGI = None  # cached dxgi adapter descs
_CPU_NAME = None
_LUID_RE = re.compile(r"luid_(0x[0-9a-fA-F]+)_(0x[0-9a-fA-F]+)_phys_\d+")
DGPU_MIN_DED = 1024 ** 3  # 1GB dedicated vram -> discrete card


def _pdh_ensure():
    # one persistent query, reused every tick
    if _PDH["q"] is not None:
        return True
    try:
        import win32pdh
        q = win32pdh.OpenQuery()
        _PDH["eng"] = win32pdh.AddCounter(q, r"\GPU Engine(*)\Utilization Percentage")
        _PDH["ded"] = win32pdh.AddCounter(q, r"\GPU Adapter Memory(*)\Dedicated Usage")
        _PDH["sh"] = win32pdh.AddCounter(q, r"\GPU Adapter Memory(*)\Shared Usage")
        win32pdh.CollectQueryData(q)  # first collect, values invalid
        _PDH["q"] = q
        return True
    except Exception:
        _PDH["q"] = None
        return False


def _pdh_read():
    # returns (eng_dict, ded_dict, sh_dict) or None
    try:
        import win32pdh
        if not _pdh_ensure():
            return None
        win32pdh.CollectQueryData(_PDH["q"])
        if not _PDH["ok"]:
            _PDH["ok"] = True
            return None  # second collect still settling
        eng = win32pdh.GetFormattedCounterArray(_PDH["eng"], win32pdh.PDH_FMT_DOUBLE)
        ded = win32pdh.GetFormattedCounterArray(_PDH["ded"], win32pdh.PDH_FMT_LARGE)
        sh = win32pdh.GetFormattedCounterArray(_PDH["sh"], win32pdh.PDH_FMT_LARGE)
        return eng, ded, sh
    except Exception:
        _PDH["q"] = None
        _PDH["ok"] = False
        return None


def _win_names():
    # gpu + npu names, once. _NPU_NAME stays None when no NPU hardware.
    global _VC, _NPU_NAME
    if _VC is not None:
        return
    _VC, _NPU_NAME = [], None
    try:
        import win32com.client
        w = win32com.client.GetObject("winmgmts:")
        for o in w.ExecQuery("SELECT Name, AdapterRAM FROM Win32_VideoController"):
            ram = o.AdapterRAM
            if ram is not None and ram < 0:
                ram += 2 ** 32  # signed overflow fix
            _VC.append({"name": _clean_name(o.Name), "ram": ram or 0})
        for o in w.ExecQuery("SELECT Name FROM Win32_PnPEntity"):
            nm = (o.Name or "").lower()
            if "input" in nm:
                continue  # skip "USB Input Device"
            if "ai boost" in nm or "npu" in nm or "xdna" in nm or "neural" in nm:
                _NPU_NAME = _clean_name(o.Name)
                break
    except Exception:
        if not _VC:
            _VC = [{"name": "GPU", "ram": 0}]


def _dxgi_adapters():
    # real descs + luids straight from the driver, once
    global _DXGI
    if _DXGI is not None:
        return _DXGI
    _DXGI = []
    try:
        import ctypes
        from ctypes import wintypes

        class _GUID(ctypes.Structure):
            _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD),
                        ("c", wintypes.WORD), ("d", wintypes.BYTE * 8)]

        class _LUID(ctypes.Structure):
            _fields_ = [("low", wintypes.DWORD), ("high", wintypes.LONG)]

        class _DESC(ctypes.Structure):
            _fields_ = [("name", wintypes.WCHAR * 128),
                        ("ven", wintypes.UINT), ("dev", wintypes.UINT),
                        ("sub", wintypes.UINT), ("rev", wintypes.UINT),
                        ("ded", ctypes.c_size_t), ("sys", ctypes.c_size_t),
                        ("shared", ctypes.c_size_t), ("luid", _LUID)]

        dxgi = ctypes.WinDLL("dxgi.dll")
        make = dxgi.CreateDXGIFactory1
        make.argtypes = [ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]
        make.restype = ctypes.HRESULT
        gid = _GUID(0x7B7166EC, 0x21C7, 0x44AE,
                    (wintypes.BYTE * 8)(0xB2, 0x1A, 0xC9, 0xAE, 0x32, 0x1A, 0xE3, 0x69))
        fac = ctypes.c_void_p()
        if make(ctypes.byref(gid), ctypes.byref(fac)) != 0:
            return _DXGI
        fvt = ctypes.cast(fac, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))
        enum = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p,
                                 wintypes.UINT, ctypes.POINTER(ctypes.c_void_p))(fvt.contents[7])
        free = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p)(fvt.contents[2])
        i = 0
        while True:
            ad = ctypes.c_void_p()
            if enum(fac.value, i, ctypes.byref(ad)) != 0:
                break
            avt = ctypes.cast(ad, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))
            get = ctypes.WINFUNCTYPE(wintypes.LONG, ctypes.c_void_p,
                                    ctypes.POINTER(_DESC))(avt.contents[8])  # idx 8, probed
            d = _DESC()
            if get(ad.value, ctypes.byref(d)) == 0:
                _DXGI.append({"low": d.luid.low, "high": d.luid.high,
                              "name": _clean_name(d.name), "ded": d.ded, "shared": d.shared})
            ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p)(avt.contents[2])(ad.value)
            i += 1
        free(fac.value)
    except Exception:
        pass  # keep whatever adapters we got
    return _DXGI


def cpu_name():
    # brand string, once; plain fallback off windows
    global _CPU_NAME
    if _CPU_NAME is not None:
        return _CPU_NAME
    _CPU_NAME = "CPU"
    try:
        import win32com.client
        w = win32com.client.GetObject("winmgmts:")
        for o in w.ExecQuery("SELECT Name FROM Win32_Processor"):
            _CPU_NAME = " ".join((o.Name or "").replace("(R)", "").replace("(TM)", "").split())
            break
    except Exception:
        pass
    return _CPU_NAME


def _tag_for(ded):
    # only two buckets: integrated vs everything else ("gpu")
    return "gpu" if (ded or 0) >= DGPU_MIN_DED else "igpu"


def _fallback_cards():
    # libraries failed (PDH down) -> still show adapters from DXGI, then WMI
    _win_names()
    cards = []
    for a in _dxgi_adapters():
        if "basic render" in (a["name"] or "").lower():
            continue
        cards.append({
            "name": a["name"], "tag": _tag_for(a["ded"]),
            "load": 0.0, "mem_used": 0,
            "mem_total": (a["ded"] or 0) + (a["shared"] or 0),
            "temp": None,
        })
    if not cards:
        for i, v in enumerate(_VC or []):
            cards.append({
                "name": v["name"], "tag": "gpu" if i == 0 and len(_VC or []) == 1 else ("igpu" if i == 0 else "gpu"),
                "load": 0.0, "mem_used": 0,
                "mem_total": v.get("ram", 0), "temp": None,
            })
    return _merge_same_name(cards)


def _norm(name):
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


_GENERIC_TOKS = {"nvidia", "geforce", "amd", "radeon", "intel", "arc",
                 "laptop", "mobile", "notebook", "gpu", "graphics",
                 "max", "design", "dynamic", "boost"}


def _sig(name):
    # distinctive tokens only, so "RTX 4060" != "RTX 3060" but identical
    # names from two sources (NVML vs DXGI vs WMI) match each other
    toks = set(_norm(name).split()) - _GENERIC_TOKS
    if toks:
        return frozenset(toks)
    return frozenset(_norm(name).split())


def _merge_same_name(cards):
    # two readings can resolve to one adapter (e.g. an unmatched PDH luid
    # falling back to the WMI name) -> fold them into a single card
    merged, order = {}, []
    for c in cards:
        key = _sig(c.get("name"))
        m = merged.get(key)
        if m is None:
            merged[key] = dict(c)
            order.append(key)
            continue
        m["load"] = max(m.get("load") or 0, c.get("load") or 0)
        m["mem_used"] = max(m.get("mem_used") or 0, c.get("mem_used") or 0)
        if (c.get("mem_total") or 0) > (m.get("mem_total") or 0):
            m["mem_total"] = c.get("mem_total")
        if len(c.get("name") or "") > len(m.get("name") or ""):
            m["name"] = c.get("name")
        if m.get("temp") is None:
            m["temp"] = c.get("temp")
    return [merged[k] for k in order]


def _win_stats():
    # library-first, automatic fallback: PDH live values, else DXGI/WMI list
    _win_names()
    _dxgi_adapters()  # warm the library cache even if PDH fails
    data = _pdh_read()
    npu_name = _NPU_NAME  # None when no NPU hardware
    if data is None:
        cards = _fallback_cards()
        npu = [{"name": npu_name, "load": 0.0}] if npu_name else []
        return cards, npu
    eng, ded, sh = data
    gload, mem, nload = {}, {}, 0.0
    for k, v in eng.items():
        if "Neural" in k:
            nload += v  # npu lives here on win11
            continue
        m = _LUID_RE.search(k)
        key = (int(m.group(1), 16), int(m.group(2), 16)) if m else (-1, -1)
        gload[key] = gload.get(key, 0.0) + v
    for src in (ded, sh):
        for k, v in src.items():
            m = _LUID_RE.search(k)
            key = (int(m.group(1), 16), int(m.group(2), 16)) if m else (-1, -1)
            mem[key] = mem.get(key, 0) + v
    by_luid = {(a["high"], a["low"]): a for a in _dxgi_adapters()}
    cards = []
    for (hi, lo), load in gload.items():
        a = by_luid.get((hi, lo))
        if a:
            if "basic render" in a["name"].lower():
                continue  # software adapter, noise
            cards.append({
                "name": a["name"], "tag": _tag_for(a["ded"]),
                "load": max(0.0, min(100.0, load)),
                "mem_used": mem.get((hi, lo), 0),
                "mem_total": a["ded"] + a["shared"],
                "temp": None,
            })
        elif _VC:
            cards.append({  # dxgi missed it, wmi fallback
                "name": _VC[0]["name"], "tag": "gpu",
                "load": max(0.0, min(100.0, load)),
                "mem_used": mem.get((hi, lo), 0),
                "mem_total": _VC[0]["ram"],
                "temp": None,
            })
    if not cards:
        cards = _fallback_cards()
    cards = _merge_same_name(cards)
    # stable order: igpu first, then gpu
    cards.sort(key=lambda c: (0 if c.get("tag") == "igpu" else 1, c.get("name") or ""))
    npu = [{"name": npu_name, "load": max(0.0, min(100.0, nload))}] if npu_name else []
    return cards, npu


def win_gpu():
    # any-vendor gpu via windows counters, [] if none
    g, _ = _win_stats()
    return g


def npu():
    # neural engines live under gpu counters on win11
    _, n = _win_stats()
    return n


def _dedupe_gpus(nv, win):
    # NVML is authoritative; drop the windows twin of the same adapter
    # (matched by model signature, so a real second card is never dropped).
    sigs = {_sig(c.get("name")) for c in nv or []}
    out = [c for c in win or [] if _sig(c.get("name")) not in sigs]
    return _merge_same_name(out)


def snapshot(sort_by="cpu"):
    # everything in one call, for the worker thread
    g, n = _win_stats()
    try:
        nv = gpu()
    except Exception:
        nv = []  # library failed -> windows fallback below covers it
    g = _dedupe_gpus(nv, g)
    return {
        "cpu": cpu(),
        "cpu_name": cpu_name(),
        "mem": memory(),
        "disks": disks(),
        "net": network(),
        "sensors": sensors(),
        "gpu_nv": nv,
        "gpu_win": g,
        "npu": n,
        "procs": top_processes(30, sort_by=sort_by),
    }


def _ncpu():
    # cached core count for norm
    n = psutil.cpu_count(logical=True) or 1
    return n


def top_processes(n=10, sort_by="cpu"):
    # reuse procs so cpu% is real, norm to 0-100
    ncpu = _ncpu()
    out = []
    pids = psutil.pids()
    for pid in pids:
        if pid == 0:
            continue  # skip idle, clutters top
        p = _PROC_CACHE.get(pid)
        if p is None:
            try:
                p = psutil.Process(pid)
                p.cpu_percent()  # warm up, next tick is real
                _PROC_CACHE[pid] = p
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        try:
            with p.oneshot():  # one syscall batch, not three
                raw = p.cpu_percent()  # 0 on first tick by design
                mem = p.memory_percent()
                name = _PROC_NAMES.get(pid)
                if name is None:
                    name = p.name()
                    _PROC_NAMES[pid] = name
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            _PROC_CACHE.pop(pid, None)
            _PROC_NAMES.pop(pid, None)
            continue
        # raw can exceed 100 on multicore, scale down
        cpu = max(0.0, min(100.0, raw / ncpu))
        out.append({"pid": pid, "name": name, "cpu_percent": cpu, "memory_percent": mem})
    # drop dead pids from cache
    alive = set(pids)
    for pid in list(_PROC_CACHE):
        if pid not in alive:
            _PROC_CACHE.pop(pid, None)
            _PROC_NAMES.pop(pid, None)
    key = "cpu_percent" if sort_by == "cpu" else "memory_percent"
    if sort_by == "name":
        out.sort(key=lambda d: (d.get("name") or "").lower())
    else:
        out.sort(key=lambda d: d.get(key) or 0, reverse=True)
    return out[:n]
