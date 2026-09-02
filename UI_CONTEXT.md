# Oracle A2A Demo — UI Context

## Purpose

This local Flask console guides preparation of an Oracle Autonomous AI Database
for Select AI Agents and then tests a published team through external A2A. It
is a setup, validation, and troubleshooting aid; it is not a production control
plane and does not install Oracle sample packages.

The console keeps three kinds of evidence distinct:

- A target-schema `DBMS_CLOUD_AI.GENERATE` call proves the credential, Select
  AI profile, model access, and relevant IAM/network prerequisites work.
- External OAuth, published-team discovery, and an Agent Card prove the A2A
  boundary is reachable by an external client.
- A completed chat task separately proves the database agent runtime completed
  work. An accepted A2A request can still fail or remain `RUNNING` internally.

Use [SQL_VALIDATION.md](SQL_VALIDATION.md) for database-side checks and
[OAUTH_VALIDATION.md](OAUTH_VALIDATION.md) for command-line OAuth validation.

## Overall process

1. Select OCI context and provision or identify an Autonomous AI Database.
2. Check lifecycle, download its wallet, and test an ADMIN connection.
3. Create a target schema if required, then test a connection as it. That test
   selects the active target schema for user-level setup.
4. Enable target-schema Resource Principal access when needed, and create/test
   the target-owned Select AI credential and profile.
5. Install an Oracle agent/team sample manually with SQLcl, following the
   upstream sample instructions.
6. Confirm the owner can see its installed team from SQL.
7. Register an external OAuth client as ADMIN when needed.
8. In A2A Testing, obtain a token, discover a published team, load its Agent
   Card, and chat.

Do not diagnose A2A chat before Select AI works as the target schema and the
team appears in `DBMS_CLOUD_AI_AGENT.LIST_TEAMS()` for that schema.

## Operating modes

### Setup

Setup is the administrator and target-schema-owner workflow. It contains OCI
context, database lifecycle, wallet connections, database users, Resource
Principal access, target Select AI setup, manual SQLcl installation checks, and
OAuth client registration. It may need ADMIN credentials, a wallet, and a
target-schema password.

### A2A Testing

A2A Testing is the external-client workflow. It assumes setup and team
installation already exist. It contains the authorization-code flow, published
team discovery, Agent Card loading, and A2A chat. It intentionally omits OCI,
wallet, ADMIN, and target-schema setup controls.

## Shared layout

### Left navigation

The persistent left outline is the primary navigation. **A2A Testing** appears
first so a ready environment can go straight to testing; **Setup** follows in
the intended configuration order. The active page is highlighted. Green visited
markers are browser-session navigation history, not success evidence.

### Center pane

The center pane has one focused topic per page, normally one or two cards.
Fields are vertically stacked. Buttons are disabled when local prerequisites,
such as a wallet, are unavailable.

### Right help and Setup Status

The right pane explains the page, prerequisites, ownership, and destructive
effects. On Setup pages it also presents clickable **Setup Status** items,
ordered as:

1. OCI profile
2. Wallet downloaded
3. Database lifecycle
4. DB connection (ADMIN)
5. DB connection (Target)
6. Select AI profile
7. Agent Teams
8. OAuth registration

There is no Target Schema status item: a successful target connection is the
authoritative selection of the target user. Select AI status can include both
the profile and credential, for example `A2A_PROFILE - OCI$RESOURCE_PRINCIPAL`.

### Bottom activity log

The fixed bottom dock is a timestamped, newest-first local activity log. It
shows about three entries before scrolling and never shows passwords, private
keys, OAuth tokens, authorization codes, or client secrets.

## State and security

- OCI context, database OCID, service alias, non-secret schema/profile names,
  and safe metadata can be retained locally.
- Wallet files are local under ignored `wallet/`.
- Passwords, private keys, provider secrets, authorization codes, refresh and
  access tokens, and returned OAuth client secrets are memory-only. Passwords
  may be displayed as masked values for the current app process.
- ADMIN performs environment work and grants. The target schema owns its
  credentials, profiles, packages, tools, tasks, agents, and teams.
