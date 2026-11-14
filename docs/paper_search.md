# Paper search (`search_papers`)

The agent explains every verdict from the PSI papers: "tested at Moon conditions, see FLEX Test Plan p. 6". `search_papers` is how it finds those passages. It takes a question in plain English and returns the five passages that best answer it, each with its study, document and page, so the verdict can cite them.

This is the **retrieval** half of RAG (retrieval-augmented generation): find the evidence first, then let the language model write from it. The agent that writes the verdict is not built yet; this document covers everything up to the passages it will receive.

```python
from src.agent.search import search_papers

hits = search_papers("CO2 dilution putting out droplet flames")            # all studies
hits = search_papers("flame spread limits", psi_ids=["PSI-98", "PSI-99"])  # only these studies

hits[0]  # PaperHit(psi_id='PSI-117', doc_title='Dissertation ...', page=74, text='...', score=0.98)
```

| Field | Meaning |
| --- | --- |
| `psi_id` | Study the document is filed under |
| `doc_title` | Document title, from the file name |
| `page` | PDF page or PowerPoint slide; `None` for Word files (no fixed pages) |
| `text` | The passage, about 180 words |
| `score` | Relevance from 0 to 1, given by the re-ranker |

## The big picture

```
OFFLINE, once (maintainers)                          ONLINE, every question (~1.5–2 s)

psi_data/ (PDF, pptx, docx)                          question
   │  scripts/make_chunks.py  (10–15 min)               │  embed with bge-small (+ query prefix)
   ▼                                                     ▼
data/processed/chunks.parquet  ── committed ──┐    1. SHORTLIST, one SQL query
   │  make load-chunks  (seconds)             │       meaning:  50 nearest embeddings
   ▼                                          │       keywords: 50 best full-text matches
psi_dashboard.chunks (Postgres + pgvector) ◄──┘       merged with reciprocal rank fusion → ~100
                                                        │
                                                     2. RE-RANK, cross-encoder reads question + passage
                                                        │
                                                        ▼
                                                     top 5 passages → agent writes the verdict
```

## Offline: from documents to a searchable table

### 1. Chunking (`scripts/make_chunks.py`)

A whole document is too long to compare with a question, so each one is cut into **chunks** of about 180 words, overlapping by 30 so a sentence cut at the edge still appears whole in the next chunk.

- **A chunk never crosses a page.** Every hit then has one exact page to cite.
- **Formats:** PDFs (page = PDF page), PowerPoint `.pptx` (page = slide) and Word `.docx` (page = `None`). Word files matter: for the Saffire studies they hold most of the readable text. Old `.doc`/`.ppt` files and image-only PDF pages (no text layer, no OCR) are skipped.
- **A paper filed under two studies is kept once per study**, so filtering by `psi_id` still finds it. A file filed twice in the *same* study is kept once.
- **Text cleanup:** control characters are removed (Postgres cannot store NUL bytes, and some PDFs contain them) and words broken across lines are rejoined.
- **Noise filter:** reference lists, software logs and tables of contents are dropped (608 chunks, 3.7%). They are packed with topic words, so they used to rank high while explaining nothing. The thresholds were measured on this corpus: 8% or more of the words are citation markers (years, "et al", "pp.", "[12]"), or three or more runs of dot leaders.

Result: 298 documents → **15,752 chunks**.

Adding Word/PowerPoint and per-study copies, without the header below, was measured as 2/5 on the test (ranks not recorded).

### 2. Embedding

An **embedding** turns a text into a list of numbers, here 384, that act as coordinates on a "map of meaning": texts about the same thing land close together, even with different words. Search then means: put the question on the map and take the nearest passages.

- **Model:** `BAAI/bge-small-en-v1.5` (`src/agent/embeddings.py`). Small, free, runs on a laptop, and the issue fixed `vector(384)`.
- **Query prefix:** bge expects `"Represent this sentence for searching relevant passages: "` in front of questions and nothing in front of passages. Without it, retrieval is noticeably worse.
- **Context header:** a page rarely names its experiment, so each chunk is embedded as `"<study acronym (title)>. <document title>: <text>"`, e.g. `"SAFFIRE-II (Spacecraft Fire Experiment-II). SAFFIRE-II Readme: ..."`. Only the embedding sees the header; the stored `text` stays as written. On the test questions this took "cool flames" from a fail to a pass, but pushed FLEX and Saffire further down.

### 3. Storage and loading

- **`chunks.parquet` is committed** (`data/processed/`, ~43 MB). It holds every chunk *with* its embedding, so nobody has to re-embed: `make load-chunks` empties `psi_dashboard.chunks` and reloads it in about 5 seconds, in one transaction (a failure leaves the old rows), and checks that the row counts match. `make setup` runs it too.
- **Rebuilding the parquet** (`.venv/bin/python scripts/make_chunks.py`) is a maintainer step, needed only when the documents, the chunking or the model change. It asks for confirmation; an accidental run is undone with `git checkout data/processed/chunks.parquet`.
- **Postgres with pgvector** holds the chunks next to the rest of the data, so one query can filter by study and rank by similarity. The table also has a `text_search` column (the words of the title and text, maintained by Postgres) with a GIN index, for the keyword side of the search.
- **No vector index:** 15,752 rows are scanned exactly in milliseconds. Add an HNSW index only if searches get slow.

## Online: answering a question (`src/agent/search.py`)

### Stage 1 — shortlist (hybrid search, one SQL query)

Two rankings of the same chunks, each keeping its best 50:

