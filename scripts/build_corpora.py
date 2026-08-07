"""Build the three benchmark corpora from data already on disk.

    easy    corpora/hdfs_demo.log   copied from the Flare repo
    medium  corpora/jobs.jsonl      exported from the JobHunter SQLite DB
    hard    corpora/mixed.log       the two above, interleaved

Nothing here downloads or synthesizes data — every corpus is real production
output. The hard tier is built by interleaving, which is what a shared log
pipeline does to two services anyway.

Usage:
    python scripts/build_corpora.py --flare-repo PATH --jobs-db PATH
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = ROOT / "corpora"

JOBS_LIMIT = 4000  # keep the corpus demo-sized; the DB has 33,626
MIXED_LIMIT = 3000
SEED = 20260807


def build_hdfs(flare_repo: Path) -> Path:
    """Copy the HDFS demo log out of the Flare repo."""
    src = flare_repo / "logs" / "hdfs_demo.log"
    if not src.exists():
        sys.exit(f"error: {src} not found — pass --flare-repo pointing at a Flare checkout")
    dst = CORPUS_DIR / "hdfs_demo.log"
    shutil.copyfile(src, dst)
    return dst


def build_jobs(jobs_db: Path) -> Path:
    """Export job rows as newline-delimited JSON.

    Keys repeat on every record, which is exactly the redundancy that schema
    collapse attacks — a different mechanism from line templating.
    """
    if not jobs_db.exists():
        sys.exit(f"error: {jobs_db} not found — pass --jobs-db pointing at jobs.db")

    dst = CORPUS_DIR / "jobs.jsonl"
    conn = sqlite3.connect(jobs_db)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute(
            "SELECT company, title, location, url, posted_at, discovered_at "
            "FROM jobs ORDER BY id LIMIT ?",
            (JOBS_LIMIT,),
        )
        with dst.open("w", encoding="utf-8") as fh:
            for row in cursor:
                fh.write(json.dumps(dict(row), separators=(",", ":")) + "\n")
    finally:
        conn.close()
    return dst


def build_mixed(hdfs: Path, jobs: Path) -> Path:
    """Interleave the two corpora into one multi-format stream.

    Deterministic given SEED so the benchmark is reproducible across runs.
    """
    rng = random.Random(SEED)
    hdfs_lines = hdfs.read_text(encoding="utf-8", errors="replace").splitlines()
    jobs_lines = jobs.read_text(encoding="utf-8", errors="replace").splitlines()

    dst = CORPUS_DIR / "mixed.log"
    out: list[str] = []
    hi = ji = 0
    while len(out) < MIXED_LIMIT and (hi < len(hdfs_lines) or ji < len(jobs_lines)):
        # 2:1 log-to-record ratio, jittered — roughly what a shared pipeline looks like
        take_hdfs = rng.random() < 0.66
        if take_hdfs and hi < len(hdfs_lines):
            out.append(hdfs_lines[hi])
            hi += 1
        elif ji < len(jobs_lines):
            out.append(jobs_lines[ji])
            ji += 1
        elif hi < len(hdfs_lines):
            out.append(hdfs_lines[hi])
            hi += 1

    dst.write_text("\n".join(out) + "\n", encoding="utf-8")
    return dst


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flare-repo", type=Path, required=True)
    parser.add_argument("--jobs-db", type=Path, required=True)
    args = parser.parse_args()

    CORPUS_DIR.mkdir(exist_ok=True)

    hdfs = build_hdfs(args.flare_repo)
    jobs = build_jobs(args.jobs_db)
    mixed = build_mixed(hdfs, jobs)

    for path in (hdfs, jobs, mixed):
        n = sum(1 for _ in path.open(encoding="utf-8", errors="replace"))
        print(f"  {path.name:20} {n:>7,} lines  {path.stat().st_size / 1024:>7.1f} KB")


if __name__ == "__main__":
    main()
