"""Lossless byte-indexed splitting of target logs into monotonic time epochs."""

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path


def digest(data):
    return hashlib.sha256(data).hexdigest()


def index_frames(data):
    lines = data.splitlines(keepends=True)
    offset = 0
    frames = []
    current = None
    prefix_end = len(data)
    for line in lines:
        first = line.decode("utf-8-sig").split(",")[0].strip()
        if first == "START":
            if current is not None:
                current["end"] = offset
                frames.append(current)
            else:
                prefix_end = offset
            current = {
                "start": offset,
                "end": None,
                "complete": False,
                "times": [],
                "invalid_time": False,
            }
        elif current is not None:
            if first == "END":
                current["complete"] = True
            if "---->" in first:
                try:
                    current["times"].append(
                        datetime.strptime(
                            first.replace("---->", "").strip(), "%Y/%m/%d %H:%M:%S:%f"
                        )
                    )
                except ValueError:
                    current["invalid_time"] = True
        offset += len(line)
    if current is not None:
        current["end"] = len(data)
        frames.append(current)
    if not frames:
        raise ValueError("No START-framed input")
    for frame in frames:
        times = frame.pop("times")
        frame["time"] = (
            times[0] if len(times) == 1 and not frame.pop("invalid_time") else None
        )
        frame["length"] = frame["end"] - frame["start"]
    return prefix_end, frames


def export(source, destination, max_gap=1.0):
    if not 0 < max_gap < float("inf"):
        raise ValueError("Finite positive max_gap required")
    source = Path(source)
    data = source.read_bytes()
    prefix_end, frames = index_frames(data)
    prefix = data[:prefix_end]
    dest = Path(destination)
    dest.mkdir(parents=True, exist_ok=False)
    (dest / "prefix.bin").write_bytes(prefix)
    previous = None
    epoch = -1
    active = None
    records = []
    for order, frame in enumerate(frames):
        stamp = frame["time"]
        reason = None
        if not frame["complete"]:
            reason = "incomplete_frame"
        elif stamp is None:
            reason = "missing_or_invalid_or_multiple_timestamp"
        if reason:
            name = "quarantine.bin"
            previous = None
            active = None
        else:
            if previous is None:
                reason = "start_after_boundary"
            elif stamp <= previous:
                reason = "non_increasing_time"
            elif (stamp - previous).total_seconds() > max_gap:
                reason = "long_gap"
            if reason or active is None:
                epoch += 1
                active = f"epoch_{epoch:04d}.csv"
                (dest / active).write_bytes(prefix)
            name = active
            previous = stamp
        chunk = data[frame["start"] : frame["end"]]
        path = dest / name
        with path.open("ab") as stream:
            out_offset = stream.tell()
            stream.write(chunk)
        records.append(
            {
                "order": order,
                "source_start": frame["start"],
                "length": len(chunk),
                "file": name,
                "offset": out_offset,
                "sha256": digest(chunk),
                "time": stamp.isoformat() if stamp is not None else None,
                "complete": frame["complete"],
                "boundary_reason": reason,
            }
        )
    manifest = {
        "version": 1,
        "source": source.name,
        "source_sha256": digest(data),
        "source_bytes": len(data),
        "prefix_bytes": len(prefix),
        "prefix_sha256": digest(prefix),
        "max_gap_s": max_gap,
        "epochs": epoch + 1,
        "frames": records,
    }
    (dest / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    if reconstruct(dest) != data:
        raise RuntimeError("Export failed byte-exact reconstruction")
    return manifest


def reconstruct(directory):
    root = Path(directory)
    m = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if m.get("version") != 1:
        raise ValueError("Unsupported manifest")
    prefix = (root / "prefix.bin").read_bytes()
    if len(prefix) != m["prefix_bytes"] or digest(prefix) != m["prefix_sha256"]:
        raise ValueError("Prefix integrity failure")
    chunks = [prefix]
    cursor = len(prefix)
    for order, row in enumerate(m["frames"]):
        name = row["file"]
        if Path(name).name != name or name in (".", ".."):
            raise ValueError("Invalid artifact filename")
        if (
            row["order"] != order
            or row["source_start"] != cursor
            or row["length"] <= 0
            or row["offset"] < 0
        ):
            raise ValueError("Non-contiguous source index")
        with (root / name).open("rb") as stream:
            stream.seek(row["offset"])
            chunk = stream.read(row["length"])
        if len(chunk) != row["length"] or digest(chunk) != row["sha256"]:
            raise ValueError("Frame integrity failure")
        chunks.append(chunk)
        cursor += len(chunk)
    result = b"".join(chunks)
    if len(result) != m["source_bytes"] or digest(result) != m["source_sha256"]:
        raise ValueError("Source integrity failure")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--max-gap", type=float, default=1.0)
    args = parser.parse_args()
    result = export(args.source, args.destination, args.max_gap)
    print(
        json.dumps(
            {
                "frames": len(result["frames"]),
                "epochs": result["epochs"],
                "source_sha256": result["source_sha256"],
            }
        )
    )
