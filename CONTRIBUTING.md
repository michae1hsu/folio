# Contributing to Folio

Thanks for helping improve Folio. Start with the [project overview](README.md), [installation guide](docs/getting-started.md), and [architecture guide](docs/architecture.md).

## Report a problem

Open an issue with the behavior you expected, what happened, and steps to reproduce it. Include your operating system and relevant dependency versions. Use a small sample that contains no private material; omit credentials, signed URLs, and personal documents from logs or screenshots. For security vulnerabilities, follow [SECURITY.md](SECURITY.md).

## Set up a development environment

Clone the repository and run the platform script in [Getting started](docs/getting-started.md). The project lives at the repository root:

```text
backend/          API, conversion, Drive/ZIP intake, storage, retrieval, chat
src/              React interface
agent_skills/     Source-reading instructions installed in the Agent environment
tests/            Synthetic offline unit and integration tests
scripts/          Setup, sample generation, and secret guard
examples/         Empty Google JSON structure
docs/             User guides, architecture, and project artwork
vendor/tiktoken/  Bundled tokenizer vocabulary and its license
.githooks/        Index, commit-message, and pre-push guards
.env.example      Blank credential configuration template
```

Start the backend on port 8765 and run `npm run dev` for frontend development. Vite proxies API requests to the backend. After changing the interface, run `npm run build` and restart/reload the app to use the production build.

Setup installs Gitleaks 8.30.1 after verifying the release archive against a pinned SHA-256. If you did not use setup, run `python scripts/install_gitleaks.py` and `git config core.hooksPath .githooks`. The downloaded executable stays in ignored `.tools/`. Coding agents can use [AGENTS.md](AGENTS.md) for repository conventions.

Use the exact GitHub noreply address shown in your account's email settings for local `git config user.email` to keep your personal email private. The owner has explicitly chosen `ai.michae1hsu@gmail.com` as a dedicated public contact address. The guard accepts GitHub noreply identities and that exact address for authors, committers, and annotated tags; other personal addresses remain blocked. Check environment overrides as well as Git configuration.

## Preserve application behavior

- Keep the interface, documentation, and operational messages in English. Preserve source-language transcription.
- Use synthetic inputs for development. Never commit real documents, API keys, Google credential JSON, runtime data, conversations, signed links, or local verification logs.
- Preserve complete-document processing and explicit failures. Do not silently truncate inputs or substitute models.
- Keep library boundaries, source hashes, and GCS generations enforced for retrieval and file reads.
- Automated tests use temporary data and mocked providers. Keep real API calls and cloud changes out of the test suite.

## Verify a change

Run the relevant tests and `npm run build` from the repository root. Use the project's virtual environment. Full offline verification commands follow.

### Windows / PowerShell

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
npm run build
.\.venv\Scripts\python.exe scripts\check_secrets.py --worktree
.\.venv\Scripts\python.exe scripts\check_secrets.py --staged
.\.venv\Scripts\python.exe scripts\check_secrets.py --history
.\.venv\Scripts\python.exe scripts\run_gitleaks.py --history
```

### Linux / macOS

```bash
PYTHONUTF8=1 .venv/bin/python -m unittest discover -s tests -v
npm run build
.venv/bin/python scripts/check_secrets.py --worktree
.venv/bin/python scripts/check_secrets.py --staged
.venv/bin/python scripts/check_secrets.py --history
.venv/bin/python scripts/run_gitleaks.py --history
```

Tests use synthetic documents, temporary data directories, and mocked remote providers; they make no billable API calls. Integration coverage includes ZIP intake, text/PDF conversion, local Qdrant, indexing, scoped source downloads, hash validation, Agent file transfer, and Markdown export. Provider credentials and permissions can be checked through the [connection controls](docs/configuration.md) when deliberately testing a configured deployment.

Review the staged diff before committing. The pre-commit hook scans the entire index and effective author/committer identities. The commit-msg hook scans the proposed message and identities. The pre-push hook scans every stored local Git blob, commit, and annotated tag, including unreachable objects left after an amend or reset. It checks every stored file path, even when the same blob appears at an allowed and forbidden path. GitHub Actions scans its full checkout history, runs the backend suite, and builds the frontend.

Both scanners are mandatory in hooks and CI. The Python guard rejects common keys, private identities/email, signed URLs, private Drive links, runtime paths, and source documents. Synthetic example domains, GitHub noreply identities, and the owner's approved public address are accepted; tests do not have a blanket exemption. [Gitleaks](https://github.com/gitleaks/gitleaks) adds provider rules, encoded-content decoding, and archive inspection. Its wrapper suppresses raw output and prints only rule names and counts. A missing or failed scanner blocks the operation. Do not bypass hooks or add a baseline to hide a real finding.

`.gitignore` excludes local configuration, credentials, runtime data, source documents, scanners, and build artifacts. A clean working tree alone does not prove clean history. If old sensitive objects remain, follow [the release cleanup guide](docs/releasing.md), use a freshly verified clone after a rewrite, and handle retained remote objects before publishing.

## Submit a pull request

Keep each change focused on one problem. Describe the problem, resulting behavior, and verification performed. Update documentation when configuration or behavior changes. Add tests for changed behavior where they help catch regressions, and use synthetic content for screenshots or examples.

When changing dependencies, update the versions and license information in [Third-party software and licenses](THIRD_PARTY_NOTICES.md). Preserve required notices for bundled material and dependencies included in distributed builds.
