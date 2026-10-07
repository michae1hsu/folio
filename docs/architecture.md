[Folio](../README.md) / [Documentation](README.md)

# Architecture

Folio combines document conversion, hybrid retrieval, and an Agent that can read selected files. A local React workbench and FastAPI backend coordinate cloud services; Qdrant can run embedded or on a separate server.

## A collection becomes an isolated library

Every Drive scan or ZIP upload creates an import batch and a separate knowledge library. The library records its sources and has its own Qdrant collection. Reimporting creates another snapshot; Folio does not continuously synchronize a Drive folder.

- **Google Drive:** the service account reads shared folders and downloads accessible files. Native Google documents are exported to supported formats. The catalog records the source revision and available modification time.
- **ZIP:** the server validates the archive, retains originals locally, and records their SHA-256 hashes as source versions. ZIP modification times are not treated as verified source-revision dates.

Nested source paths remain visible in the catalog and exports. Unsupported files and processing failures stay visible in batch status.

## Processing preserves distinct source and output forms

| Input | Processing | Outputs |
| --- | --- | --- |
| PDF, Office files, images, text, and HTML | Convert to PDF where needed, generate previews, and send the complete PDF in one parsing request. | Page-level Markdown, layout and purpose descriptions, an integrated overview, review markers, and parsing audit records. |
| Audio and video | Use FFmpeg to prepare the complete audio track, transcribe it, then generate a report from the full transcript. | A separate transcript and report. Video processing covers audio, not visual frame analysis. |

LibreOffice handles Office rendering, PyMuPDF supports PDF conversion and previews, Playwright renders HTML, and FFmpeg handles media. Additional Office content such as spreadsheet cells and slide notes is exposed where supported by the converters.

Document parsing checks page count, sequence, required fields, and model-reported coverage. It does not guarantee perfect transcription: compare uncertain text, tables, and diagrams with the original. Inputs or outputs that exceed limits fail visibly instead of being silently split or truncated. See [Formats and limits](formats-and-limits.md).

## Storage keeps generated content tied to its source

Generated Markdown, transcripts, and reports are uploaded to a private Google Cloud Storage bucket. The catalog records content hashes and object generations so later reads can verify the registered content. Local generated Markdown staging is removed after both upload and indexing succeed; originals, previews, and audit data remain local.

Cloud Markdown links are renewable signed URLs. Drive originals retain Drive links; ZIP originals have scoped local download routes. A ZIP library still needs GCS for its generated Markdown.

Parsing, cloud upload, and indexing have separate status fields. A parsed batch may need **Update library** after a storage or indexing failure. See [Using Folio](workflow.md#4-check-the-library).

## Search combines semantic and keyword retrieval

1. Split the generated Markdown into overlapping retrieval chunks and embed them with OpenAI `text-embedding-3-large` at 3,072 dimensions. For media, the report is the indexed representation; the full transcript remains available to read.
2. Store dense vectors and BM25 sparse vectors in the library's Qdrant collection. Sparse tokenization uses Jieba to support Chinese word segmentation alongside other text.
3. Embed the search query and run dense and sparse retrieval within that library. Qdrant combines the rankings using reciprocal rank fusion (RRF).
4. Group retrieved chunks into document candidates. Verify and read each candidate's complete Markdown, then ask the selected provider (TypeSafe JEV or OpenAI Decisions) to grade its relevance to the question.
5. Return document metadata, source variants, available links, and relevance grades. Scoring failures remain explicitly unscored.

**Chunking is for retrieval.** The parsing request still receives the complete converted document, and relevance grading receives the full candidate Markdown. RRF is a ranking score; the grading provider's 0–3 assessment measures relevance, not factual truth. Both adapters use the same four-level rubric. Decisions sends the complete state as text input and validates the ordered answers and level probabilities. Failures and refusals remain unscored, without automatic retries or provider fallback.

## Chat adds file reading to retrieval

Each conversation is bound to one library. Folio creates an OpenAI-hosted Agent environment and gives it two application tools:

| Tool | Responsibility |
| --- | --- |
| `search_file` | Search the current library, grade candidates, and return metadata and available source variants. |
| `fetch_drive_file` | Verify and transfer a registered original, parsed Markdown, transcript, or report into the Agent environment. It supports both Drive and ZIP sources despite its name. |

The Agent uses its environment's reading tools to inspect transferred files. Its instructions require reading evidence before substantive factual answers, treating document instructions as source content, and citing available source links. The activity log exposes retrieval, grading, transfers, and reading commands so users can inspect what happened.

File transfer checks library membership and registered versions. Cloud content reads verify GCS generations and SHA-256. Google credentials remain in the backend; the Agent receives the selected file. A file transfer is not proof of a completed read, and a relevance grade is not proof of an answer's correctness.

## Evaluation closes the review loop

Captured answers can be evaluated by JEV or OpenAI Decisions using the complete question, context, and evidence. The evaluation provider is selected independently from retrieval grading. Each evaluation saves its provider, model, rubric, threshold, and turn evidence; later settings changes do not alter that snapshot. Local request and response records retain the selected provider's payload, and retries reuse completed grading when only prompt suggestions failed.

OpenAI can generate prompt suggestions for low-scoring answers. Automatic evaluation and prompt suggestions are configurable; suggestions do not silently replace a conversation's prompt.

Usage is recorded from provider responses, with unknown usage left unknown. Displayed costs are estimates. These operations send content to cloud providers and may incur charges; see [Data, privacy, and costs](privacy-and-costs.md).

## Code map

| Area | Source |
| --- | --- |
| Interface and API | [`src/`](../src/), [`backend/app.py`](../backend/app.py) |
| Source intake | [`backend/drive.py`](../backend/drive.py), [`backend/zip_import.py`](../backend/zip_import.py) |
| Processing and export | [`backend/jobs.py`](../backend/jobs.py), [`backend/converters.py`](../backend/converters.py), [`backend/models.py`](../backend/models.py) |
| Storage and retrieval | [`backend/gcs.py`](../backend/gcs.py), [`backend/knowledge.py`](../backend/knowledge.py) |
| Agent and reading instructions | [`backend/chat.py`](../backend/chat.py), [`agent_skills/read-drive-files/SKILL.md`](../agent_skills/read-drive-files/SKILL.md) |
| Grading, evaluation, and usage | [`backend/grading.py`](../backend/grading.py), [`backend/evaluation.py`](../backend/evaluation.py), [`backend/usage.py`](../backend/usage.py) |

For local development and verification commands, see [Contributing](../CONTRIBUTING.md).
