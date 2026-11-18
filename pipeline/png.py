"""Minimal PNG structure checks and an RGB writer (standard library only).

inspect_png() verifies the container: signature, IHDR first, every chunk CRC,
contiguous IDAT, a complete zlib stream of exactly the expected size, valid
per-row filter bytes, IEND last and no trailing data. It does not reconstruct
pixels (PNG unfiltering in pure Python is too slow for 720p sequences); pixel
level checks are done through FFmpeg elsewhere.
"""
import struct
import zlib

SIGNATURE = b"\x89PNG\r\n\x1a\n"
_CHANNELS = {0: 1, 2: 3, 4: 2, 6: 4}
_COLOR_CHUNKS = (b"sRGB", b"gAMA", b"cHRM", b"iCCP", b"cICP")


class PNGError(Exception):
    pass


def inspect_png(data):
    """Return a dict describing a structurally valid PNG, or raise PNGError."""
    if not data.startswith(SIGNATURE):
        raise PNGError("missing PNG signature")
    offset = len(SIGNATURE)
    header = None
    idat = []
    idat_closed = False
    color = {}
    seen_iend = False
    while offset < len(data):
        if len(data) - offset < 12:
            raise PNGError("truncated chunk header")
        length, kind = struct.unpack(">I4s", data[offset:offset + 8])
        end = offset + 8 + length
        if end + 4 > len(data):
            raise PNGError(f"truncated {kind!r} chunk")
        body = data[offset + 8:end]
        (crc,) = struct.unpack(">I", data[end:end + 4])
        if zlib.crc32(kind + body) & 0xFFFFFFFF != crc:
            raise PNGError(f"CRC mismatch in {kind!r} chunk")
        offset = end + 4
        if header is None:
            if kind != b"IHDR" or length != 13:
                raise PNGError("first chunk is not a valid IHDR")
            width, height, depth, ctype, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", body)
            if ctype not in _CHANNELS or depth not in (8, 16):
                raise PNGError(f"unsupported color type {ctype} / bit depth {depth}")
            if compression or filtering:
                raise PNGError("unsupported compression/filter method")
            if interlace:
                raise PNGError("interlaced PNG is not supported")
            header = {"width": width, "height": height, "bit_depth": depth, "color_type": ctype}
            continue
        if kind == b"IDAT":
            if idat_closed:
                raise PNGError("IDAT chunks are not contiguous")
            idat.append(body)
        elif idat:
            idat_closed = True
        if kind in _COLOR_CHUNKS:
            color[kind.decode("ascii")] = _describe_color_chunk(kind, body)
        if kind == b"IEND":
            seen_iend = True
            break
    if header is None:
        raise PNGError("no IHDR chunk")
    if not seen_iend:
        raise PNGError("missing IEND chunk")
    if offset != len(data):
        raise PNGError("data after IEND")
    if not idat:
        raise PNGError("no image data")

    channels = _CHANNELS[header["color_type"]]
    stride = header["width"] * channels * header["bit_depth"] // 8 + 1
    expected = stride * header["height"]
    stream = zlib.decompressobj()
    try:
        raw = stream.decompress(b"".join(idat), expected + 1)
    except zlib.error as error:
        raise PNGError(f"corrupt image data: {error}") from None
    if not stream.eof or stream.unconsumed_tail or stream.unused_data or len(raw) != expected:
        raise PNGError(f"image data is {len(raw)} bytes or incomplete, expected {expected}")
    for row in range(header["height"]):
        if raw[row * stride] > 4:
            raise PNGError(f"invalid filter byte on row {row}")
    header["channels"] = channels
    header["color_chunks"] = color
    return header


def _describe_color_chunk(kind, body):
    if kind == b"sRGB" and len(body) == 1:
        return {"rendering_intent": body[0]}
    if kind == b"gAMA" and len(body) == 4:
        return {"gamma": struct.unpack(">I", body)[0] / 100000}
    if kind == b"cHRM" and len(body) == 32:
        return {"chromaticities": list(struct.unpack(">8I", body))}
    if kind == b"cICP" and len(body) == 4:
        return {"primaries": body[0], "transfer": body[1], "matrix": body[2], "full_range": body[3]}
    if kind == b"iCCP":
        return {"profile_name": body.split(b"\0", 1)[0].decode("latin-1")}
    return {"bytes": len(body)}


CICP_BT709_RGB_FULL = bytes([1, 1, 0, 1])  # BT.709 primaries, BT.709 transfer, RGB, full range


def redeclare_color(data, remove=(b"sRGB", b"gAMA"), cicp=CICP_BT709_RGB_FULL):
    """Replace a renderer's color tags without touching its samples.

    Drops the `remove` chunks and any existing cICP, then inserts one cICP chunk
    right after IHDR. Every other chunk, including all image data, is copied
    byte for byte. This corrects metadata only: use it solely when the renderer
    is known to write tags that do not describe its samples, and the samples
    have been verified to already match the declared encoding.
    """
    if not data.startswith(SIGNATURE):
        raise PNGError("missing PNG signature")
    if len(cicp) != 4:
        raise ValueError("cICP must be 4 bytes")
    out, offset, seen_header = [SIGNATURE], len(SIGNATURE), False
    while offset < len(data):
        if len(data) - offset < 12:
            raise PNGError("truncated chunk header")
        length, kind = struct.unpack(">I4s", data[offset:offset + 8])
        end = offset + 12 + length
        if end > len(data):
            raise PNGError(f"truncated {kind!r} chunk")
        chunk, offset = data[offset:end], end
        if not seen_header:
            if kind != b"IHDR":
                raise PNGError("first chunk is not IHDR")
            out += [chunk, _chunk(b"cICP", bytes(cicp))]
            seen_header = True
        elif kind not in remove and kind != b"cICP":
            out.append(chunk)
    return b"".join(out)


def _chunk(kind, body):
    return (struct.pack(">I", len(body)) + kind + body
            + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))


def encode_rgb8(width, height, rows):
    """Encode 8-bit RGB rows (each exactly width*3 bytes) as a PNG byte string."""
    rows = list(rows)
    if len(rows) != height or any(len(row) != width * 3 for row in rows):
        raise ValueError("rows do not match width/height")
    raw = b"".join(b"\0" + bytes(row) for row in rows)
    return (SIGNATURE
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(raw, 6))
            + _chunk(b"IEND", b""))
