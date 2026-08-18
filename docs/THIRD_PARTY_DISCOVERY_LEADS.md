# Third-party discovery leads

Third-party career and ATS lists are planning inputs. They are not evidence that a
company currently owns a career board, that the board is complete, or that collection
and redistribution are permitted.

## Admission gates

1. **License review:** an operator must record `license_status=permitted`, the license
   identifier and URL, review time, and actor. A public repository without a license is
   not assumed reusable. Database and individual-content rights may differ, so the
   operator must review both when applicable.
2. **Immutable provenance:** retain the dataset name, source record identifier, download
   URL, SHA-256 file checksum, retrieval time, and responsible actor. This follows the
   W3C PROV separation of source entity, import activity, and responsible agent.
3. **Exact identity:** `company_id` and the exact stored `company_name` must agree. The
   importer never searches, normalizes, or fuzzy-matches a third-party company name.
   Prefer an independently reviewed identifier such as SEC CIK or LEI when preparing the
   mapping into the canonical company universe.
4. **Bounded URL validation:** only public HTTPS URLs matching a strict supported ATS,
   policy-held ATS, or bounded career-URL pattern are retained. Import performs no HTTP.
5. **Isolation:** leads are passive fingerprints with explicit unverified and
   non-activatable evidence. They do not change coverage or create source candidates.

The later promotion path is deliberately separate: verify the ATS handoff from the
company's official primary website, import it through `import-source-candidates`, review
robots and terms, and require a complete connector manifest before activation.

## Authoritative references

- Creative Commons BY 4.0 includes attribution conditions and addresses database rights:
  <https://creativecommons.org/licenses/by/4.0/legalcode.en>
- Open Data Commons explains that database rights and rights in database contents can be
  separate: <https://opendatacommons.org/licenses/odbl/1-0/>
- W3C PROV-O defines provenance entities, activities, agents, derivation, and primary
  sources: <https://www.w3.org/TR/prov-o/>
- The SEC describes CIK as a unique filer identifier:
  <https://www.sec.gov/submit-filings/filer-support-resources/how-do-i-guides/look-central-index-key-cik-number>
- GLEIF describes LEI as a unique legal-entity identifier linked to authoritative
  reference data:
  <https://www.gleif.org/en/organizational-identity/lei-vlei/the-legal-entity-identifier-lei>
