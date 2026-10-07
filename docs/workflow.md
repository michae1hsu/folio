[Folio](../README.md) / [Documentation](README.md)

# Using Folio

Follow a collection from import to reviewed Markdown, document search, and evaluated answers. Complete [setup](getting-started.md) and [configuration](configuration.md) first. Run sample commands from the repository root.

## 1. Import your sources

Open **Workbench → Add your files** and choose one source:

- **Google Drive:** paste a folder URL or ID, choose whether to include subfolders, and click **Read folder**. This scans/downloads accessible source files and creates a batch.
- **Upload ZIP:** choose or drop a ZIP file and click **Import ZIP**. The server validates it before retaining extracted originals. No Drive folder is needed.

Each import creates a new isolated library. Reimport after source changes; libraries are snapshots, not continuously synchronized folders. ZIP source versions use SHA-256 and do not claim a verified modification date.

To try the workflow, generate a ZIP containing two sample project notes:

```powershell
# Windows
.\.venv\Scripts\python.exe scripts\make_sample_zip.py
```

```bash
# Linux/macOS
.venv/bin/python scripts/make_sample_zip.py
```

Upload `data/samples/folio-sample.zip`. The generator refuses to overwrite an existing file. Using it through the real processing workflow makes billable API calls; running the offline test suite does not.

## 2. Process the batch

Review the discovered file list and choose **Start processing**. Document/image inputs are converted to PDF and parsed. Audio/video inputs are converted to a complete audio track, transcribed, and then turned into a report.

You can switch views while work continues. Cancellation stops further work after in-flight calls return; it cannot undo a provider request already submitted. Resume eligible interrupted or failed batches from the workbench. Completed files are reused where possible.

## 3. Review and export

Open a processed file to compare its PDF/page preview with Markdown. Inspect **Needs review**, warnings, missing text, and uncertain visual details before relying on the result. Original language is preserved in transcriptions; descriptions, reports, and the interface default to English.

Use **Outputs** or the batch download action to export:

| Archive path | Contents |
| --- | --- |
| `markdown/` | Full Markdown per document; reports for media |
| `text/` | Separate media transcripts, reports, and report metadata |
| `audit/` | Complete parsing response records when available |
| `manifest.json` | Source entries, page/status information, warnings, and errors |
| `README.md` | Export layout explanation |

Exports include source content and metadata. Review them before sharing them with others.

## 4. Check the library

Parsing triggers a library build. Open **Libraries**, select the import, and check **GCS upload** and **Qdrant index** status. A completed parsing batch does not prove its library build succeeded.

If Google permissions, billing, or a provider request failed, fix the configuration and choose **Update library**. Completed parsing and verified cloud objects are reused. Local generated Markdown staging is removed only after verified cloud upload; originals and audit data remain local. Keep GCS available for later previews, exports, searches, and Agent file reads.

## 5. Search

Open **Document search** or **Search this library**. Enter a question, select a retrieval limit, and choose **Grading provider**: JEV (default) or OpenAI Decisions. Folio retrieves chunks within that library, combines ranks, groups candidates by file, and grades each candidate's **complete Markdown** with the selected provider.

For the synthetic sample, ask: `What is the project budget and deadline?`

RRF is a rank-fusion score, not a probability. Grades are model assessments of relevance on a 0–3 scale, not proof that a document or answer is true. Results identify the grading provider and model. A missing grade means evaluation failed, was refused, or is unavailable; it does not mean irrelevance.

## 6. Ask the Agent

Open **Document chat**, choose the same library, an available Agent model, and **File grading for new conversations**, then create a conversation. Ask the sample question or request a comparison between files. Every conversation stays bound to one library. You can change **Conversation file grading** in conversation settings while idle; the choice applies to future turns.

The activity log distinguishes search, scoring, source transfer, and reading commands. Transferring a file does not prove that its contents were read. The Agent can request the original, full parsed Markdown, transcript, or report; reads verify registered library membership, source hashes, and pinned GCS generations. ZIP originals are transferred from the local server into the Agent environment without exposing Google credentials.

Prompt settings support defaults for new conversations and revisions for an existing conversation. A changed prompt may require a new Agent session; prior evidence and the revision history remain inspectable.

## 7. Inspect evaluations and costs

**Evaluations** shows captured questions, answers, conversation context, evidence, scores, and prompt suggestions. Choose the **Answer grading provider** and click **Save settings**. This selection is independent of search and chat retrieval; saved evaluations keep their original provider, model, and threshold. Automatic evaluation is enabled by default; automatic prompt suggestions are also enabled for answers below the configured threshold. Both can be disabled in the UI. Suggestions do not silently replace the conversation prompt.

Evaluation sends complete captured context and evidence to the selected grading provider; prompt optimization sends it to OpenAI. These are additional billable operations. **Usage** identifies the provider and model, and estimates costs from recorded provider usage and bundled pricing rules. Decisions uses its dedicated input-only pricing instead of ordinary chat-model rates. Unknown usage remains unknown, and displayed estimates are not your provider invoice.

See [Supported formats and limits](formats-and-limits.md) when preparing inputs, or [Troubleshooting](troubleshooting.md) when a stage fails.
