"""System Monitor -- live CPU, RAM, temperature and disk on DisplayPad buttons."""
import hashlib
import os
import threading

try:
    import psutil
except ImportError:
    psutil = None

from PIL import Image, ImageDraw

try:
    from PIL import ImageFont
    _FONT_B = ImageFont.truetype("/usr/share/fonts/google-noto/NotoSans-Bold.ttf", 18)
    _FONT_M = ImageFont.truetype("/usr/share/fonts/google-noto/NotoSans-Bold.ttf", 12)
    _FONT_S = ImageFont.truetype("/usr/share/fonts/google-noto/NotoSans-Regular.ttf", 9)
except Exception:
    from PIL import ImageFont
    _FONT_B = _FONT_M = _FONT_S = ImageFont.load_default()

# Colors
_BG     = (16, 16, 36)
_BAR_BG = (30, 30, 50)
_CYAN   = (14, 165, 233)
_GREEN  = (34, 197, 94)
_AMBER  = (245, 158, 11)
_RED    = (239, 68, 68)
_WHITE  = (220, 220, 240)
_GRAY   = (90, 90, 136)


def _color_for_pct(pct):
    """Green < 50, Amber < 80, Red >= 80."""
    if pct < 50:
        return _GREEN
    if pct < 80:
        return _AMBER
    return _RED


def _color_for_temp(temp):
    if temp < 50:
        return _GREEN
    if temp < 75:
        return _AMBER
    return _RED


def _centered(draw, y, text, font, fill):
    tw = draw.textlength(text, font=font)
    draw.text(((102 - tw) / 2, y), text, fill=fill, font=font)


def _draw_bar(draw, y, pct, color):
    """Draw a horizontal progress bar."""
    draw.rounded_rectangle([11, y, 91, y + 10], radius=3, fill=_BAR_BG)
    w = max(1, int(80 * pct / 100))
    if w > 2:
        draw.rounded_rectangle([11, y, 11 + w, y + 10], radius=3, fill=color)


def _render_cpu(pct):
    img = Image.new("RGB", (102, 102), _BG)
    draw = ImageDraw.Draw(img)
    _centered(draw, 8, "CPU", _FONT_M, _CYAN)
    color = _color_for_pct(pct)
    _centered(draw, 30, f"{pct:.0f}%", _FONT_B, color)
    _draw_bar(draw, 60, pct, color)
    # Per-core mini text
    if psutil:
        cores = psutil.cpu_percent(percpu=True, interval=0)
        if cores:
            n = len(cores)
            txt = " ".join(f"{int(c)}" for c in cores[:8])  # max 8 cores shown
            _centered(draw, 78, txt, _FONT_S, _GRAY)
            if n > 8:
                txt2 = " ".join(f"{int(c)}" for c in cores[8:16])
                _centered(draw, 90, txt2, _FONT_S, _GRAY)
    return img


def _render_ram(pct, used_gb, total_gb):
    img = Image.new("RGB", (102, 102), _BG)
    draw = ImageDraw.Draw(img)
    _centered(draw, 8, "RAM", _FONT_M, _CYAN)
    color = _color_for_pct(pct)
    _centered(draw, 30, f"{pct:.0f}%", _FONT_B, color)
    _draw_bar(draw, 60, pct, color)
    _centered(draw, 78, f"{used_gb:.1f} / {total_gb:.1f} GB", _FONT_S, _GRAY)
    return img


def _render_temp(label, temp_c):
    img = Image.new("RGB", (102, 102), _BG)
    draw = ImageDraw.Draw(img)
    _centered(draw, 8, label.upper()[:8], _FONT_M, _CYAN)
    color = _color_for_temp(temp_c)
    _centered(draw, 32, f"{temp_c:.0f}\u00b0C", _FONT_B, color)
    # Temperature arc visualization
    pct = min(100, max(0, (temp_c / 100) * 100))
    _draw_bar(draw, 64, pct, color)
    return img


def _render_disk(label, pct, free_gb):
    img = Image.new("RGB", (102, 102), _BG)
    draw = ImageDraw.Draw(img)
    _centered(draw, 8, label.upper()[:8], _FONT_M, _CYAN)
    color = _color_for_pct(pct)
    _centered(draw, 30, f"{pct:.0f}%", _FONT_B, color)
    _draw_bar(draw, 60, pct, color)
    _centered(draw, 78, f"{free_gb:.0f} GB free", _FONT_S, _GRAY)
    return img


