# Sample packages

Each directory is a deployable sample package. `sample.json` declares its
identity, required target-schema Select AI profile, ordered deployment steps,
and any parameters required by those steps.

The console reads these manifests rather than hard-coding a single team. A
sample may be deployed repeatedly to different target schemas in the same
database. Packages must keep object names unique from other packages.

- `sales-data` is Oracle's published Oracle AI Database Agent sample, pinned
  by source URL and SHA-256 so the console refuses an unexpected upstream edit.
- `database-provisioning` is a local package. It has no dependency on sales
  tables and should be deployed to a least-privileged provisioning schema.
  Its grants/functions run as ADMIN; its agent, tools, task, and team run on a
  real connection as the target schema. The deployment password is request-only
  and is never saved.

Local SQL uses `{{NAME}}` placeholders. The console substitutes only manifest
parameters after validating identifier-like values; secrets are never written
to a manifest or saved in local settings.

For `database-provisioning`, a successful deploy must be visible to the target
schema through `DBMS_CLOUD_AI_AGENT.LIST_TEAMS()`. This is the owner-side check
before external OAuth discovery.
