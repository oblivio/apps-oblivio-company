# MDB_RUNTIME

MongoDB Multi-Tenant Experiment Runtime Engine

## Overview

MDB_RUNTIME is an enterprise-grade runtime engine for building multi-tenant applications with automatic database scoping, authentication, and resource management.

## Package Structure

```
mdb_runtime/
├── core/              # Core runtime engine and orchestration
├── database/          # Database abstraction and scoping ✅ EXTRACTED
├── auth/              # Authentication and authorization
├── indexes/           # Index management
├── actors/            # Ray actor management
├── routing/           # Route registration
├── observability/     # Logging, metrics, tracing
├── security/          # Security controls
├── resilience/        # Resilience patterns
├── multi_tenancy/     # Multi-tenancy support
├── cache/             # Caching layer
├── utils/             # Utility functions
└── testing/           # Testing utilities
```

## Status

✅ **Phase 1 Complete**: Package structure created  
✅ **Phase 2 Complete**: Database layer extracted  
✅ **Phase 3 Complete**: Manifest system extracted  
✅ **Phase 4 Complete**: Auth system extracted  
✅ **Phase 5 Complete**: Index management extracted  
✅ **Phase 6 Complete**: RuntimeEngine created

### Database Module (`mdb_runtime/database/`)

**Extracted Components:**
- `scoped_wrapper.py` - ScopedMongoWrapper, ScopedCollectionWrapper, AsyncAtlasIndexManager, AutoIndexManager
- `abstraction.py` - ExperimentDB, Collection, get_experiment_db, create_actor_database
- `connection.py` - Connection pooling (get_shared_mongo_client, get_pool_metrics)

**Usage:**
```python
from mdb_runtime.database import (
    ScopedMongoWrapper,
    ExperimentDB,
    get_shared_mongo_client
)
```

### Manifest Module (`mdb_runtime/core/manifest.py`)

**Extracted Components:**
- `ManifestValidator` - Class-based validator with caching and versioning
- `ManifestParser` - Parser for loading manifests from files/dicts
- All validation functions (backward compatible)

**Usage:**
```python
from mdb_runtime.core import ManifestValidator, ManifestParser

# Class-based API
validator = ManifestValidator()
is_valid, error, paths = validator.validate(manifest)

parser = ManifestParser()
manifest = await parser.load_from_file("manifest.json")
```

### Auth Module (`mdb_runtime/auth/`)

**Extracted Components:**
- `provider.py` - AuthorizationProvider protocol, CasbinAdapter, OsoAdapter
- `jwt.py` - JWT token utilities
- `dependencies.py` - FastAPI auth dependencies (get_current_user, require_admin, etc.)
- `sub_auth.py` - Experiment-specific authentication
- `restrictions.py` - Demo user restrictions

**Usage:**
```python
from mdb_runtime.auth import (
    AuthorizationProvider,
    get_current_user,
    require_admin,
    get_experiment_sub_user,
    is_demo_user
)
```

### Index Management Module (`mdb_runtime/indexes/`)

**Extracted Components:**
- `manager.py` - High-level index orchestration (`run_index_creation_for_collection`)
- Re-exports `AsyncAtlasIndexManager` and `AutoIndexManager` from database module

**Note:** The index manager classes (`AsyncAtlasIndexManager`, `AutoIndexManager`) are in `mdb_runtime/database/scoped_wrapper.py` and are re-exported from this module for convenience.

**Usage:**
```python
from mdb_runtime.indexes import (
    AsyncAtlasIndexManager,
    AutoIndexManager,
    run_index_creation_for_collection
)
```

### RuntimeEngine (`mdb_runtime/core/engine.py`)

**Core Orchestration:**
- `RuntimeEngine` - Main orchestration class that manages:
  - Database connections and scoping
  - Manifest validation and parsing
  - Experiment registration
  - Index management
  - Resource lifecycle

**Usage:**
```python
from mdb_runtime.core import RuntimeEngine

# Initialize engine
engine = RuntimeEngine(
    mongo_uri="mongodb://localhost:27017",
    db_name="my_database",
    experiments_dir=Path("experiments")
)

# Initialize (async)
await engine.initialize()

# Get scoped database
db = engine.get_scoped_db("my_experiment")

# Register experiment
manifest = await engine.load_manifest(Path("experiments/my_exp/manifest.json"))
await engine.register_experiment(manifest)

# Reload all experiments
count = await engine.reload_experiments()

# Cleanup
await engine.shutdown()

# Or use as async context manager
async with RuntimeEngine(mongo_uri, db_name) as engine:
    await engine.reload_experiments()
    # ... use engine
```

## Next Steps

- Phase 7: Add enterprise features (observability, resilience, security, caching, etc.)
- Integration testing with existing codebase
- Performance optimization
- Documentation and examples

## License

Same as parent project.
