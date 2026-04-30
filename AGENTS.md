# AGENTS.md — uk-due-diligence-mcp

AI agent instructions for working in this repo. See `/home/bch/dev/ops/OPS.md` for credentials, fleet overview, and release tooling.

## Repo shape

Flat layout. Entry point is `server.py`. Domain modules: `companies_house.py`, `charity.py`, `gazette.py`, `hmrc_vat.py`, `disqualified.py`, `land_registry.py`.

## Deploy

```bash
fly deploy --ha=false
```

Single instance, lhr region. App name: `uk-due-diligence-mcp`. Fly.io account: articat1066@gmail.com.

## Version bump

1. Update `version` in `pyproject.toml`
2. Update version string in the `smithery_server_card` route in `server.py`
3. Commit, tag `vX.Y.Z`, push + push tags
4. GitHub Actions publishes to PyPI automatically on tag
5. `fly deploy --ha=false`
6. Cut a new Glama release

## Standard routes (must always be present)

- `/.well-known/mcp/server-card.json` — Smithery metadata
- `/.well-known/glama.json` — Glama maintainer claim
- `/health` — Fly health check

Verify after deploy:
```bash
curl https://uk-due-diligence-mcp.fly.dev/.well-known/mcp/server-card.json
curl https://uk-due-diligence-mcp.fly.dev/.well-known/glama.json
curl https://uk-due-diligence-mcp.fly.dev/health
```

## README badge order

```
PyPI → SafeSkill → Glama card → Smithery
```

## Do not

- Do not use `FASTMCP_PORT` — the server reads `PORT` env var only
- Do not set `internal_port` in fly.toml to anything other than 8080
- Do not commit API keys — all secrets are in Fly secrets (`fly secrets list`)
