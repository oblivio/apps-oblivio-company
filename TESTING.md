# Testing MDB_RUNTIME Migration

## Quick Test

Run the migration test suite:

```bash
python3 test_migration.py
```

This will verify:
- ✅ All `mdb_runtime` imports work
- ✅ Application layer (`core_deps.py`) correctly uses `mdb_runtime`
- ✅ No legacy files remain
- ✅ No legacy imports in code
- ✅ All Python files have valid syntax

## Understanding the Architecture

### `mdb_runtime/` - The Library
This is the **reusable runtime engine** that provides:
- Database scoping (`ScopedMongoWrapper`, `ExperimentDB`)
- Authentication/Authorization (`get_current_user`, `require_admin`, etc.)
- Manifest validation (`ManifestValidator`, `ManifestParser`)
- Index management (`AsyncAtlasIndexManager`)

### `core_deps.py` - The Application Layer
This is **NOT legacy code** - it's the application-specific integration layer that:
- Uses `mdb_runtime` as a library
- Provides application-specific dependencies (FastAPI Depends)
- Handles application-specific caching (`get_experiment_config`)
- Manages application-specific templates
- Provides application-specific auth helpers

**This is the correct pattern!** The application layer imports from `mdb_runtime`, not the other way around.

## What Was Deleted (Legacy Code)

These files were **duplicates** that have been extracted into `mdb_runtime`:
- ❌ `async_mongo_wrapper.py` → Now in `mdb_runtime/database/scoped_wrapper.py`
- ❌ `experiment_db.py` → Now in `mdb_runtime/database/abstraction.py`
- ❌ `mongo_connection_pool.py` → Now in `mdb_runtime/database/connection.py`
- ❌ `manifest_schema.py` → Now in `mdb_runtime/core/manifest.py`
- ❌ `authz_provider.py` → Now in `mdb_runtime/auth/provider.py`
- ❌ `sub_auth.py` → Now in `mdb_runtime/auth/sub_auth.py`
- ❌ `experiment_auth_restrictions.py` → Now in `mdb_runtime/auth/restrictions.py`
- ❌ `index_management.py` → Now in `mdb_runtime/indexes/manager.py`

## What Remains (Application Code)

These files are **application-specific** and should remain:
- ✅ `core_deps.py` - Application layer using `mdb_runtime`
- ✅ `main.py` - Main FastAPI application
- ✅ `database.py` - Application-specific database initialization
- ✅ `role_management.py` - Application-specific role management
- ✅ `authz_factory.py` - Application-specific auth provider factory
- ✅ `experiment_routes.py` - Application-specific routes

## Running the Application

To test the full application:

```bash
# Set environment variables
export MONGO_URI="mongodb://localhost:27017"
export DB_NAME="labs_db"
export FLASK_SECRET_KEY="your-secret-key"

# Run the application
uvicorn main:app --reload
```

## Verifying Imports

Check that imports work correctly:

```python
# ✅ Correct - importing from mdb_runtime
from mdb_runtime.database import ScopedMongoWrapper
from mdb_runtime.auth import get_current_user

# ✅ Correct - importing from application layer
from core_deps import get_experiment_config, require_admin

# ❌ Wrong - legacy imports (should not exist)
from async_mongo_wrapper import ScopedMongoWrapper  # DELETED
from experiment_db import ExperimentDB  # DELETED
```

## Troubleshooting

### Import Errors
If you see import errors, check:
1. Are you importing from `mdb_runtime`?
2. Is the file you're importing from still in the codebase?
3. Run `python3 test_migration.py` to verify

### "core_deps.py is old shit"
`core_deps.py` is **not** legacy code. It's the application layer that:
- Imports from `mdb_runtime` (correct!)
- Provides application-specific FastAPI dependencies
- Handles application-specific caching and configuration

This is the **correct architecture pattern** - the application uses the library, not the other way around.

