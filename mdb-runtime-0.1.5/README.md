# mdb-runtime

MongoDB Multi-Tenant Experiment Runtime Engine

## Installation

```bash
pip install mdb-runtime
```

## Quick Start

```python
from mdb_runtime import RuntimeEngine

# Initialize engine
engine = RuntimeEngine(
    mongo_uri="mongodb://localhost:27017",
    db_name="my_database"
)
await engine.initialize()

# Get scoped database
db = engine.get_scoped_db("my_experiment")
doc = await db.my_collection.find_one({"name": "test"})
```

## Features

- **Multi-tenant database scoping** - Automatic experiment isolation
- **Authentication & Authorization** - Built-in auth with Casbin/OSO support
- **Manifest validation** - JSON schema validation with versioning
- **Index management** - Automatic Atlas Search and Vector index management
- **Runtime engine** - Centralized orchestration for all components

## Documentation

See `mdb_runtime/README.md` for detailed documentation.

## License

MIT License
