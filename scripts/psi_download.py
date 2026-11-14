#!/usr/bin/env python3
"""
Download the useful part of NASA PSI combustion investigations (Flame in Freefall).

Default ("docs" tier, MB-scale):
  - investigation metadata JSON          -> <out>/<PSI-ID>/investigation.json
  - full file index                      -> <out>/<PSI-ID>/files_index.json
  - experimental tables (CSV)            -> one row per test: the core dataset
  - reports, science/engineering docs, analyzed data, presentations (PDF/XLS/...)
  - representative images

Optional --raw-text: also pull the small raw-data text files (per-run .txt/.csv/.dat time series).
Videos, image sequences and big zips (the ~14 TB) are always skipped.

Usage:
  pip install requests
  python psi_download.py --tables-only        # JSON + experimental CSVs only (~1 MB)
  python psi_download.py                      # docs tier, all combustion investigations
  python psi_download.py --raw-text           # + raw per-run text data
  python psi_download.py --ids PSI-69 PSI-25  # only some investigations
  python psi_download.py --dry-run            # print plan + sizes, download nothing

Unofficial API (the one psi.nasa.gov's own web app calls) - may change without notice.
"""

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

BASE = "https://psi.nasa.gov/geode-py/ws"

# Search hits that are not combustion / fire-safety related
OFF_TOPIC = {"PSI-181", "PSI-108"}

DOC_CATEGORIES = {
    "Experimental table",
    "Investigation Metadata Files",
    "Reports",
    "Science Documents",
    "Engineering Documents",
    "Analyzed Data",
    "Presentations",
    "Reference Data",
    "Representative Image(s)",
}
DOC_EXT = {"pdf", "csv", "txt", "dat", "xls", "xlsx", "doc", "docx",
           "ppt", "pptx", "zip", "jpg", "jpeg", "png", "asc"}
RAW_TEXT_EXT = {"csv", "txt", "dat", "asc", "xls", "xlsx", "log", "rtf"}

session = requests.Session()
session.headers["User-Agent"] = "flame-in-freefall-ingest/1.0"


def get_json(url, **kw):
    for attempt in range(4):
        try:
            r = session.get(url, timeout=60, **kw)
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def search_investigations(term):
    data = get_json(f"{BASE}/repo/search",
                    params={"term": term, "source": "psi", "type": "investigation", "size": 200})
    return [h["_source"]["Accession"] for h in data["hits"]["hits"]]


def get_investigation(psi_id):
    return get_json(f"{BASE}/repo/investigations/{psi_id}")


def list_files(psi_id, version, cache_path):
    """Returns a flat list of file dicts (id, key, bytes_size, version, sortingCategory, subcategory).

    The listing is slow for big investigations (thousands of files) and the PSI gateway
    sometimes answers 504 - so retry with backoff, and cache the result on disk.
    """
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text())
            if isinstance(cached, dict) and cached.get("version") == version:
                return cached["files"]
        except (ValueError, KeyError):
            pass

    body = {"folder": f"psi-data/{psi_id}/", "fileType": "study",
            "studyId": psi_id, "version": version}
    delays = [5, 15, 30, 60, 120]
    for attempt, delay in enumerate(delays + [None]):
        try:
            r = session.post(f"{BASE}/files/v2", json=body, timeout=300)
            r.raise_for_status()
            break
        except requests.RequestException as e:
            if delay is None:
                raise
            code = getattr(e.response, "status_code", type(e).__name__)
            print(f"  {psi_id}: file listing failed ({code}), retry {attempt + 1}/{len(delays)} in {delay}s")
            time.sleep(delay)
    flat = []

    def walk(node):
        for f in node.get("files") or []:
            if f.get("directory") is False:
                flat.append(f)
            else:
                walk(f)

    walk(r.json())
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({"version": version, "files": flat}, indent=2, ensure_ascii=False))
    return flat


def wanted(f, raw_text, max_bytes, tables_only=False):
    cat = f.get("sortingCategory") or ""
    ext = f["key"].rsplit(".", 1)[-1].lower()
    size = float(f.get("bytes_size") or 0)
    if size > max_bytes or f.get("restricted"):
        return False
    if tables_only:
        return cat == "Experimental table"
    if cat in DOC_CATEGORIES and ext in DOC_EXT:
        return True
    if raw_text and cat == "Raw Data" and ext in RAW_TEXT_EXT:
        return True
    return False


