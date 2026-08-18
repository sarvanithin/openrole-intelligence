# Clean public release

The original repository history contains previously tracked company-list CSVs.
Deleting them in a later commit does not remove their contents from Git history.
Do not make that history public and do not force-rewrite it as part of an ordinary
deployment.

After the launch checklist passes, create a history-free source snapshot:

```bash
./scripts/create_public_release.sh
shasum -a 256 -c dist/openrole-intelligence-*.tar.gz.sha256
```

Then extract the archive into a new empty directory, review it, initialize a new
Git repository, and publish that new repository. The script includes tracked and
untracked non-ignored working-tree files, excludes deleted files, and refuses
known credential, database, environment, and restricted company-list names.

Before publishing, inspect the archive listing and run a secret scanner against
the extracted directory. Import only the synthetic sample data and independently
licensed or public data whose redistribution terms have been reviewed.

The public beta scope must remain precise: explicitly approved sources on the
four supported public ATS connectors, reviewed DOL evidence where present, and
no claim of complete Fortune-company, USCIS, or sponsorship coverage.