- **Meaning:** cosine distance between the question's embedding and each chunk's (`embedding <=> query`). Finds passages that say the same thing in other words.
- **Keywords:** Postgres full-text search. Any of the question's words may match; chunks with more matching words, closer together, rank higher. Catches exact terms the embedding blurs: "CO2", "SF6", acronyms.

The two lists are merged with **reciprocal rank fusion (RRF)**: each list gives a chunk `1 / (60 + its rank)`, and the points add up. A chunk near the top of either list, or fairly high in both, makes the shortlist of up to 100. RRF only uses ranks, so the two scores (a distance and a text rank) never need to be put on the same scale.

The optional `psi_ids` filter applies to both lists.

### Stage 2 — re-rank (cross-encoder)

The embedding compares two points on a map: fast, but it squeezes 180 words into 384 numbers and loses details. A **cross-encoder** instead reads the question and one passage *together* and scores how well the passage answers it. It is far more precise, and far too slow to run on 15,752 chunks, so it only re-orders the shortlist. Think of hiring: stage 1 screens CVs, stage 2 interviews the shortlist.

- **Model:** `cross-encoder/ms-marco-MiniLM-L-6-v2` (~90 MB, downloaded once, loaded once per process).
- Its raw scores are unbounded; a sigmoid maps them to `score` in 0–1 without changing the order.
- **Cost:** a search takes about 1.5–2 s on a laptop CPU, most of it in the re-ranker.

## Why these choices: what we measured

`tests/test_search.py` asks five questions and checks that the right study appears in the top 5. The rank of the first correct passage, step by step:

| Step | Oxygen / solids | CO₂ dilution | Smoke detectors | Cool flames | Saffire | Pass |
| --- | --- | --- | --- | --- | --- | --- |
| Meaning only, PDFs only | 1 | 8 | 1 | 7 | 63 | 2/5 |
| + Word/PowerPoint, per-study copies, context header | 1 | 36 | 1 | 4 | 97 | 3/5 |
| + noise filter | 1 | 35 | 1 | 4 | 91 | 3/5 |
| + keyword search (hybrid) | 1 | 34 | 1 | 2 | 181 | 3/5 |
| + re-ranking, widened expectations | 1 | 1 | 1 | 1 | — | **4/5** |

Tried and dropped:

- **Showing identical text only once** (a paper filed under three studies takes three of five places). Keeping one copy hid the other studies: "cool flames" lost CFI because its slide deck is also filed under FLEX. Dropped: 2/5.
- **A larger re-ranker** (`BAAI/bge-reranker-base`): same results, ~12 s per search.
- **A larger shortlist** (up to 300): Saffire gets in, but only reaches #9–#11, and a search takes 3–4 s.

The issue's expected studies were **widened for two questions**, from each study's own `investigation.json`: PSI-117 and PSI-142 are ground studies of FLEX flight data (PSI-117 covers diluents, both cover cool flames), FLEX-2 (PSI-68) is the same programme as FLEX, and CFI-G (PSI-159) is the "Cool Flames Investigation with Gases". The search was finding those studies; the test was rejecting correct answers.

## Known gap: Saffire

"Large-scale flame spread in a spacecraft" still misses the Saffire studies (PSI-98/99/100) without a filter. The expectation is right: that sentence describes Saffire. The causes are in the data and in how both rankings treat the one decisive word:

- Saffire has little text: ~110 chunks across its three studies, most of them one shared 6-page paper, against 3,000+ for some ACME studies. Hundreds of passages elsewhere also discuss flame spread on the ISS, which is a spacecraft too.
- "Large-scale" is what makes Saffire special, and both stage-1 rankings underweight it: the embedding blends it into the passage's overall meaning, and Postgres's full-text rank has no notion of rare words (no IDF), so the rare "large scale" counts no more than the ubiquitous "flame".
- A page of the "ISS Researchers Guide" is filed under three ACME studies and takes three of the five places.

When the agent knows which studies matter (the nearby runs of a scenario), it passes their `psi_ids`, and the filtered search finds the Saffire passages directly. Possible fixes for the unfiltered case: keyword ranking with IDF (BM25, via a Postgres extension such as ParadeDB's `pg_search`), or rewriting the question with the language model before searching ("Saffire large-scale spacecraft fire experiment").

## Knobs

| Setting | Where | Value | Effect |
| --- | --- | --- | --- |
| `WORDS_PER_CHUNK`, `OVERLAP_WORDS`, `MIN_WORDS` | `scripts/make_chunks.py` | 180, 30, 15 | Chunk size; changing them means re-embedding |
| `MODEL_NAME`, `QUERY_PREFIX` | `src/agent/embeddings.py` | bge-small-en-v1.5 | Changing the model means re-embedding and a new `vector(N)` |
| `CANDIDATES` | `src/agent/search.py` | 50 | Chunks each ranking contributes; the shortlist holds up to twice this |
| `RRF_K` | `src/agent/search.py` | 60 | Fusion constant; larger flattens the gap between ranks |
| `RERANKER_NAME` | `src/agent/search.py` | MiniLM-L-6 | Precision vs. speed of stage 2 |

## Commands

| Command | Time | What |
| --- | --- | --- |
| `make load-chunks` | ~5 s | Load the committed parquet into Postgres |
| `make test` | ~20 s | Includes `tests/test_search.py` (skipped when the table is empty) |
| `.venv/bin/python scripts/make_chunks.py` | 10–15 min | Rebuild the parquet; maintainers only, asks for confirmation |
