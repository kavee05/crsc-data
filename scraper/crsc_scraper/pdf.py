"""Minimal PDF text extraction (standard library only) for one-page price-list PDFs.

Returns text pieces with their position. Only Flate-compressed content streams and simple
(single-byte) fonts are understood; that is enough for price lists exported from design tools.
Image-only or encrypted PDFs yield nothing, and callers must then treat the brand as unparseable.
"""

from __future__ import annotations

import re
import zlib
from dataclasses import dataclass

_ESC = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f", b"(": b"(", b")": b")", b"\\": b"\\"}
_TOKEN = re.compile(rb"\((?:\\.|[^\\()]|\((?:\\.|[^\\()])*\))*\)|<[0-9A-Fa-f\s]*>|\[|\]|/[^\s/\[\]()<>]+|[-+]?\d*\.?\d+|[A-Za-z'\"*]+")
_NUMBER = re.compile(rb"[-+]?\d*\.?\d+$")


@dataclass(frozen=True)
class Piece:
    y: float
    x: float
    text: str


def _literal(s: bytes) -> str:
    out, i = bytearray(), 0
    while i < len(s):
        c = s[i:i + 1]
        if c == b"\\":
            n = s[i + 1:i + 2]
            if n in _ESC:
                out += _ESC[n]
                i += 2
                continue
            m = re.match(rb"[0-7]{1,3}", s[i + 1:i + 4])
            if m:
                out.append(int(m.group(0), 8) & 255)
                i += 1 + len(m.group(0))
                continue
            i += 2
            continue
        out += c
        i += 1
    return out.decode("cp1252", "replace")


def _stream_pieces(stream: bytes) -> list[Piece]:
    out: list[Piece] = []
    tm = [1.0, 0, 0, 1, 0, 0]
    lm = list(tm)
    stack: list = []
    leading = 0.0
    arr: list | None = None
    for t in _TOKEN.findall(stream):
        if t == b"[":
            arr = []
            continue
        if t == b"]":
            stack.append(arr)
            arr = None
            continue
        if arr is not None:
            arr.append(t)
            continue
        if t[:1] in (b"(", b"<", b"/") or _NUMBER.match(t):
            stack.append(t)
            continue
        try:
            if t == b"BT":
                tm = [1.0, 0, 0, 1, 0, 0]
                lm = list(tm)
            elif t == b"Tm":
                tm = [float(v) for v in stack[-6:]]
                lm = list(tm)
            elif t in (b"Td", b"TD"):
                x, y = (float(v) for v in stack[-2:])
                lm = lm[:4] + [lm[4] + x * lm[0] + y * lm[2], lm[5] + x * lm[1] + y * lm[3]]
                tm = list(lm)
                if t == b"TD":
                    leading = -y
            elif t == b"TL":
                leading = float(stack[-1])
            elif t == b"T*":
                lm = lm[:4] + [lm[4] - leading * lm[2], lm[5] - leading * lm[3]]
                tm = list(lm)
            elif t == b"Tj" and stack and isinstance(stack[-1], bytes) and stack[-1][:1] == b"(":
                out.append(Piece(round(tm[5], 1), round(tm[4], 1), _literal(stack[-1][1:-1])))
            elif t == b"TJ" and stack and isinstance(stack[-1], list):
                out.append(Piece(round(tm[5], 1), round(tm[4], 1), "".join(_literal(v[1:-1]) for v in stack[-1] if v[:1] == b"(")))
        except (ValueError, TypeError, IndexError):
            pass
        stack = []
    return out


def pieces(data: bytes) -> list[Piece]:
    """Text pieces in drawing order. Pieces drawn at the same x keep that order."""
    if b"/Encrypt" in data[:4096] or b"/Encrypt" in data[-4096:]:
        return []
    out: list[Piece] = []
    for raw in re.findall(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S):
        try:
            stream = zlib.decompress(raw)
        except zlib.error:
            continue
        if b"BT" in stream and (b"Tj" in stream or b"TJ" in stream):
            out.extend(_stream_pieces(stream))
    return out
