# Changelog

All notable changes to `uk-due-diligence-mcp` are documented here, starting with 1.7.0. Earlier releases are described in their GitHub release notes.

## [Unreleased]

### Added

- `gazette_insolvency`: new `upstream_total` (the feed's match count, null when absent), `page` and `is_partial` fields, and a `page` parameter to read past the first 100 feed entries. Requests now ask only for corporate insolvency notices (#83).

### Fixed

- `gazette_insolvency` is explicitly corporate-only: the `name`, `query` and `entity_name` descriptions no longer claim individual search, the tool description states that personal insolvency is not covered and points to the Individual Insolvency Register, and new `skipped_unrecognised` and `unrecognised_codes` fields report feed entries with codes outside the corporate taxonomy (#69).
- `charity_search` percent-encodes the query as one path segment, so `/`, `?`, `#` and `%` are searched for instead of changing the request URL (#70).
- The sanctions startup cache warm keeps a reference to its task, so it can no longer be garbage-collected mid-flight (#67).
- A failed charges check inside `company_profile` no longer logs the company number.

## [1.7.0] — 2026-11-18

### Breaking

- `company_profile`: `accounts.overdue` and `confirmation_statement.overdue` are null when Companies House sends no flag (previously false).
- `company_search`: `company_status`/`company_type` are now applied by the server (Companies House ignored them). New fields: `next_start_index`, `upstream_total`, `filtered_returned`, `filters_applied`, `upstream_scanned`, `is_partial`, `partial_reason`. Page with `next_start_index`.

### Fixed

- Company numbers are trimmed and format-checked; malformed numbers return `validation`, not `not_found`.
- `sanctions_screen` rejects an unknown `entity_type` instead of ignoring it.
- Non-JSON upstream bodies and dropped connections return a structured `transient` error instead of an anonymous one (#37).
- MCP handshake reports `uk-due-diligence-mcp` and the package version (was FastMCP's 4.1.3).

### Added

- `/health` reports `version` and `git_sha`.