def local_path(out_dir, psi_id, f):
    # Category / subcategory folders mirror the PSI tree and keep same-named files apart
    name = f["key"].rsplit("/", 1)[-1]
    parts = [out_dir, psi_id, f.get("sortingCategory") or "other"]
    if f.get("subcategory"):
        parts += f["subcategory"].split("/")
    return Path(*parts) / name


def download_file(psi_id, f, dest):
    if dest.exists() and dest.stat().st_size == int(float(f.get("bytes_size") or 0)):
        return "skip"
    for attempt in range(4):
        try:
            # 1) ask PSI for a short-lived signed S3 URL (plain text response).
            #    `file` = the file's id (what the PSI web app sends) - unambiguous, unlike file names.
            r = session.get(f"{BASE}/studies/{psi_id}/download",
                            params={"file": f["id"], "version": f.get("version", 1), "redirect": "false"},
                            timeout=60)
            r.raise_for_status()
            url = r.text.strip().strip('"')
            # 2) stream the file from S3
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(dest.suffix + ".part")
            with session.get(url, stream=True, timeout=300) as d:
                d.raise_for_status()
                with open(tmp, "wb") as fh:
                    for chunk in d.iter_content(1 << 20):
                        fh.write(chunk)
            tmp.replace(dest)
            return "ok"
        except requests.RequestException as e:
            if attempt == 3:
                return f"error: {e}"
            time.sleep(2 ** attempt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="psi_data")
    ap.add_argument("--term", default="combustion")
    ap.add_argument("--ids", nargs="*", help="only these PSI IDs (e.g. PSI-69 PSI-25)")
    ap.add_argument("--raw-text", action="store_true", help="also fetch small raw-data text files")
    ap.add_argument("--tables-only", action="store_true",
                    help="only investigation JSON + experimental tables (~1 MB, seconds)")
    ap.add_argument("--max-mb", type=float, default=50, help="skip any single file above this size")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    out_dir = Path(args.out)
    ids = args.ids or [i for i in search_investigations(args.term) if i not in OFF_TOPIC]
    print(f"{len(ids)} investigations: {' '.join(ids)}")

    jobs, total_bytes, failed = [], 0, []
    for psi_id in ids:
        inv_dir = out_dir / psi_id
        try:
            inv = get_investigation(psi_id)
            inv_dir.mkdir(parents=True, exist_ok=True)
            (inv_dir / "investigation.json").write_text(json.dumps(inv, indent=2, ensure_ascii=False))
            files = list_files(psi_id, inv["version"], inv_dir / "files_index.json")
        except requests.RequestException as e:
            # Don't let one slow investigation kill the run: skip it, report at the end
            print(f"  {psi_id:8} SKIPPED - could not list files ({e})", file=sys.stderr)
            failed.append(psi_id)
            continue

        # The listing repeats some files: keep one entry per file id
        unique = {f["id"]: f for f in files if f.get("id")}
        picked = [f for f in unique.values()
                  if wanted(f, args.raw_text, args.max_mb * 1e6, args.tables_only)]
        size = sum(float(f.get("bytes_size") or 0) for f in picked)
        total_bytes += size
        print(f"  {psi_id:8} {inv.get('investigationAcronym') or '':22} "
              f"{len(picked):5} / {len(unique):5} files  {size / 1e6:9.1f} MB")
        used = set()
        for f in picked:
            dest = local_path(out_dir, psi_id, f)
            if dest in used:  # two different files with the same name in the same folder
                dest = dest.with_name(f"{dest.stem}__{f['id']}{dest.suffix}")
            used.add(dest)
            jobs.append((psi_id, f, dest))

    print(f"Plan: {len(jobs)} files, {total_bytes / 1e9:.2f} GB -> {out_dir.resolve()}")
    if failed:
        print(f"Listing failed for {' '.join(failed)} - retry later with: --ids {' '.join(failed)}")
    if args.dry_run:
        return

    done = errors = 0
    with ThreadPoolExecutor(args.workers) as pool:
        futs = {pool.submit(download_file, *j): j for j in jobs}
        for fut in as_completed(futs):
            psi_id, f, dest = futs[fut]
            status = fut.result()
            done += 1
            if status.startswith("error"):
                errors += 1
                print(f"  [{psi_id}] {dest.name}: {status}", file=sys.stderr)
            if done % 50 == 0 or done == len(jobs):
                print(f"  {done}/{len(jobs)} ({errors} errors)")

    print("Done." if not errors else f"Done with {errors} errors - rerun to retry (finished files are skipped).")
    if failed:
        print(f"Not downloaded (listing failed): {' '.join(failed)} -> rerun with --ids {' '.join(failed)}")


if __name__ == "__main__":
    main()
