# OCI Autonomous Database A2A Demo

Local Flask console for deploying Select AI Agent sample packages and testing
them as an external A2A OAuth client. It is a guided test tool, not a
production control plane.

## TL;DR

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
FLASK_SECRET_KEY='long-random-value' .venv/bin/python app.py
```

Open <http://127.0.0.1:5000>.

1. **Setup → Infra & ADMIN**: select OCI profile/region, select or provision
   the database, download its wallet, and test the ADMIN connection.
2. **Setup → Target User**: create/select the schema, grant required packages,
   then create its enabled `A2A_PROFILE` using either an OCI API-signing key or
   Resource Principal. Resource Principal uses the database-managed
   `OCI$RESOURCE_PRINCIPAL`; configure its OCI policies outside the console.
3. **Setup → Package Readiness**: deploy a package.
   - **Sales Data Query** requires controlled demo data.
   - **Database Provisioning** requires a dedicated target schema password,
     Resource Principal policy, tenancy/compartment/secret inputs, and creates
     its agent objects as that target schema.
4. **Setup → OAuth · Register**: register a client and copy its one-time
   secret securely.
5. **A2A Testing → OAuth · Get Token**: prepare endpoints, authorize as the
   target user, and exchange the code.
6. **Team Selection**, then **Chat**. For an approval pause, reply in the same
   chat. Do not choose **Start new conversation**.

The left outline is the workflow navigation. The bottom dock shows known
environment state and recent actions. **Verify Demo** is a setup-only
preflight; it does not obtain an OAuth token.

## Important boundaries

- `ADMIN` performs environment work and grants.
- The target schema owns its credential, Select AI profile, and agent objects.
  `CURRENT_SCHEMA` is not a substitute for connecting as that user.
- OAuth token, client secret, authorization code, passwords, and provider keys
  are never persisted by the console.
- A successful token, discovery, and Agent Card prove external A2A access.
  They do not prove the database Select AI Agent runtime can complete a task.

## Documentation

- [UI_CONTEXT.md](UI_CONTEXT.md): UI, navigation, state, and security design.
- [samples/README.md](samples/README.md): package layout and deployment model.
- [AGENT_TEAM_RUNBOOK.md](AGENT_TEAM_RUNBOOK.md): SQLcl walkthrough and
  database-agent runtime troubleshooting.
- [SQL_VALIDATION.md](SQL_VALIDATION.md): SQL checks for profiles, packages,
  teams, tools, and task history.
- [OAUTH_VALIDATION.md](OAUTH_VALIDATION.md): terminal OAuth flow and A2A
  discovery validation.

Wallets are stored under ignored `wallet/`; non-secret form state is stored in
ignored `last-settings.json`. Never commit either file or terminal output that
contains credentials or tokens.
