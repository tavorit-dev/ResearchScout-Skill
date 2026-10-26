"""JSONL record storage and Excel-safe CSV export.

JSONL is the on-disk format for the source registry and document metadata:
appending is cheap and the file migrates across research projects (spec 9.4).
"""
import csv
import json
from pathlib import Path


def append_record(path, record):
    """Append one JSON object as a line, creating the parent directory."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_records(path):
    """Read all records; return [] when the file does not exist.

    Blank lines are skipped so a trailing newline or a hand-edited gap is
    tolerated.
    """
    path = Path(path)
    if not path.exists():
        return []
    records = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def upsert_by_id(path, record, id_field):
    """Insert or replace the record with a matching id_field value.

    Returns "inserted" or "updated". An update keeps the record's original
    position in the file.
    """
    path = Path(path)
    records = read_records(path)
    target = record.get(id_field)
    status = "inserted"
    for index, existing in enumerate(records):
        if existing.get(id_field) == target:
            records[index] = record
            status = "updated"
            break
    else:
        records.append(record)

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for item in records:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    return status


def write_csv(path, records, fields):
    """Write records as CSV with the given column order.

    Uses UTF-8 with BOM and newline="" so Excel opens the file correctly,
    including non-ASCII text (acceptance V8). Missing fields become empty
    cells.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            writer.writerow({field: record.get(field, "") for field in fields})
