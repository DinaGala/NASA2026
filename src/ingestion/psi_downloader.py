"""Download the three approved PSI-99 SAFFIRE-II files.

The PSI file-listing response is not yet represented by a verified client in this
project. This module therefore accepts a small manifest populated from PSI's
actual file listing; it deliberately does not invent a listing endpoint/schema.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.parse import urlparse

import requests

LOGGER = logging.getLogger(__name__)
DOWNLOAD_ENDPOINT = "https://psi.nasa.gov/geode-py/ws/studies/PSI-99/download"
OUTPUT_DIR = Path("data/raw/psi/PSI-99")
CSV_NAME = "PSI-99_Experimental table_SAFFIRE-2.csv"
IMAGE_ZIP_NAME = "SAFFIRE_II_F1C1_Image_Data_All_JPEGS.zip"
CSV_PATH = "Experimental table"
IMAGE_PATH = "Raw Data/Reduced Gravity/SAFFIRE-II F1C1"


@dataclass(frozen=True)
class RepositoryFile:
    """The fields needed from one item in PSI's file listing."""

    filename: str
    repository_path: str
    file_id: str
    version: str
    study_version: str
    size_bytes: int | None = None


def _normal_path(value: str) -> str:
    parts = [part for part in PurePosixPath(value.replace("\\", "/")).parts if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise ValueError(f"Unsafe repository path: {value!r}")
    return "/".join(parts).casefold()


def _readme_candidate(files: Iterable[RepositoryFile]) -> RepositoryFile:
    candidates = [
        item for item in files
        if _normal_path(item.repository_path).rstrip("/").endswith("science documents")
        and item.filename.casefold().endswith(".docx")
        and re.search(r"read\s*me|readme", item.filename, re.IGNORECASE)
    ]
    if len(candidates) != 1:
        raise ValueError(
            "Expected exactly one README DOCX in PSI-99 / Science Documents; "
            f"found {len(candidates)}. Check the PSI file listing and its paths."
        )
    return candidates[0]


def select_targets(files: Iterable[RepositoryFile]) -> list[RepositoryFile]:
    """Select exactly the two named assets and the unique Science Documents README."""
    entries = list(files)
    selected: list[RepositoryFile] = []
    expected = (
        (CSV_NAME, CSV_PATH),
        (IMAGE_ZIP_NAME, IMAGE_PATH),
    )
    for filename, expected_path in expected:
        found = [
            item for item in entries
            if item.filename == filename and _normal_path(item.repository_path) == _normal_path(expected_path)
        ]
        if len(found) != 1:
            raise ValueError(f"Expected exactly one {filename!r} at {expected_path!r}; found {len(found)}")
        selected.append(found[0])
    readme = _readme_candidate(entries)
    if readme in selected:
        raise ValueError("The README matched another target unexpectedly")
    selected.append(readme)
    if len({item.filename for item in selected}) != 3:
        raise ValueError("Target file names must be unique")
    return selected


def _signed_url(response: requests.Response) -> str:
    """Extract a single HTTPS URL from a plain-text or JSON download response."""
    response.raise_for_status()
    body = response.text.strip()
    candidates: list[str] = []
    if body.startswith("https://"):
        candidates.append(body)
    else:
        try:
            payload: Any = response.json()
        except (ValueError, json.JSONDecodeError):
            payload = None

        def visit(value: Any) -> None:
            if isinstance(value, str) and value.startswith("https://"):
                candidates.append(value)
            elif isinstance(value, dict):
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(payload)
    candidates = list(dict.fromkeys(candidates))
    if len(candidates) != 1:
        raise ValueError("PSI download response did not contain exactly one HTTPS URL")
    parsed = urlparse(candidates[0])
    host = (parsed.hostname or "").casefold()
    if not (host.endswith(".amazonaws.com") or host == "amazonaws.com"):
        raise ValueError("PSI download response URL is not on an Amazon AWS host")
    return candidates[0]


class PSI99Downloader:
    def __init__(
        self,
        output_dir: Path = OUTPUT_DIR,
        session: requests.Session | None = None,
        timeout: tuple[float, float] = (15, 120),
    ) -> None:
        self.output_dir = Path(output_dir)
        self.session = session or requests.Session()
        self.timeout = timeout

    def _download_url(self, item: RepositoryFile) -> str:
        response = self.session.get(
            DOWNLOAD_ENDPOINT,
            params={
                "file": item.file_id,
                "version": item.version,
                "studyVersion": item.study_version,
                "redirect": "false",
            },
            timeout=self.timeout,
        )
        try:
            return _signed_url(response)
        finally:
            response.close()

    def _is_valid_existing(self, path: Path, expected_size: int | None) -> bool:
        if not path.is_file():
            return False
        size = path.stat().st_size
        return size > 0 and (expected_size is None or size == expected_size)

    def download_one(self, item: RepositoryFile) -> str:
        # Protect this low-level method too: callers cannot use it to fetch arbitrary files.
        select_targets_from_single(item)
        if Path(item.filename).name != item.filename or "\\" in item.filename:
            raise ValueError("Unsafe file name from PSI listing")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        destination = self.output_dir / item.filename
        if self._is_valid_existing(destination, item.size_bytes):
            LOGGER.info("Skipped existing valid file: %s", item.filename)
            return "skipped"

        part_path = destination.with_name(destination.name + ".part")
        part_path.unlink(missing_ok=True)
        try:
            signed_url = self._download_url(item)
            with self.session.get(signed_url, stream=True, timeout=self.timeout) as response:
                response.raise_for_status()
                header_size = response.headers.get("Content-Length")
                expected_size = item.size_bytes
                if expected_size is not None and header_size and int(header_size) != expected_size:
                    raise IOError(f"Content-Length mismatch for {item.filename}")
                bytes_written = 0
                with part_path.open("wb") as output:
                    for chunk in response.iter_content(chunk_size=1024 * 256):
                        if chunk:
                            output.write(chunk)
                            bytes_written += len(chunk)
                if bytes_written == 0:
                    raise IOError(f"Empty response while downloading {item.filename}")
                if expected_size is not None and bytes_written != expected_size:
                    raise IOError(f"Downloaded size mismatch for {item.filename}")
                if header_size and bytes_written != int(header_size):
                    raise IOError(f"Truncated response while downloading {item.filename}")
            os.replace(part_path, destination)
            LOGGER.info("Downloaded: %s (%d bytes)", item.filename, bytes_written)
            return "downloaded"
        except Exception as exc:
            part_path.unlink(missing_ok=True)
            # Never log the exception text: HTTP clients may include the signed S3 URL.
            LOGGER.error("Failed to download %s (%s)", item.filename, type(exc).__name__)
            raise

    def download_targets(self, files: Iterable[RepositoryFile]) -> dict[str, str]:
        targets = select_targets(files)
        results: dict[str, str] = {}
        for item in targets:
            results[item.filename] = self.download_one(item)
        return results


def select_targets_from_single(item: RepositoryFile) -> list[RepositoryFile]:
    """Validate a single selected record without requiring the other listing rows."""
    path = _normal_path(item.repository_path)
    valid = (
        (item.filename == CSV_NAME and path == _normal_path(CSV_PATH))
        or (item.filename == IMAGE_ZIP_NAME and path == _normal_path(IMAGE_PATH))
        or (
            path.rstrip("/").endswith("science documents")
            and item.filename.casefold().endswith(".docx")
            and re.search(r"read\s*me|readme", item.filename, re.IGNORECASE) is not None
        )
    )
    if not valid:
        raise ValueError("File is not one of the PSI-99 allowlisted targets")
    return [item]


def load_manifest(path: Path) -> tuple[list[RepositoryFile], str]:
    """Load an explicit manifest; its format is internal and not a PSI API schema."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if str(payload.get("study_id")) != "PSI-99":
        raise ValueError("Manifest study_id must be PSI-99")
    study_version = str(payload.get("study_version", ""))
    if not study_version:
        raise ValueError("Manifest must include study_version from the PSI listing")
    records = []
    for row in payload.get("files", []):
        records.append(RepositoryFile(
            filename=row["filename"],
            repository_path=row["repository_path"],
            file_id=str(row["file_id"]),
            version=str(row["version"]),
            study_version=str(row.get("study_version", study_version)),
            size_bytes=int(row["size_bytes"]) if row.get("size_bytes") is not None else None,
        ))
    return records, study_version


def main() -> int:
    parser = argparse.ArgumentParser(description="Download only the selected PSI-99 SAFFIRE-II files")
    parser.add_argument("--manifest", required=True, type=Path, help="JSON exported from a verified PSI file listing")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        records, _study_version = load_manifest(args.manifest)
        PSI99Downloader(output_dir=args.output_dir).download_targets(records)
    except Exception as exc:
        # Exception text can contain temporary signed URLs or other request details.
        LOGGER.error("Downloader stopped (%s)", type(exc).__name__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