def _get_cpu_temp():
    """Get CPU temperature from k10temp, coretemp, or first available."""
    if not psutil:
        return None, "CPU"
    temps = psutil.sensors_temperatures()
    # Prefer k10temp (AMD) or coretemp (Intel)
    for name in ("k10temp", "coretemp"):
        if name in temps:
            for e in temps[name]:
                if e.current > 0:
                    lbl = "CPU" if not e.label else e.label
                    return e.current, lbl
    # Fallback: first sensor with reading
    for name, entries in temps.items():
        for e in entries:
            if e.current > 0:
                lbl = e.label if e.label else name
                return e.current, lbl
    return None, "CPU"


def _get_gpu_temp():
    """Get GPU temperature from amdgpu or nvidia."""
    if not psutil:
        return None, "GPU"
    temps = psutil.sensors_temperatures()
    for name in ("amdgpu", "nvidia", "nouveau", "radeon"):
        if name in temps:
            for e in temps[name]:
                if e.current > 0:
                    return e.current, "GPU"
    return None, "GPU"


class Plugin:
    panel_id = "system_monitor"
    panel_label = "System Monitor"

    def __init__(self, ctx):
        self.ctx = ctx
        self._stop = threading.Event()
        self._hashes = {}  # key_index -> md5

        ctx.register_translations({
            "en": {
                "mon_cpu":  "Monitor: CPU",
                "mon_ram":  "Monitor: RAM",
                "mon_temp": "Monitor: CPU Temp",
                "mon_gpu":  "Monitor: GPU Temp",
                "mon_disk": "Monitor: Disk",
            },
            "de": {
                "mon_cpu":  "Monitor: CPU",
                "mon_ram":  "Monitor: RAM",
                "mon_temp": "Monitor: CPU Temp",
                "mon_gpu":  "Monitor: GPU Temp",
                "mon_disk": "Monitor: Festplatte",
            }
        })

        ctx.register_action_type("mon_cpu", ctx.T("mon_cpu"), lambda v: None)
        ctx.register_action_type("mon_ram", ctx.T("mon_ram"), lambda v: None)
        ctx.register_action_type("mon_temp", ctx.T("mon_temp"), lambda v: None)
        ctx.register_action_type("mon_gpu", ctx.T("mon_gpu"), lambda v: None)
        ctx.register_action_type("mon_disk", ctx.T("mon_disk"), lambda v: None)

    def create_panel(self, parent):
        # No panel needed — pure DisplayPad widget
        return None

    def start(self):
        if not psutil:
            print("[system_monitor] psutil not installed, plugin disabled")
            return
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        # Prime CPU percent (first call always returns 0)
        psutil.cpu_percent(interval=0.5)
        while not self._stop.is_set():
            self._update()
            self._stop.wait(2)

    def _update(self):
        try:
            from shared.config import _load_displaypad_actions, CONFIG_DIR
        except ImportError:
            return

        actions = _load_displaypad_actions()
        dp = self.ctx.get_displaypad()

        for i, act in enumerate(actions):
            atype = act.get("type", "")
            img = None

            if atype == "mon_cpu":
                pct = psutil.cpu_percent(interval=0)
                img = _render_cpu(pct)

            elif atype == "mon_ram":
                mem = psutil.virtual_memory()
                img = _render_ram(mem.percent,
                                  mem.used / (1024**3),
                                  mem.total / (1024**3))

            elif atype == "mon_temp":
                temp, label = _get_cpu_temp()
                if temp is not None:
                    img = _render_temp(label, temp)

            elif atype == "mon_gpu":
                temp, label = _get_gpu_temp()
                if temp is not None:
                    img = _render_temp(label, temp)

            elif atype == "mon_disk":
                path = act.get("action", "/") or "/"
                try:
                    usage = psutil.disk_usage(path)
                    lbl = "DISK" if path == "/" else os.path.basename(path)
                    img = _render_disk(lbl, usage.percent,
                                       usage.free / (1024**3))
                except Exception:
                    pass

            if img is None:
                continue

            # Only push if image changed
            raw = img.tobytes()
            h = hashlib.md5(raw).hexdigest()
            if self._hashes.get(i) == h:
                continue
            self._hashes[i] = h

            # Save to disk so DisplayPad upload worker includes it
            img_path = os.path.join(CONFIG_DIR, f"dp_mon_{i}.png")
            img.save(img_path)
            if dp:
                dp._images[str(i)] = img_path
                if hasattr(dp, "_page_images") and 0 in dp._page_images:
                    dp._page_images[0][str(i)] = img_path

            self.ctx.push_displaypad_image(i, img)
