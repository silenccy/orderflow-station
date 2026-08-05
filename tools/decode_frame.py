"""
Generic protobuf wire-format decoder for the unidentified non-book frames.
Reads capture_raw.jsonl, skips book (OFFER/BID) and ping frames, buckets the
rest by length, and dumps one representative of each bucket as a protobuf field
tree so we can identify trade/tick frames. No .proto needed.
"""
import json
import struct
from pathlib import Path

RAW = "capture_raw.jsonl"


def read_varint(b, i):
    shift = 0
    result = 0
    while True:
        byte = b[i]
        i += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return result, i
        shift += 7


def printable_ratio(bs):
    if not bs:
        return 0.0
    return sum(0x20 <= c <= 0x7E for c in bs) / len(bs)


def parse(b, depth=0, out=None):
    if out is None:
        out = []
    i, n = 0, len(b)
    pad = "  " * depth
    while i < n:
        try:
            tag, i = read_varint(b, i)
        except IndexError:
            out.append(f"{pad}<trailing {b[i:].hex()}>")
            break
        field, wt = tag >> 3, tag & 7
        if wt == 0:
            val, i = read_varint(b, i)
            out.append(f"{pad}#{field} varint = {val}")
        elif wt == 1:
            chunk = b[i:i + 8]; i += 8
            d = struct.unpack("<d", chunk)[0] if len(chunk) == 8 else None
            out.append(f"{pad}#{field} fixed64 = {d!r}  (i64={int.from_bytes(chunk,'little')})")
        elif wt == 2:
            ln, i = read_varint(b, i)
            sub = b[i:i + ln]; i += ln
            if printable_ratio(sub) > 0.85 and ln > 0:
                out.append(f"{pad}#{field} str[{ln}] = {sub.decode('ascii','replace')!r}")
            else:
                # try to parse as a nested message
                try:
                    nested = parse(sub, depth + 1)
                    out.append(f"{pad}#{field} msg[{ln}]:")
                    out.extend(nested)
                except Exception:
                    out.append(f"{pad}#{field} bytes[{ln}] = {sub.hex()}")
        elif wt == 5:
            chunk = b[i:i + 4]; i += 4
            f = struct.unpack("<f", chunk)[0] if len(chunk) == 4 else None
            out.append(f"{pad}#{field} fixed32 = {f!r}  (i32={int.from_bytes(chunk,'little')})")
        else:
            out.append(f"{pad}<bad wiretype {wt} at {i}, rest={b[i:].hex()}>")
            break
    return out


def main():
    lines = Path(RAW).read_text().splitlines()
    buckets = {}
    for ln in lines:
        try:
            frame = bytes.fromhex(json.loads(ln)["hex"])
        except Exception:
            continue
        if b"OFFER" in frame or b"BID" in frame or b"ping" in frame:
            continue
        buckets.setdefault(len(frame), frame)

    print(f"{len(lines)} raw frames; non-book length buckets: {sorted(buckets)}\n")
    for length in sorted(buckets):
        frame = buckets[length]
        print("=" * 70)
        print(f"LENGTH {length}  (first occurrence)")
        print("=" * 70)
        for row in parse(frame):
            print(row)
        print()


if __name__ == "__main__":
    main()
