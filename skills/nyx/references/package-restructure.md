# Python Package Restructure — Lessons from Nyx v3.0.0

## Pattern: Flat scripts → modular sub-packages

**When:** 25+ scripts in root dir, imports are string-based (`__import__("X")`) or assume flat layout.

### Structure template

```
package/
├── core/          # Infrastructure (memory, search, storage)
├── l3/            # Intelligence layer (persona, offset, weave, scene, emotion)
├── features/      # Feature modules (vault, think, shadow_sand, pulse)
├── interfaces/    # External APIs (MCP, plugin, CLI)
├── utils/         # Utilities (heartbeat, discipline)
├── scripts/       # One-off migration scripts
├── experiments/   # Legacy/experimental code
├── docs/          # Documentation
├── tests/         # Tests
└── demo/          # Demo files
```

### Key fixes

1. **`__import__("X")` decorators** — break when moved to subpackage. Replace with:
   ```python
   @importlib.import_module("package.sub.module").function_name(args)
   ```
   And add `import importlib` at top of file.

2. **Circular imports** — lazy initialization pattern:
   ```python
   _fail_open = None  # placeholder
   def _lazy_import():
       global _fail_open
       if _fail_open is None:
           from package.other.module import _fail_open as _fo
           _fail_open = _fo
   ```
   Decorators that call `_fail_open` at import time will fail with `TypeError: 'NoneType' is not callable`. Use `importlib.import_module` for those instead.

3. **Batch import update** — use `find + xargs sed`:
   ```bash
   find package -name "*.py" | xargs sed -i \
     -e 's/from old_name/from package.sub.old_name/g' \
     -e 's/import old_name/import package.sub.old_name/g'
   ```
   Then manually fix `__import__` calls that sed can't handle.

4. **Git remote mismatch** — if pushing to a different account, use:
   ```bash
   token=$(gh auth token)
   git remote set-url origin "https://user:token@github.com/user/repo.git"
   git push --force origin main
   ```

### Verification

```python
import importlib
for sub in ['core', 'l3', 'features', 'interfaces', 'utils']:
    importlib.import_module(f"package.{sub}")
```
