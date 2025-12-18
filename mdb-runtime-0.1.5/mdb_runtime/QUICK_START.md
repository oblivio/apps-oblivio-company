# MDB_RUNTIME Quick Start Guide

## Installation

The package is already in your workspace. No installation needed - just import!

## Basic Usage

### 1. Initialize the Runtime Engine

```python
from mdb_runtime import RuntimeEngine
from pathlib import Path

# Create engine instance
engine = RuntimeEngine(
    mongo_uri="mongodb://localhost:27017",
    db_name="my_database",
    experiments_dir=Path("experiments")
)

# Initialize (async)
await engine.initialize()
```

### 2. Get Scoped Database Access

```python
# Get experiment-scoped database
db = engine.get_scoped_db("my_experiment")

# Use MongoDB-style API
doc = await db.my_collection.find_one({"name": "test"})
docs = await db.my_collection.find({"status": "active"}).to_list(length=10)
await db.my_collection.insert_one({"name": "New Doc"})
```

### 3. Register Experiments

```python
# Load and validate manifest
manifest = await engine.load_manifest(Path("experiments/my_exp/manifest.json"))

# Register experiment (automatically creates indexes)
await engine.register_experiment(manifest)

# Or reload all active experiments from database
count = await engine.reload_experiments()
```

### 4. Use Individual Components

```python
# Database scoping
from mdb_runtime.database import ScopedMongoWrapper, ExperimentDB

# Authentication
from mdb_runtime.auth import get_current_user, require_admin

# Manifest validation
from mdb_runtime.core import ManifestValidator

validator = ManifestValidator()
is_valid, error, paths = validator.validate(manifest)

# Index management
from mdb_runtime.indexes import AsyncAtlasIndexManager
```

## Context Manager Usage

```python
# Automatic cleanup
async with RuntimeEngine(mongo_uri, db_name) as engine:
    await engine.reload_experiments()
    db = engine.get_scoped_db("my_experiment")
    # ... use engine
    # Automatic cleanup on exit
```


## Testing

Run the test suite:

```bash
python3 test_mdb_runtime.py
```

## Package Structure

```
mdb_runtime/
├── core/          # RuntimeEngine, Manifest validation
├── database/      # Scoped wrappers, ExperimentDB
├── auth/          # Authentication, authorization
└── indexes/       # Index management
```

## Next Steps

- See `README.md` for detailed documentation
- See `EXTRACTION_SUMMARY.md` for extraction details
- Check existing code for usage examples

