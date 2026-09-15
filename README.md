# ◈ glassbox

A live system monitor for your terminal. CPU, memory, disks, network, GPU, NPU, sensors, and top processes — refreshing every second, styled like `btop`.

![glassbox screenshot](screenshot.svg)

## Features

- **CPU** — total + per-core `#` bars, clock speed, chip name in the panel corner
- **MEM / DISK** — btop-style bars with used/total on their own dim line
- **NET** — live down/up rates
- **GPU** — NVIDIA via NVML, any-vendor (Intel Arc, AMD, …) via Windows counters, one row per adapter tagged `igpu` / `dgpu`
- **NPU** — neural-engine load (e.g. Intel AI Boost) from `engtype_Neural` counters
- **SENSORS** — battery, temps, fans, uptime fallback on Windows
- **PROCS** — sortable top-30 table with mini bars (`c` / `m` / `n`)
- **Smooth** — all stat gathering runs on a background thread, the UI never freezes

## Stack

| Piece | Role |
|---|---|
| `psutil` | CPU, RAM, disks, net, temps, processes |
| `pywin32` (PDH + WMI) | iGPU/NPU load, VRAM, device names on Windows |
| `ctypes` + DXGI | per-adapter luids, exact names, igpu/dgpu split (stdlib, no dep) |
| `nvidia-ml-py` | NVIDIA load, VRAM, temp |
| `textual` | app shell, layout, keys, 1s poll loop |
| `rich` | panels, tables, colored `#` bars |
| `threading` | collector thread + `call_from_thread` paints |

## Quickstart

```powershell
# local dev, venv already in the repo — use it, not global python
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe -m glassbox.app
```

Or install it as a real command (any fresh venv):

```powershell
pip install -e .
glassbox
```

## Releases

No PyPI — grab a binary from GitHub Releases:

- `glassbox-win-x64.exe` — Windows, double-click and go
- `glassbox-linux-x64.tar.gz` — Linux, unpack and run `./glassbox-linux-x64`

Both are built by `.github/workflows/release.yml` (PyInstaller, one per OS).
Push a `v*` tag to ship. Or build the exe yourself:
`venv\Scripts\pyinstaller.exe --onefile --console --name glassbox-win-x64 glassbox/app.py`.

## Keys

| Key | Action |
|---|---|
| `q` | quit |
| `r` | refresh now |
| `c` / `m` / `n` | sort processes by CPU / MEM / name |

## Layout

```
glassbox/           the package (app.py shell, stats.py collectors)
scripts/shot.py     re-captures screenshot.svg (venv python scripts/shot.py)
pyproject.toml      package metadata, deps, `glassbox` console entry
requirements.txt    pinned dev deps for the local venv
screenshot.svg      real capture of the app
.github/workflows/  release CI: windows .exe + linux .tar.gz
```

## Notes

- First tick shows zeros — `psutil` and PDH need one warmup sample by design.
- Process CPU is normalized to 0–100% across all cores (no 600% ghosts).
- No NVIDIA card? That path quietly returns `[]`. No NPU? Same deal.
- Windows-only bits (`win_gpu`, `npu`, `cpu_name`) degrade to empty lists / `"CPU"` anywhere else.
- `igpu` vs `dgpu`: matched per-adapter via DXGI luids; ≥1GB dedicated VRAM counts as discrete. Software adapters (Basic Render Driver) are skipped.
