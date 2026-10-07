[Folio](../README.md) / [Documentation](README.md)

# Troubleshooting

Find the failing stage in the workbench or library view, then use the checks below. See [Configuration](configuration.md) for credentials, permissions, and models.

| Symptom | What to check |
| --- | --- |
| Start processing says a key is missing | Set the backend OpenAI key in this root's `.env`; restart. Azure variables and parent `.env` files are not loaded. |
| Drive folder is not found or download is denied | Share it with the configured service-account email; permit downloads; verify shared-drive policies and the folder/resource key. |
| ZIP import is rejected | Check sizes, file count, encryption, compression, duplicate names, and unsafe paths. Recreate a normal ZIP with the supported files. |
| Parsing completed but library is incomplete | Test GCS, billing, and embedding access, then choose **Update library**. Parsing and indexing have independent statuses. |
| GCS permission/billing failure | Check the bucket exists, billing is enabled, and the service account has Storage Object User on that bucket. |
| A saved JSON path or bucket keeps overriding `.env` | UI settings in `data/drive-settings.json` or `data/gcs-settings.json` take precedence. Update them in the UI. |
| Office conversion or media processing fails | Check LibreOffice, FFmpeg/ffprobe, required fonts, and source validity. Restart after changing PATH. |
| Browser executable missing | Run the environment's `python -m playwright install chromium`; on Linux install browser system dependencies too. |
| Missing pages or incomplete model output | Inspect the error/audit record. Retry the full document deliberately or prepare a smaller source yourself; Folio does not silently page-split. |
| Search finds files but scores are unavailable | Check the selected grading provider, its backend credential, quota/model access, and the per-file scoring error. JEV needs its own key; Decisions uses the configured OpenAI key. |
| Decisions returns 404 or rejects a request | Verify your project and endpoint support `POST /decisions` and `gpt-6-luna`. A compatible chat-only proxy is insufficient. Complete-input limits and refusals remain unscored; Folio does not truncate or switch providers. |
| Chat/model access fails | Verify the configured model and Agents API hosted-environment access. Availability is specific to your API project. |
| A source hash or GCS generation fails verification | Preserve the evidence; reimport/rebuild from the intended revision. Do not edit stored hashes to bypass the check. |
| A cloud link expired | Use **Refresh links**, search again, or fetch the variant again. Do not edit signed URL parameters. |
| Embedded Qdrant is locked | Stop the other Folio process using the same data directory. |
| Port 8765 is occupied | Stop the previous server or use a separate development setup with an explicitly adjusted proxy/port. |