- `CURRENT_SCHEMA` is not a target-user login: Select AI Agent execution needs
  a real target-schema connection with the correct `SESSION_USER`.

## Overview pages

| Page | Route | Purpose |
| --- | --- | --- |
| Start | `/` | Offers Setup or A2A Testing without performing an operation. |
| Setup overview | `/setup` | Explains setup and offers a read-only Verify Demo preflight. |
| A2A Testing overview | `/test` | Explains the token → team → chat workflow. |

Verify Demo checks saved OCI context, wallet presence, current ADMIN session,
target profile, and owner-side agent objects. It does not obtain OAuth tokens,
discover teams, or send chat. An app restart correctly requires ADMIN to be
tested again because the connection is memory-only.

## Setup pages

### OCI Context

**Route:** `/setup/infra/provision?view=context`

Selects the OCI CLI profile and OCI region. This is the only page that edits
that context; all other infrastructure pages use it rather than providing
competing selectors.

### Provision Database

**Route:** `/setup/infra/provision?view=provision`

Creates an Autonomous Database with the saved OCI context. It collects the
compartment, database/display names, workload/deployment selection, compute,
storage, and required secret reference. The resulting database OCID is retained
as non-secret local context. Wait for `AVAILABLE` before the wallet flow.

### Database Lifecycle

**Route:** `/connect?view=status`

Checks the selected database OCID's lifecycle. `AVAILABLE` is the expected
ready state and is a prerequisite for wallet operations.

### Download / Replace Wallet

**Route:** `/connect?view=wallet`

Downloads the wallet for the selected database after lifecycle is available. A
new download replaces a local wallet copy. Status shows the wallet base path,
not the per-database or secret detail.

### Database Connections

**Route:** `/connect?view=admin`

Contains two independent tests:

1. **ADMIN connection** validates the wallet service alias, wallet password,
   and ADMIN password.
2. **Target connection** validates the target schema password with the same
   wallet context. A successful result selects that schema for the rest of
   Setup.

Creating a schema does not select it. Return here after schema creation and
test the new user's connection.

### OAuth Registration

**Route:** `/oauth-clients?view=register`

