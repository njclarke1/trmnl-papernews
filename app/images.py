"""Photos for e-ink and QR codes. Processed files are cached on the data volume, never in git."""
from __future__ import annotations

import hashlib
import io
import math
import os
import pathlib
import tempfile

import numpy as np
import qrcode
from PIL import Image, ImageOps

from . import config

SIZES = {"full": (558, 360), "wide": (1156, 820), "side": (470, 320)}


def _save_atomic(img: Image.Image, path: pathlib.Path) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    os.close(fd)
    img.save(tmp, format="PNG")
    os.replace(tmp, path)


def _dir(kind: str) -> pathlib.Path:
    d = pathlib.Path(config.DATA_DIR) / kind
    d.mkdir(parents=True, exist_ok=True)
    return d


def halftone(gray: np.ndarray, cell: int = 5, angle: float = 45, ss: int = 4) -> Image.Image:
    """AM screen: area-true dots, rotated screen, supersampled (the newsprint look)."""
    h, w = gray.shape
    big = np.asarray(Image.fromarray((gray * 255).astype("uint8")).resize((w * ss, h * ss), Image.BICUBIC), float) / 255
    yy, xx = np.mgrid[0:h * ss, 0:w * ss].astype(float)
    a = math.radians(angle)
    c = cell * ss
    u = (xx * math.cos(a) + yy * math.sin(a)) / c
    v = (-xx * math.sin(a) + yy * math.cos(a)) / c
    d = np.hypot(u - np.floor(u) - .5, v - np.floor(v) - .5)
    r = np.sqrt(np.clip(1 - big, 0, 1) / math.pi) * 1.04
    return Image.fromarray(np.where(d < r, 0, 255).astype("uint8")).resize((w, h), Image.LANCZOS)


def clean16(gray: np.ndarray) -> Image.Image:
    """Floyd–Steinberg to the Kindle's 16 greys."""
    im = Image.fromarray((gray * 255).astype("uint8")).convert("RGB")
    pal = Image.new("P", (1, 1))
    pal.putpalette(sum(([v * 17] * 3 for v in range(16)), []) + [0] * (768 - 48))
    return im.quantize(palette=pal, dither=Image.Dither.FLOYDSTEINBERG).convert("L")


def process(raw: bytes, size: tuple[int, int], mode: str | None = None, focus=(0.5, 0.4)) -> Image.Image:
    im = Image.open(io.BytesIO(raw)).convert("L")
    im = ImageOps.fit(im, size, Image.LANCZOS, centering=focus)
    im = ImageOps.autocontrast(im, cutoff=1)
    g = (np.asarray(im, float) / 255) ** 0.92
    return halftone(g) if (mode or config.IMAGE_MODE) == "halftone" else clean16(g)


def cached_photo(url: str, raw_fetch, size_key: str) -> str | None:
    """Return the public URL of a processed photo, fetching via raw_fetch(url)->bytes if needed."""
    if not url:
        return None
    size = SIZES[size_key]
    name = hashlib.sha1(f"{url}|{size}|{config.IMAGE_MODE}".encode()).hexdigest()[:20] + ".png"
    path = _dir("img") / name
    if not path.exists():
        try:
            _save_atomic(process(raw_fetch(url), size), path)
        except Exception:
            return None
    return f"{config.PUBLIC_BASE_URL}/img/{name}"


def qr_url(target: str) -> str | None:
    if not target:
        return None
    name = hashlib.sha1(target.encode()).hexdigest()[:20] + ".png"
    path = _dir("qr") / name
    if not path.exists():
        q = qrcode.QRCode(border=0, box_size=6, error_correction=qrcode.constants.ERROR_CORRECT_M)
        q.add_data(target)
        q.make(fit=True)
        _save_atomic(q.make_image(fill_color="black", back_color="white").convert("L"), path)
    return f"{config.PUBLIC_BASE_URL}/qr/{name}"
