# StoreContext and host binding

M1-04 adds a trusted in-process storage scope. A host chooses a root from its
own profile configuration, constructs `StoreContext`, and binds it around work:

```python
from nexsandglass.runtime import StoreContext, observe, recall_request, RecallRequest

store = StoreContext('/operator/configured/memory', profile_id='owner')
with store.bind():
    observe('I live in Oslo.', source='user')
    packet = recall_request(RecallRequest('Where do I live?', token_budget=600))
```

The constructor is an operator/host API, not authentication. Session, task,
turn and language fields never select a directory or grant access. A network
adapter must authenticate first and map its own profile to a context; it must
not accept a data directory from a model tool argument. This is not an OS
sandbox for arbitrary Python code or a defense against a malicious filesystem
administrator replacing symlinks concurrently.

Bound roots take precedence over import-time paths and legacy overrides.
Managed canonical file overrides, traversal and symlinks resolving outside the
root are rejected. Binding restores the previous scope even after exceptions;
it does not modify `os.environ` or imported module globals. Unbound CLI and MCP
processes retain existing single-store behavior and path override compatibility.

## Implemented closure

| Path | Explicit store behavior |
| --- | --- |
| Raw write, canonical IDs and source resolution | Per-root journal and `nyx.db`; thread-local connections retain nested stores' transactions |
| SearchRouter, lexical/FTS/mmap, shadow, graph and engram | Per-root files, connections and caches; executor work captures context |
| Semantic provider/indexing | Per-store configuration and credential identity; background work captures the originating store |
| Profile/follow-up views and voice facts | Bound canonical sources, durable checkpoints and per-store follow-up switch |
| Forget, quarantine, restore, expiry cleanup | Same root for sources and derived files; copied quarantine paths cannot redirect restore |
| Consolidation snapshots | Scoped cleanup and verification; snapshot copies are not authority |
| Historical Hermes import | Duplicate checks, import progress and writes use the provider's bound store; historical clocks stay local |
| Media maintenance | Bound media directory; copied metadata cannot delete a blob in a different root |
| Vault | Separate DB and confirmation/pending/redaction state; secondary file vault requires an explicit scoped key |
| Hermes provider | Binds on availability/initialization from trusted `nyx.json`; hook calls and warming retain that binding |

Use `store_context.submit()` and `spawn_thread()` for new asynchronous work.
They copy ContextVars and a frozen simulation clock. Ordinary Python executors
and threads do not automatically propagate these scopes. A callable in
`credential_getter` is the only credential source in an explicit context;
missing or failed lookup does not fall back to another profile or process key.
Hermes captures its native secret scope; standalone provider initialization
captures operator API-key environment values. Settings retain documented
operator environment precedence. They are not a credential lookup channel.

The default vault key path is unchanged. A secondary file vault requires a
stable `NYX_VAULT_KEY` supplied by its credential getter; it cannot silently use
the primary installation's key or create a key a standalone CLI cannot find.
Without that key it reports unavailable and keeps the existing redact-only
fallback. Keys must be backed up separately. Pending default-root reveals remain
compatible with the companion render hook. Secondary-root reveals return
`use_cli`: that hook does not yet bind a secondary store. Use the matching data
directory and key in that terminal. These restrictions prevent a secret from
being rendered into an unbound session.

## Deliberate compatibility boundary

Legacy persona/NYX L3 projections, offset/echo, history expansion and consolidation
still contain import-time globals. They remain available for the legacy root.
On a secondary root the supported runtime/provider/MCP adapters omit or reject
those paths; they never fall back to the legacy root. System-context traces use
`legacy_layers_unavailable`; ordinary recall retains per-source failure traces.
The secondary-root system block contains canonical profile/follow-ups and status,
with the same shared budget as the primary root. Raw direct calls to legacy L3
modules are not a supported multi-profile API.

This is the first complete scoped core read/write/delete/restore closure, not
migration of every historical utility or a multi-device synchronization layer.
Same-root profiles intentionally share data; hosts requiring separation must
configure different roots. `store_id` is a correlation hash, not a secret or an
access token. File-backed caches remain process-local and are not a distributed
consistency protocol.

Verification: `tests/test_store_context.py` runs nested transactions, reused
workers, credentials, historical imports, parallel initialized Hermes profiles,
and the real public write/recall/forget/restore pipeline with distinct lexical,
shadow, graph, engram and deterministic fake semantic backends. Fake embeddings
verify plumbing only; they do not establish cross-language retrieval quality.