ADMIN registers an OAuth client for an external A2A consumer. The page explains
production and local-test callback URIs, shows the returned client secret once,
and retains only safe registration metadata for listing/deletion. The secret
must be copied to OAuth · Get Token and is never persisted. See Oracle's
[OAuth registration documentation](https://docs.oracle.com/en-us/iaas/autonomous-database-serverless/doc/register-oauth-client-oauth.html).

### Target Schema

**Route:** `/select-ai?view=schema`

ADMIN creates a dedicated database user and idempotently grants `EXECUTE` on
`DBMS_CLOUD`, `DBMS_CLOUD_AI`, and `DBMS_CLOUD_AI_AGENT`. This page creates no
demo data and does not select the user. Use Database Connections to connect as
the new user before proceeding.

### Resource Principal

**Route:** `/resource-principal`

Enables, checks, or disables `OCI$RESOURCE_PRINCIPAL` for the selected target
schema only. It requires ADMIN connection and a selected target connection.
OCI dynamic groups and IAM policies remain tenancy-side prerequisites outside
the app; follow Oracle's [Resource Principal documentation](https://docs.oracle.com/en-us/iaas/autonomous-database-serverless/doc/resource-principal.html).

### Target Select AI

**Route:** `/select-ai?view=select-ai`

Creates and tests the selected target schema's provider credential and Select
AI profile. Supported provider choices are OCI API-signing key, OCI Resource
Principal, and OpenAI where supported by the database. The target user—not
ADMIN—owns these resources.

It can list target-owned profiles and credentials and executes a minimal Select
AI test. Resource Principal uses database-managed
`OCI$RESOURCE_PRINCIPAL` and does not create a user-owned credential row. Fix a
failed or hanging Select AI test before troubleshooting an agent team.

### Select AI Agent Configs

**Route:** `/agent-config`

Reads and manages a sample-owned optional `SELECTAI_AGENT_CONFIG` table using
the target-schema connection. It can add, update, or delete one `AGENT` +
`KEY` mapping. It does not create profiles, credentials, agents, or teams. A
missing table is normal for samples that do not use this pattern.

### Installed Teams · SQL

**Route:** `/agents?view=installed`

The app has no local samples directory and does not deploy sample scripts.
Install samples with SQLcl using Oracle's [Autonomous AI Agent samples](https://github.com/oracle-devrel/oracle-autonomous-database-samples/tree/main/autonomous-ai-agents).

This page then connects as the selected target schema and runs:

```sql
SELECT DBMS_CLOUD_AI_AGENT.LIST_TEAMS() FROM dual;
```

It requires no OAuth token and is the owner-side confirmation that a manual
installation is visible to the user who owns it.

### Published Teams · A2A

**Route:** `/agents?view=refresh`

This retained diagnostic calls external A2A discovery using the in-memory OAuth
token. It is distinct from the SQL owner check and is not a primary Setup step;
normal external discovery occurs in A2A Testing → Team Selection.

### Delete Target Schema

**Route:** `/schema-cleanup`

The final target-user cleanup requires ADMIN, a selected target, and the exact
confirmation `DELETE SCHEMA`. It disables schema Resource Principal access when
present, then runs `DROP USER <target> CASCADE`. This removes the user and its
owned data, profiles, packages, tools, task history, agents, teams, and direct
grants. The app clears related local target/team/chat/token state afterward.
OCI IAM policies and dynamic groups are not changed.

### Delete Database

**Route:** `/setup/infra/provision?view=delete`

The final infrastructure cleanup requires an explicit confirmation and exact
database identity. It does not remove IAM policies, Vault secrets, or external
OAuth registrations.

## A2A Testing pages

### Local Listener

**Route:** `/test/listener`

Starts or stops the local OAuth callback listener for same-machine
authorization-code testing. It displays the exact local callback URI to add to
an OAuth registration. Production clients use their own published callback URI.

### OAuth · Get Token

**Route:** `/test/token`

Performs external OAuth authorization code flow:

1. Prepare region-specific Oracle authorization and token endpoints.
2. Supply the client ID and client secret from OAuth Registration.
3. Authorize the target database user.
4. Receive through the local listener or supply the returned code.
5. Exchange it for an access token.

Client credentials, codes, refresh tokens, and access tokens are memory-only.
Safe metadata such as subject, scope, and expiry is displayed; token printing is
available only in explicit debug mode. The region determines OAuth endpoint
hosts; the database OCID is used later for A2A requests, not in those URLs.

### Team Selection

**Route:** `/test/team`

Uses the active external token and database OCID to discover published teams.
The selected team is loaded with its Agent Card, which exposes endpoint,
security, capability, and tool metadata. Zero discovered teams is an external
publication result, not proof that no team exists in the target schema.

### Chat

**Route:** `/test/chat`

Sends A2A JSON-RPC `message/send` with the selected team and in-memory token.
The conversation retains timestamps and scrolls to the latest message. It waits
briefly for a synchronous result, then exposes a pending task and status check
only when needed.

Normal view shows the conversation. Debug mode adds safe request/response and
task diagnostics. “Task submitted” means A2A accepted the request; it is not a
final database-agent answer. Use `TEAM_EXEC_ID` with the task/tool history and
session-wait queries in `SQL_VALIDATION.md` and `AGENT_TEAM_RUNBOOK.md` to
investigate failed or long-running calls.

## Design rules

1. Keep Setup administration separate from external A2A Testing.
2. Treat target selection as a successful database login, never a free-form
   schema selection control.
3. Prefer explicit, idempotent database operations and confirmations for
   destructive operations.
4. Keep pages focused, vertically stacked, and explain prerequisites in the
   right help pane.
5. Never persist or render secrets; redact them in debug output.
6. Never describe a token, discovery response, Agent Card, or accepted task as
   proof that the database agent completed a chat.
7. Update this document whenever visible routes, status, ownership boundaries,
   or setup/testing responsibilities change.
