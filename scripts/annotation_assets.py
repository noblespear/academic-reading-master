"""Bounded, verified image assets. Only decoded local image bytes are accepted."""
import base64
import binascii
import hashlib
import os
import struct
import zlib
from pathlib import Path

MAX_IMAGE_BYTES = 1024 * 1024
MAX_IMAGE_PIXELS = 16000000
MAX_IMAGE_DIMENSION = 32768


def verify_image(raw, media_type):
    """Check container structure, dimensions and integrity without optional PIL."""
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("image must be at most 1 MiB")
    width = height = 0
    if media_type == "image/png":
        if raw[:8] != b"\x89PNG\r\n\x1a\n":
            raise ValueError("PNG signature mismatch")
        offset, kinds, pixels = 8, [], bytearray()
        while offset + 12 <= len(raw):
            length = int.from_bytes(raw[offset:offset + 4], "big")
            kind = raw[offset + 4:offset + 8]
            end = offset + 12 + length
            if end > len(raw):
                raise ValueError("truncated PNG")
            chunk = raw[offset + 8:offset + 8 + length]
            if zlib.crc32(kind + chunk) & 0xffffffff != int.from_bytes(raw[end - 4:end], "big"):
                raise ValueError("invalid PNG checksum")
            if kind == b"IHDR":
                if kinds or length != 13:
                    raise ValueError("invalid PNG header")
                width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", chunk)
                if depth not in (1, 2, 4, 8, 16) or color not in (0, 2, 3, 4, 6) or compression or filtering or interlace not in (0, 1):
                    raise ValueError("unsupported PNG header")
                allowed_depths = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
                if depth not in allowed_depths[color]:
                    raise ValueError("invalid PNG bit depth")
            if kind == b"IDAT":
                pixels.extend(chunk)
            kinds.append(kind)
            offset = end
            if kind == b"IEND":
                break
        if offset != len(raw) or not kinds or kinds[0] != b"IHDR" or kinds[-1] != b"IEND" or not pixels or color == 3 and b"PLTE" not in kinds:
            raise ValueError("invalid PNG container")
        if width * height > MAX_IMAGE_PIXELS or max(width, height) > MAX_IMAGE_DIMENSION:
            raise ValueError("image dimensions exceed 16 million pixels")
        try:
            channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
            passes = [(0, 0, 1, 1)] if not interlace else [(0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2)]
            rows = []
            for left, top, xstep, ystep in passes:
                columns = max(0, (width - left + xstep - 1) // xstep)
                lines = max(0, (height - top + ystep - 1) // ystep)
                if columns and lines:
                    rows.extend([1 + (columns * channels * depth + 7) // 8] * lines)
            expected = sum(rows)
            decoder = zlib.decompressobj()
            decoded = decoder.decompress(bytes(pixels), expected + 1)
            if not decoder.eof or decoder.unconsumed_tail or decoder.unused_data or len(decoded) != expected:
                raise ValueError("invalid or oversized PNG pixel stream")
            position = 0
            for row in rows:
                if decoded[position] > 4:
                    raise ValueError("invalid PNG row filter")
                position += row
        except zlib.error as exc:
            raise ValueError("invalid PNG pixel stream") from exc
        extension = "png"
    elif media_type == "image/jpeg":
        if raw[:2] != b"\xff\xd8" or raw[-2:] != b"\xff\xd9":
            raise ValueError("JPEG signature mismatch or truncated image")
        offset = 2
        while offset < len(raw) - 2:
            if raw[offset] != 0xff:
                raise ValueError("invalid JPEG marker")
            while offset < len(raw) and raw[offset] == 0xff:
                offset += 1
            marker = raw[offset]
            offset += 1
            if marker in (0xd8, 0xd9) or 0xd0 <= marker <= 0xd7:
                continue
            if offset + 2 > len(raw):
                raise ValueError("truncated JPEG segment")
            length = int.from_bytes(raw[offset:offset + 2], "big")
            if length < 2 or offset + length > len(raw):
                raise ValueError("invalid JPEG segment")
            if marker in (0xc0, 0xc1, 0xc2):
                if length < 8:
                    raise ValueError("invalid JPEG frame")
                height, width = struct.unpack(">HH", raw[offset + 3:offset + 7])
            if marker == 0xda:
                if b"\xff\xd9" not in raw[offset + length:]:
                    raise ValueError("JPEG scan has no end")
                break
            offset += length
        extension = "jpg"
    elif media_type == "image/webp":
        if raw[:4] != b"RIFF" or raw[8:12] != b"WEBP" or len(raw) < 30 or int.from_bytes(raw[4:8], "little") + 8 != len(raw):
            raise ValueError("WebP container mismatch")
        offset, image_found = 12, False
        while offset + 8 <= len(raw):
            kind = raw[offset:offset + 4]
            length = int.from_bytes(raw[offset + 4:offset + 8], "little")
            chunk = raw[offset + 8:offset + 8 + length]
            if len(chunk) != length:
                raise ValueError("truncated WebP chunk")
            if kind == b"VP8X" and len(chunk) >= 10:
                width = int.from_bytes(chunk[4:7], "little") + 1
                height = int.from_bytes(chunk[7:10], "little") + 1
            elif kind == b"VP8 " and len(chunk) >= 10 and chunk[3:6] == b"\x9d\x01\x2a":
                width, height = struct.unpack("<HH", chunk[6:10])
                width, height, image_found = width & 0x3fff, height & 0x3fff, True
            elif kind == b"VP8L" and len(chunk) >= 5 and chunk[0] == 0x2f:
                packed = int.from_bytes(chunk[1:5], "little")
                width, height, image_found = (packed & 0x3fff) + 1, ((packed >> 14) & 0x3fff) + 1, True
            offset += 8 + length + (length % 2)
        if offset != len(raw) or not image_found:
            raise ValueError("invalid WebP pixel container")
        extension = "webp"
    else:
        raise ValueError("only PNG, JPEG and WebP uploads are supported")
    if not width or not height or width * height > MAX_IMAGE_PIXELS or max(width, height) > MAX_IMAGE_DIMENSION:
        raise ValueError("invalid or oversized image dimensions")
    # Where available, a real decoder additionally rejects corrupt image scans.
    try:
        from PIL import Image
    except ImportError:
        pass
    else:
        import io
        try:
            with Image.open(io.BytesIO(raw)) as image:
                image.verify()
            with Image.open(io.BytesIO(raw)) as image:
                image.load()
        except Exception as exc:
            raise ValueError("image decoder rejected image") from exc
    return extension


def save_upload(paper_dir, body):
    encoded = body.get("data_base64")
    if not isinstance(encoded, str) or len(encoded) > (MAX_IMAGE_BYTES + 2) // 3 * 4:
        raise ValueError("image must be at most 1 MiB")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("invalid base64 image") from exc
    media_type = body.get("media_type")
    extension = verify_image(raw, media_type)
    alt, caption = body.get("alt", ""), body.get("caption", "")
    if any(not isinstance(value, str) or len(value) > 10000 for value in (alt, caption)):
        raise ValueError("invalid image alt or caption")
    digest = hashlib.sha256(raw).hexdigest()
    root = Path(paper_dir).resolve()
    directory = (root / "assets" / "annotations").resolve()
    if not directory.is_relative_to(root):
        raise ValueError("image assets path outside paper")
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (digest + "." + extension)
    if not target.resolve().is_relative_to(root):
        raise ValueError("image target outside paper")
    if not target.exists():
        import uuid
        temporary = directory / (digest + "." + uuid.uuid4().hex + ".tmp")
        temporary.write_bytes(raw)
        os.replace(temporary, target)
    return {"id": "asset_" + digest[:24], "src": target.relative_to(root).as_posix(),
            "media_type": media_type, "alt": alt, "caption": caption, "sha256": digest}
