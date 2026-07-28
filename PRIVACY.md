# Privacy Policy — UK Due Diligence MCP Server

**Last updated: 28 July 2026**

This policy covers the UK Due Diligence MCP server: the hosted service at
`https://uk-due-diligence-mcp.fly.dev/mcp` and the open-source software
published as `uk-due-diligence-mcp` on PyPI and GitHub.

BOUCH is a trading name of Paul Boucherat, a practical AI training and
workflow practice based in the East Midlands, United Kingdom. The data
controller is Paul Boucherat, **paul@bouch.dev**. This policy is specific
to this software; the general BOUCH privacy policy is at
[bouch.dev/privacy](https://bouch.dev/privacy).

## What this service does

The server is a read-only lookup service. It forwards queries from your AI
client (Claude, ChatGPT, or any MCP-compatible client) to official UK public
registers — Companies House, the Charity Commission, The Gazette, HM Land
Registry, HMRC's VAT register — and to the published OFSI, OFAC, EU, and UN
consolidated sanctions lists, then returns the results.

## What passes through the service

Your queries may contain personal data — for example a director's name, or a
sole trader's VAT number. Query results may also contain personal data drawn
from the public registers themselves (officer names, partial dates of birth,
service addresses). All of this data:

- is processed in memory only, for the duration of your request
- is **not** stored in any database — the service has none
- is **not** cached — each query goes to the upstream register
- is **not** used for profiling, marketing, or training

## What we do collect

- **Connection metadata**: when a client connects, we log the client
  application's name and version (e.g. "claude-ai 1.0"), the transport type,
  and the server region. No user identity, no query content.
- **Aggregate metrics**: counters of tool calls per tool and client
  application, used for reliability monitoring. These contain no personal
  data and no query content.
- **Hosting logs**: our hosting provider, Fly.io, keeps standard,
  short-lived server logs (IP addresses, request timestamps).

What we do **not** collect:

- no accounts, and no registration
- no cookies or tracking of any kind
- no query content in logs or analytics
- no data shared with or sold to third parties

## Sanctions list data

The service periodically downloads the publicly published OFSI, OFAC, EU,
and UN consolidated sanctions lists, holds a searchable index of them in
memory, and deletes the downloaded files immediately after parsing. These
lists are official government publications; we add nothing to them.

## Where processing happens

The service runs on Fly.io infrastructure in **London, United Kingdom**.
Queries to upstream registers go directly to the relevant UK government API
(and, for OFAC/EU/UN sanctions lists, to those bodies' official publication
endpoints).

## Legal basis

We process query data under **legitimate interests** (UK GDPR Article
6(1)(f)): providing lookup access to information that the UK government
publishes in public registers. The registers themselves are the primary
publishers of any personal data in the results.

## Your rights

Under UK GDPR you have the right to access, correct, or request deletion of
personal data we hold. In practice we hold essentially none — queries are
not stored — but you can contact **paul@bouch.dev** with any request and
we will respond within one calendar month. To correct data *in the
registers* (e.g. your entry at Companies House), contact the relevant
register directly.

You also have the right to complain to the Information Commissioner's
Office ([ico.org.uk](https://ico.org.uk)).

## Changes

Changes to this policy will be published here with an updated date. Material
changes to what the hosted service collects will be noted in the project's
release notes. We will not retroactively apply a less protective policy to
data already collected.
