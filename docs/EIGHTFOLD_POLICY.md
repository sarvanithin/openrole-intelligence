# Eightfold connector policy hold

Status: **candidate inventory only; recurring ingestion is disabled**.

The platform records exact outbound Eightfold career-board URLs, but it does not
derive tenant URLs, query undocumented browser endpoints, automate a browser, or
register Eightfold with the scheduler.

## Official evidence

- Eightfold's [API authorization guide](https://apidocs.eightfold.ai/docs/eightfold-api-authorization-guide)
  says API keys are generated in the tenant Admin Console and Position reads
  require `Position:READ` permission.
- The official [List Positions reference](https://apidocs.eightfold.ai/reference/list_position)
  requires an `Authorization` bearer token.
- Eightfold's [API documentation](https://apidocs.eightfold.ai/docs/getting-started)
  documents authenticated list pagination with `start` and `limit`, but does not
  document an anonymous career-board complete-manifest API.
- Eightfold describes its public-facing product as a
  [Career Site](https://eightfold.ai/wp-content/uploads/Drive_More_Hires_And_Improve_Your_Employer_Brand_With_A_Talent_Experience_Solution.pdf),
  not as a public data API.

The visible career application uses parameters such as `start`, `pid`, and
`domain`, but those UI behaviors are not treated as an API contract. Without a
documented anonymous endpoint and collection authorization, the platform cannot
prove complete pagination, stable posting identity, opening dates, locations, or
closure reconciliation. Factory construction therefore fails closed.

Activation requires either documented public API terms permitting collection or
tenant-issued authorization from the employer. At that point a connector must
still pass pagination, record validation, US-geography compatibility, bounded
HTTP, empty-manifest safety, and two-run closure tests before scheduling.
