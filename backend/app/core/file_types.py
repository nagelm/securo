"""What a stored file really is, from its bytes rather than from the uploader's claim."""
from urllib.parse import quote

OCTET_STREAM = "application/octet-stream"

# The only types a browser may be handed as themselves. Everything else goes
# out as opaque bytes, so uploaded HTML or SVG can never run in the app's origin.
_INLINE_TYPES = frozenset(
    {"image/jpeg", "image/png", "image/gif", "image/webp", "image/heic", "application/pdf"}
)
_HEIC_BRANDS = (b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1")


def sniff_content_type(data: bytes) -> str:
    """The file's type by signature, or application/octet-stream when unknown."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:8] == b"ftyp" and data[8:12] in _HEIC_BRANDS:
        return "image/heic"
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    return OCTET_STREAM


def download_response_args(content_type: str | None, filename: str) -> dict:
    """media_type and headers for serving an uploaded file safely."""
    inline = content_type in _INLINE_TYPES
    ascii_name = filename.encode("ascii", "replace").decode().replace('"', "_").replace("\\", "_")
    disposition = (
        f"{'inline' if inline else 'attachment'}; filename=\"{ascii_name}\"; "
        f"filename*=UTF-8''{quote(filename)}"
    )
    return {
        "media_type": content_type if inline else OCTET_STREAM,
        "headers": {
            "Content-Disposition": disposition,
            "X-Content-Type-Options": "nosniff",
        },
    }
