# Working on Folio

Repository conventions for coding agents. See [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow.

- Keep the interface, documentation, operational messages, and default prompts in English. Preserve source languages when transcribing.
- Read README.md for the product overview and docs/README.md for setup, architecture, and product behavior. The app lives at the repository root.
- Preserve full-document fidelity: never silently summarize, omit pages, split a document into model calls, or truncate audio. Save transcripts separately from reports.
- Keep each Drive or ZIP import in its own knowledge library. Validate library/file authorization, GCS generation, and SHA-256 before reading content.
- Never commit real credentials, .env files, service-account JSON, data/, private documents, chat history, or local verification output. Do not print secrets.
- Use explicit configured models. Do not silently switch models or create credentials.
- Automated checks use temporary data, synthetic inputs, and mocked providers. They must not send paid requests or modify Drive/GCS.
- Start the service on 127.0.0.1:8765. There is no multi-user authentication; do not expose it publicly during development.
- Run relevant tests and npm run build. Before a commit, scan the index and effective identities with python scripts/check_secrets.py --staged and python scripts/run_gitleaks.py --staged. Before pushing, run both scanners with --history. Keep commit-msg hooks enabled. Use GitHub noreply identities or the owner's explicitly public address ai.michae1hsu@gmail.com, and never bypass a failed or missing scanner.

On Windows use .venv/Scripts/python.exe and start.ps1. Set PYTHONUTF8=1 and PYTHONIOENCODING=utf-8. On Linux/macOS use .venv/bin/python and scripts/start.sh. Do not share a virtual environment between operating systems.
