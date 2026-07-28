# Security Policy

## Reporting a vulnerability

If you find a security issue in this software or the hosted service at
`uk-due-diligence-mcp.fly.dev`, please report it privately:

- **Email**: paul@bouch.dev (subject line starting `SECURITY:`)
- **GitHub**: [private vulnerability reporting](https://github.com/paulieb89/uk-due-diligence-mcp/security/advisories/new)

Please do not open a public issue for security reports.

You can expect an acknowledgement within **3 working days** and a status
update within **14 days**. There is no bug bounty programme, but reports are
genuinely appreciated and reporters are credited in release notes unless
they prefer otherwise.

## Scope and good-faith research

In scope: this repository's code and the hosted MCP endpoint. Good-faith
testing is welcome, with two asks: do not run tests that degrade the service
for others (load/DoS testing), and if you encounter data belonging to
another user, stop and report rather than exploring further.

## Incident handling

The service holds no customer accounts and stores no query data, which
limits the blast radius of most incident classes. If an incident does affect
personal or customer data, we will notify affected parties, HMRC (as
required by the HMRC Developer Hub terms of use), and the ICO within
**72 hours** of becoming aware of it.

## Supported versions

Only the latest release is supported. The hosted service always runs the
latest release; self-hosted deployments should track it.
