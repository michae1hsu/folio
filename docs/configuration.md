[Folio](../README.md) / [Documentation](README.md)

# Configuration

Configure API credentials, Google access, storage, and models. Start with [Getting started](getting-started.md) if Folio is not installed yet.

There are **two distinct configuration artifacts**:

1. **`.env`** contains API keys, model choices, and paths. Create it from [`.env.example`](../.env.example) and fill in your own settings.
2. **A downloaded Google service-account JSON** contains Google's identity and private key. Keep the real file outside the checkout. [The structural example](../examples/google-service-account.example.json) only shows its shape and cannot authenticate.

Never put credentials in frontend code, a `VITE_` variable, screenshots, sample files, issue reports, or Git. Do not paste the JSON or its private key into `.env`; set its filesystem path instead. On a shared machine, restrict file permissions to your user.

Folio loads only `.env` in this repository's root. It does not load a parent directory's `.env`. Process environment variables take precedence over `.env`. Restart the backend after changing environment values.

## OpenAI and JEV

Set `OPENAI_API_KEY` locally, and set `JEV_API_KEY` if you select JEV for grading. `FOLIO_OPENAI_DIRECT_KEY`, when nonempty, takes precedence over `OPENAI_API_KEY`. The backend uses the OpenAI SDK; Azure credentials and a ChatGPT subscription are not substitutes for an OpenAI API key with the necessary API access. Conventional SDK settings such as `OPENAI_BASE_URL` and HTTP proxy environment variables remain in effect if inherited. The credential override does not bypass a proxy; use only endpoints you trust with these credentials and source contents.

Model names are explicit in the example. Verify access for your API project and set each model deliberately. Folio does not silently substitute a different model when one is unavailable. Chat uses the [OpenAI Agents API](https://developers.openai.com/api/docs/guides/agents-api/overview) with an OpenAI-hosted environment. An OpenAI-compatible chat endpoint alone is insufficient.

Grading defaults to **JEV** (`JEV_MODEL`). Select **OpenAI Decisions** in **Document search**, in **File grading for new conversations** or **Conversation file grading**, and in **Evaluations → Answer grading provider**. Answer evaluation has its own selection, independent of conversation retrieval. Decisions uses the existing OpenAI credential and the fixed `gpt-6-luna` model; there is no separate Decisions key or model environment variable. Your project and endpoint must support [the Decisions API](https://developers.openai.com/api/docs/guides/decisions), including `POST /decisions` when using a proxy.

Without JEV, parsing and indexing can still run, and grading can use Decisions when selected and accessible. Scoring failures, refusals, and oversized inputs remain explicitly unscored. Folio does not truncate input, retry automatically, or switch grading providers. To avoid automatic evaluation attempts until your selected provider is configured, turn off automatic evaluation in **Evaluations**.

## Google service account and GCS

1. Create or choose a Google Cloud project. Enable Google Drive API and Cloud Storage API. Enable billing for storage use.
2. Create a service account and download a JSON key if your organization's policy allows it. Store it in a private directory outside this repository. Follow [Google's service-account key guidance](https://cloud.google.com/iam/docs/best-practices-for-managing-service-account-keys).
3. Set `GOOGLE_SERVICE_ACCOUNT_FILE` to its absolute path. Forward slashes work on Windows, such as `C:/credentials/folio-service-account.json`; paths containing spaces should be quoted in `.env`.
4. Create a private GCS bucket. Keep public access prevention enabled. Grant the service account **Storage Object User** on that bucket; avoid project-wide administrative permissions.
5. Set `GCS_BUCKET` to the bucket name, without `gs://` or an object path.
6. For **Drive import**, share the source folder with the service-account email as a **Viewer** and allow downloads. A folder link does not grant access by itself. Native Google Docs, Sheets, Slides, and Drawings are exported to supported file formats.
7. In **Connections & models**, save the credential-file path and test Google access. In **Libraries → Cloud Markdown**, save and test the GCS bucket.

The same Google identity is used for GCS when importing ZIP files. ZIP import does not require a shared Drive folder.

Settings saved in the UI are stored under `data/`: `drive-settings.json` overrides `GOOGLE_SERVICE_ACCOUNT_FILE`, and `gcs-settings.json` overrides `GCS_BUCKET`. To return to environment configuration, stop Folio and remove only the corresponding local settings file. The UI stores the JSON **path**, not a copy of its key.

GCS Markdown links are signed bearer links valid for approximately 24 hours. Anyone with one can open that object until expiry without signing into Google. Keep them private; use **Refresh links** or search/fetch again to renew them. Drive links still require Drive permissions. ZIP originals have local download links that work only on the machine running Folio.

## Environment reference

| Variable | Default / purpose |
| --- | --- |
| `OPENAI_API_KEY` | Blank; OpenAI API credential |
| `FOLIO_OPENAI_DIRECT_KEY` | Blank; optional credential override |
| `DOCUMENT_MODEL` | `gpt-6-luna`; whole-document parsing |
| `TRANSCRIPTION_MODEL` | `gpt-transcribe`; complete audio transcription |
| `REPORT_MODEL` | `gpt-6-luna`; media reports and evaluation prompt suggestions |
| `AGENT_MODEL` | `gpt-6.1-sol`; default for new conversations |
| `JEV_API_KEY` | Blank; TypeSafe relevance and evaluation credential, needed only when JEV is selected |
| `JEV_MODEL` | `jev-1.13.0` |
| `TYPESAFE_API_KEY` | Optional legacy fallback when `JEV_API_KEY` is empty |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | Blank; path to the real Google JSON file |
| `GCS_BUCKET` | Blank; private bucket for generated Markdown |
| `QDRANT_URL` | Blank; embedded mode when absent |
| `QDRANT_API_KEY` | Blank; optional remote Qdrant credential |
| `QDRANT_COLLECTION` | `folio_text_v1`; base prefix for isolated library collections |
| `FOLIO_DATA_DIR` | `./data`; optional absolute private runtime directory |

Embeddings use `text-embedding-3-large` with 3,072 dimensions. This is fixed in `backend/config.py`; changing the embedding model or dimensions requires rebuilding compatible indexes. For a remote Qdrant server, use a private endpoint compatible with the declared client dependency and native RRF/BM25 functionality. Do not run two app processes against the same embedded Qdrant directory.
