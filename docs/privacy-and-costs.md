[Folio](../README.md) / [Documentation](README.md)

# Data, privacy, and costs

Folio runs its interface and backend locally and uses cloud services for document processing, storage, retrieval, and chat.

| Location/service | Data handled |
| --- | --- |
| Local `data/` (or `FOLIO_DATA_DIR`) | Downloaded/uploaded originals, conversions, page previews, staged Markdown, batch catalogs, embedded Qdrant, conversations, evidence snapshots, evaluations, and usage records |
| Google Drive | Read-only access to shared source folders in the normal import flow |
| Private GCS bucket | Generated Markdown, transcripts, and reports; immutable content-addressed paths and recorded generations |
| OpenAI | Complete converted documents/audio, embedding text, reports and prompt-suggestion inputs, chat context, and selected files in an Agent environment; complete candidate documents or answer-evaluation context and evidence when Decisions is selected |
| TypeSafe JEV | Full candidate document text with a relevance question, or answer-evaluation context and evidence, when JEV is selected |

Credentials stay on the backend. Source content is still sent to the providers required by the selected action. Do not upload material you are not authorized to process through those services.

The app retains runtime data until you remove it. Stopping Folio or deleting a checkout does not delete remote GCS objects, provider files, or Agent sessions. Back up local data and the matching GCS objects together if you need reproducible evidence. There is no integrated cross-provider deletion/retention manager; manage those resources separately using their provider tools.

Evaluation snapshots and local grading request/response JSON can contain complete source and conversation content. Keep the runtime directory and exported evaluation logs private. Changing the grading provider does not remove earlier records.

Conversion, embeddings, full-document scoring, chat, evaluation, GCS storage, and downloads can incur charges. Start with the small synthetic ZIP, reduce retrieved candidates when appropriate, and inspect Usage and provider billing. The entire pipeline is not free or offline.

Usage comes from provider responses. Missing usage and unknown model prices remain unknown; failed or refused grading is never recorded as a zero score. Decisions has [dedicated input-only pricing](https://developers.openai.com/api/docs/guides/decisions#pricing-and-availability), with free cache reads, cache writes, and output. Folio applies the published long-context input rate above 272K input tokens and excludes regional processing premiums from its estimate. See [OpenAI pricing](https://developers.openai.com/api/docs/pricing) for current rates and multipliers; displayed estimates are not invoices.
