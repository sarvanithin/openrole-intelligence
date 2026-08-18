# Contributing to OpenRole Intelligence

Thanks for helping make job information more accurate and more transparent.

## First contribution

1. Read the [methodology](docs/METHODOLOGY.md) and the source-safety principles in
   [README.md](README.md).
2. Create a branch from `main` and install the development dependencies:

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   python -m pip install -e '.[dev]'
   pytest -q
   ```

3. Keep pull requests focused. Include tests for behavior changes and update public
   documentation when an operator or contributor workflow changes.

## Contribution areas

### Correct a source or company record

Open a **Source correction** issue with the company name, official career URL, a
short description of the problem, and the public evidence for the correction. Do
not include applicant data, login details, or a URL that requires authentication.

An employer source is not enabled just because a URL looks plausible. It must have
company provenance, an allowed collection path, an attributable source identity,
and a complete-manifest probe. See [docs/METHODOLOGY.md](docs/METHODOLOGY.md).

### Add an ATS connector

Please include:

- a documented or explicitly authorized public endpoint;
- source identity and URL validation that fails closed;
- pagination and complete-manifest behavior;
- rate-limit, error, and empty-manifest handling;
- unit and integration tests; and
- a short policy note in `docs/` when collection terms need explanation.

Never add logic that bypasses authentication, CAPTCHAs, robots controls, rate limits,
or an employer’s technical restrictions.

### Improve sponsorship evidence

Tier A is deliberately strict: it requires a current, role-specific immigration,
visa, or H-1B sponsorship offer. Tier E is an explicit current-posting denial.
Ambiguous, generic, conditional, or conflicting wording belongs in Tier D. Add a
regression test for every rule change in `tests/test_sponsorship.py` and run a full
reassessment before a production release.

## Pull request checklist

- [ ] I ran `pytest -q`.
- [ ] I ran `ruff check src/fortune_intel tests` and `ruff format --check src/fortune_intel tests`.
- [ ] I did not add secrets, local databases, raw job descriptions, licensed lists,
  or applicant data.
- [ ] I added or updated tests for behavior changes.
- [ ] I explained any collection-policy, data-provenance, or evidence-tier impact.

## Code and data boundaries

The code is MIT-licensed. Imported data retains the source’s terms. Please do not
commit `.env` files, credentials, raw DOL exports, local SQLite databases, or
commercial company rankings. The synthetic sample data is the supported way to
develop and test locally.

By contributing, you agree that your contributions may be distributed under the
[MIT License](LICENSE).
