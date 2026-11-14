import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import requests

from src.ingestion.psi_downloader import (
    DOWNLOAD_ENDPOINT,
    IMAGE_ZIP_NAME,
    PSI99Downloader,
    RepositoryFile,
    select_targets,
)


def file_record(name, path, file_id="file-1", version="1", size=4):
    return RepositoryFile(name, path, file_id, version, "3", size)


class FakeResponse:
    def __init__(self, body=b"", status=200, headers=None, chunks=None):
        self.body = body
        self.status_code = status
        self.headers = headers or {}
        self._chunks = chunks
        self.text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else body
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        import json
        return json.loads(self.text)

    def iter_content(self, chunk_size):
        if self._chunks is not None:
            yield from self._chunks
        else:
            yield self.body

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class DownloaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)
        self.csv = file_record("PSI-99_Experimental table_SAFFIRE-2.csv", "Experimental table")
        self.readme = file_record("PSI-99_Science Documents_SAFFIRE-II_README.docx", "Science Documents")
        self.images = file_record(IMAGE_ZIP_NAME, "Raw Data/Reduced Gravity/SAFFIRE-II F1C1", "zip-3")
        self.all_files = [
            self.csv,
            self.readme,
            self.images,
            file_record("Raw2TIFF.zip", "Raw Data/Reduced Gravity/SAFFIRE-II F1C1", "huge", size=1_000_000_000),
            file_record("distortion-grid.zip", "Raw Data/Distortion Grids", "grid"),
        ]

    def session_for(self, resolver, download):
        session = Mock()
        session.get.side_effect = [resolver, download]
        return session

    def test_successful_download_and_endpoint_parameters(self):
        resolver = FakeResponse(b"https://bucket.s3.amazonaws.com/object?signature=secret")
        download = FakeResponse(b"data", headers={"Content-Length": "4"})
        session = self.session_for(resolver, download)
        downloader = PSI99Downloader(self.output, session=session)

        with self.assertLogs("src.ingestion.psi_downloader", level="INFO") as captured:
            result = downloader.download_one(self.csv)

        self.assertEqual(result, "downloaded")
        self.assertEqual((self.output / self.csv.filename).read_bytes(), b"data")
        self.assertEqual(session.get.call_args_list[0].args[0], DOWNLOAD_ENDPOINT)
        self.assertEqual(session.get.call_args_list[0].kwargs["params"], {
            "file": "file-1", "version": "1", "studyVersion": "3", "redirect": "false"
        })
        self.assertNotIn("signature=secret", " ".join(captured.output))

    def test_http_error_does_not_leave_final_file(self):
        resolver = FakeResponse(b"https://bucket.s3.amazonaws.com/object")
        download = FakeResponse(status=503)
        session = self.session_for(resolver, download)
        with self.assertRaises(requests.HTTPError):
            PSI99Downloader(self.output, session=session).download_one(self.csv)
        self.assertFalse((self.output / self.csv.filename).exists())
        self.assertFalse((self.output / (self.csv.filename + ".part")).exists())

    def test_interrupted_stream_removes_partial_file(self):
        resolver = FakeResponse(b"https://bucket.s3.amazonaws.com/object")

        def interrupted():
            yield b"ab"
            raise requests.ConnectionError("connection reset")

        download = FakeResponse(headers={"Content-Length": "4"}, chunks=interrupted())
        session = self.session_for(resolver, download)
        with self.assertRaises(requests.ConnectionError):
            PSI99Downloader(self.output, session=session).download_one(self.csv)
        self.assertFalse((self.output / self.csv.filename).exists())
        self.assertFalse((self.output / (self.csv.filename + ".part")).exists())

    def test_skips_valid_existing_file(self):
        (self.output / self.csv.filename).write_bytes(b"data")
        session = Mock()
        result = PSI99Downloader(self.output, session=session).download_one(self.csv)
        self.assertEqual(result, "skipped")
        session.get.assert_not_called()

    def test_only_three_approved_targets_are_selected(self):
        selected = select_targets(self.all_files)
        self.assertEqual({item.filename for item in selected}, {
            self.csv.filename, self.readme.filename, self.images.filename
        })
        self.assertNotIn("Raw2TIFF.zip", {item.filename for item in selected})


if __name__ == "__main__":
    unittest.main()
