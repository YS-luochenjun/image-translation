# Fixed historical Fireplay session dispatcher

Host-only DSH bundle for the 37 requirements frozen on 2026-11-03. This is
historical batch infrastructure, not the general entry point for future
requirements. General controller/worker routing is documented in the
[autonomous workflow](../../agent/workflows/fireplay-autonomous.md).

## Scope and state

Production reads `work/fireplay-production/intake-index-20261103.json`, pinned
SHA256 `cd1c3e5d62be3a59bb158b714162584c3ad67d9b8781fa264236ca501ce88c6b`.
The local control, parent reviews, handoffs and original dispatch/publication
journals must also exist. They are intentionally not copied from test fixtures
into production. An ordinary clone is enough for offline tests, not live batch
recovery.

The Host uses public `sessionController`, `workspaceRegistry` and `agents`
services. It does not read browser cookies, launch tokens, profile defaults or
credentials. The plugin exposes:

| Tool | Responsibility |
| --- | --- |
| `fireplay_session_dispatch` | Preview/freeze a requirement handoff, create an ordinary code-titled conversation, dispatch once after parent scope/evidence/capacity review, or inspect |
| `fireplay_session_control` | Inspect the fixed batch and queue reviewed control to eligible original conversations; scoped idle resume needs fresh parent evidence |
| `fireplay_session_identity` | Review/migrate an original title through official rename, preserving the immutable dispatch chain; never create, prompt or unarchive |

New cold originals use the exact requirement code, including the four
historically empty business titles. Creation requires the reviewed snapshot and
source/delivery context. First dispatch has a separate reviewed admission gate.
Active, reserved, approved wake and cold first-dispatch slots share a maximum
of four. Archived, foreign, nonordinary or uncertain identities fail closed.

The entry also retains the separate historical 043 launcher with
`allowPrompt:false`: activation can create/rename an authorized cold conversation
when its exact local plan exists. Installation is therefore a Host mutation,
not a read-only test. Review the current entry and local evidence before activation.

State is persisted before mutation under `work/fireplay-production/`. Unknown
create/rename/prompt results are not automatically replayed after restart.
Inspection may recover only positive durable identity evidence. Acceptance by
the Host means a prompt was accepted, not that production finished. Preserve all
original journals.

## Installation and validation

Install only through the running host's official Plugin Manager/local bundle
entry, following its actual permissions and activation result. This repository
does not change profiles or imply that a clone should activate the historical
dispatcher. Current SDK compatibility and actual chat creation require separate
authorized live verification.

Offline tests use Node's built-in runner and public-interface mocks:

```powershell
$tests = @(rg --files plugins/fireplay-session-dispatch -g '*.test.js' -g '*.test.mjs')
node --test --test-isolation=none @tests
```

The [fixtures](fixtures/README.md) contain the exact pinned historical manifest
and seven bounded read-only helper shapes. Tests create temporary projects and
never execute a real Host or platform write. They cover scope/hash changes,
four-slot admission, identity migration, unknown-result recovery without replay,
readonly declarations and ordinary043 capacity. Local control reviews,
production artifacts and paid capabilities remain outside the test contract.
