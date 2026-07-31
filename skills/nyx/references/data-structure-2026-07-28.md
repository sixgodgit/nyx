# Nyx Data Structure & Visualization Hooks

## Data Sources (confirmed 2026-07-28)

### Sandglass Storage
- **File**: `/root/.hermes/nexsandglass/sandglass.txt`
- **Format**: `{timestamp} | {sender} | {text}` (pipe-delimited, one entry per line)
- **Scale**: ~36,000 entries over ~1,800 days
- **Size**: ~1.8 MB

### Session Database
- **File**: `/root/.hermes/state.db` (SQLite)
- **Tables**: `sessions`, `messages`
- **Session columns**: id, source, user_id, model, model_config, ended_at, input_tokens, reasoning_tokens, cwd, billing_mode, cost_source, handoff_state, handoff_error, archived, git_branch, git_repo_root
- **Message columns**: (full schema via `PRAGMA table_info(messages)`)

### Session Sources Distribution
| Source | Typical Count |
|--------|--------------|
| cron | ~1200 |
| cli | ~80 |
| qqbot | ~60 |
| subagent | ~60 |
| feishu | ~54 |
| api_server | ~36 |
| weixin | ~1 |

### Memory Layers
- **Directory**: `/root/.hermes/memory_layers/`
- **Files**: L0 identity, L1 facts, Persona, AAA-K compressed

### Archives
- **Directory**: `/root/.hermes/nexsandglass/archive/`
- **Chroma**: `/root/.hermes/nexsandglass/chroma_sand/` (semantic search index)

### Dreams
- **Directory**: `/root/.hermes/dreams/`
- **Count**: ~40 files

## Quick Stats Query

```python
import sqlite3
conn = sqlite3.connect('file:/root/.hermes/state.db?mode=ro', uri=True)
c = conn.cursor()
c.execute("SELECT source, COUNT(*) FROM sessions GROUP BY source ORDER BY COUNT(*) DESC")
print(c.fetchall())
c.execute("SELECT COUNT(*) FROM messages")
print("Messages:", c.fetchone()[0])
conn.close()
```

## Visualization History

The user has repeatedly requested Nyx memory visualization. Key aesthetic requirements established across iterations:

1. **Must be literal** — A sandglass must look like a sandglass, not abstract triangles
2. **Dot-matrix wireframe** — Geometric shapes built from point clouds + connecting lines
3. **Particles with physics** — Gravity, turbulence, damping, container collision
4. **5000+ particles** — User rejected 300 as insufficient
5. **Data labels in flow** — Numbers float alongside particles, not in separate legends
6. **Cyberpunk palette** — Dark bg (#04040a), neon blue-purple glow

See also `creative/generative-data-art` skill for the full technical implementation pattern.

## MCP Tools Available

When working with Nyx data, these MCP tools are useful:
- `mcp_pre_gateway_dispatch_sandglass_recent` — get recent sandglass entries
- `mcp_pre_gateway_dispatch_sandglass_search` — keyword search
- `mcp_pre_gateway_dispatch_sandglass_chart` — ASCII emotion chart
- `mcp_pre_gateway_dispatch_sandglass_offset` — offset/spend stats
- `mcp_pre_gateway_dispatch_sandglass_persona` — current persona
- `mcp_pre_gateway_dispatch_sandglass_ping` — health check
