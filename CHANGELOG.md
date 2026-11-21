# Changelog

All notable changes to `uk-due-diligence-mcp` are documented here, starting with 1.7.0. Earlier releases are described in their GitHub release notes.

## [Unreleased]

### Added

- `sanctions_screen`: new `lists_stale` (lists screened from a previous load because their latest refresh failed) and `list_as_at` (each screened list's own load time) fields. `as_at` is now the oldest screened list's load time (#26).

### Changed

- Every upstream request (Companies House, Charity Commission, The Gazette, HMLR, sanctions list downloads) sends one identifying User-Agent, `uk-due-diligence-mcp/<version>` with the project URL and maintainer contact, as the Gazette's fair-use policy asks (#86).
- FastMCP 4.1.3 → 4.1.8 (#49).

### Fixed

- `sanctions_screen` keeps a list's last verified records when a refresh of that list fails, instead of dropping the list for up to 24 hours. Freshness is tracked per list; a list whose last successful load is more than 7 days old is dropped and reported in `lists_unavailable` (#26).
- During a sanctions list outage a failed list is retried at most once per 5 minutes, instead of every call re-downloading every list (#28).

## [1.8.0] — 2026-11-21

### Added

- `gazette_insolvency`: new `upstream_total` (the feed's match count, null when absent), `page` and `is_partial` fields, and a `page` parameter to read past the first 100 feed entries. Requests now ask only for corporate insolvency notices (#83).

### Fixed

- `gazette_insolvency` is explicitly corporate-only: the `name`, `query` and `entity_name` descriptions no longer claim individual search, the tool description states that personal insolvency is not covered and notes that the Individual Insolvency Register covers England and Wales only (Scotland and Northern Ireland have separate registers), and new `skipped_unrecognised` and `unrecognised_codes` fields report feed entries with codes outside the corporate taxonomy (#69).
- `sanctions_screen` treats an underscore as a word separator, so `Begemot_Sun` and `Begemot Sun` match each other on every list (#29).
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
