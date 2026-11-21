"""Collect timestamped matches from the job's speech and reviewed visual evidence."""
from __future__ import annotations

import heapq
import hashlib
from itertools import chain
import json
from pathlib import Path
import re
import tempfile
import unicodedata

ARABIC_FOLD = str.maketrans("أإآٱىـ", "ااااي ")
TRANSCRIPT_LINE = re.compile(r"\[([\d.]+) --> ([\d.]+)\] (.+)")


def terms(text):
    folded = unicodedata.normalize("NFKC", text).casefold().translate(ARABIC_FOLD)
    return re.findall(r"[^\W_]+", "".join(c for c in folded if not unicodedata.combining(c)), re.UNICODE)


def visual_note(job, duration, start, end, frame, description):
    if not 0 <= start < end <= duration:
        raise ValueError("Visual note timestamps must be inside the source")
    if not 1 <= len(description.strip()) <= 240:
        raise ValueError("Enter a visual description up to 240 characters")
    evidence = (job / frame).resolve()
    if (not (evidence.is_relative_to(job / "frames") or evidence.is_relative_to(job / "shots"))
            or evidence.suffix.lower() not in (".jpg", ".jpeg", ".png") or not evidence.is_file()):
        raise ValueError("Visual note frame must be an existing image in the job's frames or shots")
    return {"kind": "visual", "start": start, "end": end, "text": description.strip(),
            "frame": evidence.relative_to(job).as_posix()}


def save_note(job, source, entry):
    data = json.dumps({"source": fingerprint(source), "entry": entry}, ensure_ascii=False, sort_keys=True)
    notes = job / "visual-notes"
    notes.mkdir(exist_ok=True)
    path = notes / (hashlib.sha256(data.encode("utf-8")).hexdigest() + ".json")
    with tempfile.TemporaryDirectory(dir=notes) as directory:
        temp = Path(directory) / path.name
        temp.write_text(data, encoding="utf-8")
        temp.replace(path)
    return path


def visual_entries(job, source, duration):
    current = fingerprint(source)
    for path in (job / "visual-notes").glob("*.json"):
        note = json.loads(path.read_text(encoding="utf-8"))
        if note["source"] == current:
            entry = note["entry"]
            frame = (job / entry["frame"]).resolve()
            if (frame.is_relative_to(job) and frame.is_file() and entry["end"] <= duration):
                yield entry


def speech_entries(job, duration):
    path = job / "transcript.txt"
    if path.is_file():
        with path.open(encoding="utf-8-sig") as stream:
            for line in stream:
                match = TRANSCRIPT_LINE.fullmatch(line.rstrip())
                if match:
                    start, end = map(float, match.groups()[:2])
                    if 0 <= start < end and start < duration:
                        yield {"kind": "speech", "start": start, "end": min(end, duration), "text": match[3][:240],
                               "_search_text": match[3]}


def search(job, source, duration, query, limit):
    wanted = terms(query)
    if not wanted:
        raise ValueError("Enter searchable words")
    unique = set(wanted)
    phrase = " ".join(wanted)

    def ranked():
        for entry in chain(visual_entries(job, source, duration), speech_entries(job, duration)):
            found = terms(entry.pop("_search_text", entry["text"]))
            count = len(unique.intersection(found))
            if count:
                exact = f" {phrase} " in (" " + " ".join(found) + " ")
                yield (count, exact, -entry["start"], entry["kind"], entry["text"]), entry

    return [entry for _, entry in heapq.nlargest(limit, ranked(), key=lambda match: match[0])]


def fingerprint(source):
    stat = source.stat()
    return {"source": str(source), "source_size": stat.st_size, "source_mtime_ns": stat.st_mtime_ns}
