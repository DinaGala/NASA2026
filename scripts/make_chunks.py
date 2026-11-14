"""Split the PSI documents into chunks, embed them, and write data/processed/chunks.parquet.

Reads PDFs (page = PDF page), PowerPoint .pptx (page = slide number) and Word .docx
(page = null: Word files have no fixed pages). Chunks never cross a page, so every search
hit can cite its page. Image-only pages have no text layer and are skipped (no OCR); old
binary .doc / .ppt files are skipped too.
"""

import hashlib
import json
import re
from collections.abc import Iterator
from pathlib import Path

import docx
import pandas as pd
import pptx
import pymupdf

from src.agent.embeddings import embed_passages

ROOT = Path(__file__).resolve().parents[1]
PSI_DATA = ROOT / "psi_data"
OUTPUT = ROOT / "data" / "processed" / "chunks.parquet"

SUFFIXES = {".pdf", ".pptx", ".docx"}
WORDS_PER_CHUNK = 180
OVERLAP_WORDS = 30
MIN_WORDS = 15


def doc_title(path: Path, psi_id: str, category: str) -> str:
    """'PSI-69_Science Documents_FLEX_Test_Plan.pdf' -> 'FLEX Test Plan'."""
    title = path.stem.removeprefix(f"{psi_id}_{category}_")
    return " ".join(title.replace("_", " ").split())


def clean(text: str) -> str:
    # Postgres text cannot hold NUL bytes; some PDFs carry them and other control characters.
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", text)
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    return " ".join(text.split())


def split_words(text: str) -> list[str]:
    words = text.split()
    step = WORDS_PER_CHUNK - OVERLAP_WORDS
    chunks = []
    for start in range(0, len(words), step):
        window = words[start : start + WORDS_PER_CHUNK]
        if len(window) >= MIN_WORDS:
            chunks.append(" ".join(window))
        if start + WORDS_PER_CHUNK >= len(words):
            break
    return chunks


def pages(path: Path) -> Iterator[tuple[int | None, str]]:
    """Yield (page number, raw text) for each page of the document."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        with pymupdf.open(path) as pdf:
            for number, page in enumerate(pdf, start=1):
                yield number, page.get_text()
    elif suffix == ".pptx":
        for number, slide in enumerate(pptx.Presentation(path).slides, start=1):
            yield number, "\n".join(
                shape.text_frame.text for shape in slide.shapes if shape.has_text_frame
            )
    elif suffix == ".docx":
        document = docx.Document(path)
        cells = (cell.text for table in document.tables for row in table.rows for cell in row.cells)
        yield None, "\n".join([*(p.text for p in document.paragraphs), *cells])


def study_names() -> dict[str, str]:
    """'PSI-69' -> 'FLEX (Flame Extinguishment Experiment)', from each investigation.json."""
    names = {}
    for path in PSI_DATA.glob("PSI-*/investigation.json"):
        record = json.loads(path.read_text())
        acronym, title = record.get("investigationAcronym"), record.get("title", "")
        names[path.parent.name] = f"{acronym} ({title})" if acronym else title
    return names


def with_header(chunk: dict, names: dict[str, str]) -> str:
    """Text to embed: the chunk prefixed by its study and document.

    A page rarely names its experiment, so without the header a question about FLEX or
    Saffire cannot match it. Only the embedding gets the header; `text` stays as written.
    """
    return f"{names.get(chunk['psi_id'], chunk['psi_id'])}. {chunk['doc_title']}: {chunk['text']}"


def chunk_document(path: Path) -> list[dict]:
    psi_id, category = path.parts[-3], path.parts[-2]
    title = doc_title(path, psi_id, category)
    doc_key = hashlib.sha1(str(path.relative_to(PSI_DATA)).encode()).hexdigest()[:10]
    rows = []
    for page, raw_text in pages(path):
        for index, text in enumerate(split_words(clean(raw_text))):
            rows.append(
                {
                    "chunk_id": f"{psi_id}/{doc_key}/p{page or 0}/c{index}",
                    "psi_id": psi_id,
                    "doc_title": title,
                    "doc_category": category,
                    "page": page,
                    "text": text,
                }
            )
    return rows


def main() -> None:
    rows: list[dict] = []
    seen_files: set[tuple[str, str]] = set()
    skipped = []
    paths = sorted(p for p in PSI_DATA.glob("PSI-*/*/*") if p.suffix.lower() in SUFFIXES)
    for path in paths:
        # Skip a file filed twice in the same study. A paper shared by two studies is
        # kept once per study, so filtering by psi_id still finds it.
        key = (path.parts[-3], hashlib.sha1(path.read_bytes()).hexdigest())
        if key in seen_files:
            continue
        seen_files.add(key)
        try:
            rows.extend(chunk_document(path))
        except Exception as error:  # a broken file should not stop the run
            skipped.append(f"{path.relative_to(PSI_DATA)}: {error}")

    chunks = pd.DataFrame(rows).astype({"page": "Int64"})
    print(f"{len(seen_files)} documents, {len(chunks)} chunks; embedding...")
    names = study_names()
    chunks["embedding"] = embed_passages([with_header(row, names) for row in rows])

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    chunks.to_parquet(OUTPUT, index=False)
    print(f"Wrote {len(chunks)} chunks to {OUTPUT.relative_to(ROOT)}")
    for line in skipped:
        print(f"Skipped {line}")


if __name__ == "__main__":
    main()
