# Security

## Report a vulnerability

Use GitHub private vulnerability reporting when available, or contact the repository owner privately. Include a minimal reproduction, affected versions, expected behavior, and the security impact. Use synthetic data and remove credentials, personal documents, and signed URLs from the report.

## Deployment model

Folio is a local, single-user workbench. There is no user authentication or tenant access-control system. Use its localhost launcher on a trusted machine. Library scoping, host validation, and the write-request header protect application invariants; they do not make it a publicly deployable service.

## Credentials and data

Keep API credentials in the ignored root `.env` or process environment. Keep the Google service-account JSON outside the checkout with restricted permissions. Use the committed example files as templates, and keep real configuration out of Git.

Imported content, conversations, evaluation evidence, usage records, and signed GCS URLs are sensitive. Review the [data-flow and retention information](docs/privacy-and-costs.md). Never include them in a public issue or screenshot.

Revoke or rotate an exposed credential promptly. Removing it from the current file does not remove copies from Git history, logs, or exported artifacts.

Mandatory local hooks and CI use the Python privacy guard and a pinned, checksum-verified Gitleaks executable. They scan source, the complete staged index, proposed commit messages, identities, and all stored local Git objects including unreachable history. Reports omit matching values. Commit/tag email is restricted to GitHub noreply identities and the owner's explicitly public address `ai.michae1hsu@gmail.com`. Other personal email, signed URLs, private Drive links, credential files, and source/runtime data paths are blocked.

These checks cannot identify all confidential prose, binary-image metadata, or every provider token format. They cannot purge GitHub's retained commits or copies outside the checkout. Review assets manually and follow [the release cleanup guide](docs/releasing.md) before publication. Manual review and provider/GitHub controls remain necessary.
