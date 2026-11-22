import io
import struct
import zlib

import pytest
from PIL import Image

from app.services.images import (
    MAX_UPLOAD_BYTES,
    ImageError,
    process_avatar,
    process_portfolio,
    read_limited,
)


def _encode(img: Image.Image, fmt: str, **kw) -> bytes:
    buf = io.BytesIO()
    img.save(buf, fmt, **kw)
    return buf.getvalue()


def _jpeg(size=(300, 200), **kw) -> bytes:
    return _encode(Image.new("RGB", size, (200, 30, 30)), "JPEG", **kw)


def _open(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    img.load()
    return img


def _code(data: bytes, fn=process_portfolio) -> str:
    with pytest.raises(ImageError) as exc:
        fn(data)
    return exc.value.code


def test_text_file_named_jpg_is_rejected():
    assert _code(b"hello, I am not an image") == "type"


def test_gif_is_rejected():
    assert _code(_encode(Image.new("P", (10, 10)), "GIF")) == "type"


def test_eleven_megabytes_is_rejected_while_reading():
    stream = io.BytesIO(b"\0" * (MAX_UPLOAD_BYTES + 1))
    with pytest.raises(ImageError) as exc:
        read_limited(stream)
    assert exc.value.code == "size"


def test_read_limited_accepts_exactly_the_limit():
    assert len(read_limited(io.BytesIO(b"\0" * MAX_UPLOAD_BYTES))) == MAX_UPLOAD_BYTES


def _chunk(tag: bytes, body: bytes) -> bytes:
    crc = zlib.crc32(tag + body) & 0xFFFFFFFF
    return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", crc)


def test_pixel_bomb_is_rejected_before_decoding():
    ihdr = struct.pack(">IIBBBBB", 20000, 20000, 8, 2, 0, 0, 0)
    data = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(b"\0"))
        + _chunk(b"IEND", b"")
    )
    assert _code(data) == "pixels"
    assert _code(data, process_avatar) == "pixels"


def test_truncated_jpeg_is_unreadable():
    assert _code(_jpeg((800, 600))[:200]) == "unreadable"


def test_corrupt_body_with_valid_magic_is_unreadable():
    assert _code(b"\x89PNG\r\n\x1a\n" + b"garbage" * 20) == "unreadable"


def test_gps_and_exif_are_removed():
    exif = Image.Exif()
    exif[0x010F] = "SecretCam"
    exif[0x8825] = {1: "N", 2: (38.0, 33.0, 0.0), 3: "E", 4: (68.0, 46.0, 0.0)}
    data = _jpeg((300, 200), exif=exif.tobytes())
    assert len(_open(data).getexif()) > 0  # sanity: the input has EXIF
    full, thumb = process_portfolio(data)
    for out in (full, thumb, process_avatar(data)):
        img = _open(out.data)
        assert img.format == "WEBP"
        assert len(img.getexif()) == 0
        for key in ("exif", "icc_profile", "xmp"):
            assert key not in img.info
        assert b"SecretCam" not in out.data


def test_exif_orientation_is_applied():
    exif = Image.Exif()
    exif[0x0112] = 6
    full, _ = process_portfolio(_jpeg((300, 100), exif=exif.tobytes()))
    assert (full.width, full.height) == (100, 300)
    assert _open(full.data).size == (100, 300)


def test_portfolio_sizes():
    big = _encode(Image.new("RGB", (4000, 3000), (10, 120, 200)), "JPEG")
    full, thumb = process_portfolio(big)
    assert (full.width, full.height) == (1600, 1200)
    assert (thumb.width, thumb.height) == (400, 300)
    assert _open(full.data).format == "WEBP"
    assert _open(thumb.data).size == (400, 300)

    small = _encode(Image.new("RGB", (800, 600)), "PNG")
    full, thumb = process_portfolio(small)
    assert (full.width, full.height) == (800, 600)
    assert (thumb.width, thumb.height) == (400, 300)


def test_portrait_long_side_is_limited():
    full, thumb = process_portfolio(_jpeg((1000, 3000)))
    assert (full.width, full.height) == (533, 1600)
    assert thumb.height == 400


def test_avatar_is_square_400():
    out = process_avatar(_jpeg((1000, 500)))
    assert (out.width, out.height) == (400, 400)
    img = _open(out.data)
    assert img.format == "WEBP"
    assert img.size == (400, 400)


def test_png_with_alpha_palette_and_cmyk_jpeg_are_processed():
    rgba = Image.new("RGBA", (50, 50), (255, 0, 0, 0))  # fully transparent
    pal = Image.new("RGB", (50, 50), (0, 0, 255)).convert("P")
    pal_t = Image.new("P", (50, 50), 0)
    pal_t.info["transparency"] = 0
    sixteen = Image.new("I;16", (50, 50), 40000)
    cmyk = Image.new("CMYK", (60, 40), (0, 255, 255, 0))
    la = Image.new("LA", (50, 50), (100, 0))
    samples = [
        _encode(rgba, "PNG"),
        _encode(pal, "PNG"),
        _encode(pal_t, "PNG", transparency=0),
        _encode(sixteen, "PNG"),
        _encode(cmyk, "JPEG"),
        _encode(la, "PNG"),
    ]
    for data in samples:
        full, _thumb = process_portfolio(data)
        img = _open(full.data)
        assert img.mode == "RGB"
        assert process_avatar(data).width == 400
    # transparent pixels are composited on white
    px = _open(process_portfolio(samples[0])[0].data).getpixel((10, 10))
    assert min(px) > 240
    # CMYK red is visibly red, not black
    px = _open(process_portfolio(samples[4])[0].data).getpixel((10, 10))
    assert px[0] > 150 and px[1] < 100


def test_animated_webp_uses_first_frame():
    frames = [Image.new("RGB", (60, 40), c) for c in ((255, 0, 0), (0, 255, 0))]
    data = _encode(frames[0], "WEBP", save_all=True, append_images=frames[1:])
    full, _ = process_portfolio(data)
    assert (full.width, full.height) == (60, 40)
    assert _open(full.data).getpixel((5, 5))[0] > 200
