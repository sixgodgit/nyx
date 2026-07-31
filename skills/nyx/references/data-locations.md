# Nyx Data Locations & Schema

## Sandglass Log
**Path**: `/root/.hermes/nexsandglass/sandglass.txt`
**Format**: pipe-delimited text
```
YYYY-MM-DD HH:MM:SS | sender | text content
```
**Stats** (as of 2026-07): ~36K entries, ~1804 days, ~20 entries/day avg

## Sessions Database
**Path**: `/root/.hermes/state.db` (SQLite)
**Access**: `sqlite3 "file:/root/.hermes/state.db?mode=ro"` (read-only)

### sessions table
| Column | Type | Description |
|--------|------|-------------|
| id | TEXT | Session ID (format: `YYYYMMDD_HHMMSS_hex`) |
| source | TEXT | Origin: `cron`, `cli`, `qqbot`, `feishu`, `subagent`, `api_server`, `weixin` |
| user_id | TEXT | User identifier |
| model | TEXT | Model used |
| input_tokens | INT | Token count |
| ended_at | TEXT | End timestamp |
| cwd | TEXT | Working directory |
| billing_mode | TEXT | Billing type |
| archived | BOOL | Archive status |

### messages table
| Column | Type | Description |
|--------|------|-------------|
| id | TEXT | Message ID |
| session_id | TEXT | Parent session |
| role | TEXT | `user`, `assistant`, `system`, `tool` |
| content | TEXT | Message body |

## Memory Layers
**Path**: `/root/.hermes/memory_layers/`
- `l0_identity.md` — core identity (~321 bytes)
- `l1_facts.aaak` — compressed facts (~1.6 KB)
- `persona_combined.md` — persona data (~3.5 KB)

## Dreams
**Path**: `/root/.hermes/dreams/`
**Format**: `YYYY-MM-DD.md` (one file per day)
**Stats**: ~40 files

## Chroma Semantic Index
**Path**: `/root/.hermes/nexsandglass/chroma_sand/`
**Size**: ~1.14 MB

## WeaveThread (Knowledge Graph)
**Path**: `/root/.hermes/nexsandglass/weavethread/`
