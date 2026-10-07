# Preparing a clean public release

Publishing source code and deploying a publicly reachable service are separate decisions. Folio remains a localhost, single-user application. Keep the repository private while unresolved privacy findings or retained historical objects exist.

## Verify the candidate

1. Use a fresh checkout containing only intended source files. Keep configuration, service accounts, personal documents, conversations, verification logs, and cleanup backups outside it.
2. Install the pinned Gitleaks executable with `python scripts/install_gitleaks.py`. Use GitHub's account-provided noreply email or the owner's explicitly public address `ai.michae1hsu@gmail.com` for author, committer, and tagger identities, including environment overrides.
3. Review the entire staged diff. Run `python scripts/check_secrets.py --worktree`, both scanners with `--staged`, then both with `--history` after committing. Review images and other binary assets separately; automated patterns cannot establish the absence of all personal information.
4. Run the offline test suite and frontend build described in [Contributing](../CONTRIBUTING.md). Audit the installed Python environment and locked npm dependencies, and resolve findings relevant to the release.
5. Review every remote branch and tag, pull request, issue/comment, release asset, Actions run/log/artifact, wiki, and Pages deployment. Local object scanning cannot inspect GitHub's retained objects, external forks, or other people's clones.
6. Decide Folio's own license separately and preserve [third-party notices](../THIRD_PARTY_NOTICES.md). No Folio license is chosen by this security checklist.

## Remove historical exposure

Removing a file or changing Git configuration does not change earlier commits. Rewrite every affected branch/tag or publish a new clean root, using an explicit force-with-lease against the reviewed remote head. Coordinate collaborators so they do not restore old history. Retain any necessary cleanup backup privately and never push its audit refs.

Remove obsolete Actions logs/artifacts and other affected repository records. Then inspect known old commit URLs, GitHub commit APIs, and SHA fetches in an isolated private audit directory. A rewritten default branch does not prove that the old objects have disappeared.

If previously reachable objects remain accessible, keep the repository private and follow [GitHub's sensitive-data removal process](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository). Server-side removal may require GitHub Support, and GitHub determines which requests it can process. Rotate/revoke any exposed credentials regardless of history cleanup. Verify again after the removal is confirmed.

Document the checked refs, scanner versions, test results, remaining reachability, and limitations in a private redacted audit report. Do not place affected personal identities or credential values in a public issue, commit message, or cleanup report.
