# MongoDB Implementation Guidelines & Best Practices

This document outlines the architectural decisions and implementation patterns for MongoDB interactions within the Modular Experiment Labs platform. It focuses on robust index management, asynchronous operations, and handling the specific requirements of MongoDB Atlas (especially Vector and Search indexes).

While the platform provides wrappers (`AsyncAtlasIndexManager`, `ScopedMongoWrapper`), understanding the underlying implementation patterns is crucial for extending functionality or debugging.

## 1. Async & Non-Blocking Architecture

All database interactions use `motor` (Motor: Asynchronous Python driver for MongoDB). This ensures that database I/O does not block the main event loop, which is critical for high-performance FastAPI applications.

### Implementation Pattern: Async Wrappers

When building database utilities, wrap standard Motor calls in `async def` methods.

```python
# ✅ Correct: Async implementation
async def get_user_data(collection, user_id):
    # await the cursor to avoid blocking
    return await collection.find_one({"user_id": user_id})

# ❌ Incorrect: Blocking calls
def get_user_data_blocking(collection, user_id):
    # This would fail with Motor, or block if using PyMongo
    return collection.find_one({"user_id": user_id})
```

## 2. Robust Index Management

Index management—especially for Atlas Search and Vector Search—requires careful handling because **Atlas index creation is asynchronous**. Sending a create command returns immediately, but the index is not ready for queries until it has finished building on the Atlas side.

### A. The `SearchIndexModel`

We use `pymongo.operations.SearchIndexModel` to define Atlas Search indexes programmatically. This provides a structured way to define vector and full-text search configurations.

**Key Components:**
*   **definition**: The raw JSON configuration for Atlas (mappings, fields, analyzers).
*   **name**: Unique identifier for the index.
*   **type**: Usually `vectorSearch` or `search`.

```python
from pymongo.operations import SearchIndexModel

# Define a Vector Search Index
model = SearchIndexModel(
    definition={
        "fields": [
            {
                "type": "vector",
                "path": "embedding",
                "numDimensions": 1536,
                "similarity": "cosine"
            },
            # Always index filter fields for performance!
            {"type": "filter", "path": "experiment_id"}
        ]
    },
    name="my_vector_index",
    type="vectorSearch"
)
```

### B. The "Wait for Ready" Pattern

Because Atlas Search indexes build in the background, code that relies on them immediately (like initializing an app or running a test) must **poll for readiness**.

**Implementation Strategy:**
1.  **Submit Creation**: Call `create_search_index`.
2.  **Poll Status**: Repeatedly check `$listSearchIndexes`.
3.  **Check Queryable**: Wait until `queryable` is `true` and `status` is `READY`.
4.  **Timeout**: Fail gracefully if it takes too long.

**Example Implementation Logic:**

```python
async def wait_for_index(collection, index_name, timeout=300):
    start = time.time()
    while time.time() - start < timeout:
        # Use aggregation to inspect specific index status
        cursor = collection.aggregate([
            {"$listSearchIndexes": {"name": index_name}}
        ])
        
        # We expect 0 or 1 result
        async for index_info in cursor:
            if index_info.get("queryable") is True:
                logger.info(f"Index {index_name} is ready!")
                return True
            elif index_info.get("status") == "FAILED":
                raise Exception(f"Index build failed: {index_info}")
        
        logger.debug(f"Waiting for index {index_name}...")
        await asyncio.sleep(5) # Don't hammer the API
        
    raise TimeoutError(f"Index {index_name} not ready after {timeout}s")
```

### C. Idempotency & Race Conditions

Index creation code runs frequently (e.g., on container startup). It must be **idempotent**: running it multiple times should not cause errors or duplicate work.

**Best Practices:**
1.  **Check First**: Use `list_search_indexes` or `$listSearchIndexes` to see if the index exists.
2.  **Compare Definitions**: If it exists, check if the definition has changed.
    *   *Match*: Do nothing (Success).
    *   *Mismatch*: Update the index (triggering a rebuild).
3.  **Handle Race Conditions**: Catch `OperationFailure` errors related to "IndexAlreadyExists" or "IndexNotFound" which can occur if multiple processes start simultaneously.

```python
try:
    await collection.create_search_index(model=my_model)
except OperationFailure as e:
    if "IndexAlreadyExists" in str(e):
        # Benign race condition - another process beat us to it
        pass 
    else:
        raise e
```

## 3. General Implementation Guidelines

### Handling `OperationFailure`

MongoDB operations can fail for various reasons (network, permissions, logic). Robust code explicitly catches `pymongo.errors.OperationFailure`.

*   **NamespaceNotFound**: Trying to drop an index that doesn't exist.
*   **IndexNotFound**: Trying to use an index that doesn't exist.
*   **CollectionInvalid**: Trying to create a collection that already exists (common race condition).

**Recommendation**: Always wrap administrative commands (create/drop/update) in try/except blocks that specifically handle these benign error states.

### Aggregation Pipelines

Aggregation is powerful but complex.

1.  **First Stage Rule**: `$vectorSearch`, `$search`, and `$geoNear` **MUST** be the very first stage in the pipeline. You cannot `$match` before them.
2.  **Filtering**: Use the `filter` property *inside* the `$vectorSearch` operator, not a separate `$match` stage, for efficient pre-filtering of vectors.

```python
# ✅ Correct: Filter inside vectorSearch
pipeline = [{
    "$vectorSearch": {
        "index": "default",
        "path": "embedding",
        "queryVector": [...],
        "filter": {"category": "A"}  # <-- Efficient pre-filtering
    }
}]

# ⚠️ Less Efficient: Match after search (scans more vectors)
pipeline = [
    {
        "$vectorSearch": { ... }
    },
    {
        "$match": {"category": "A"}  # <-- Filters results AFTER vector search
    }
]
```

## 4. Anti-Patterns to Avoid

*   **Fire-and-Forget Indexing**: Creating an index and immediately trying to query it without waiting. This leads to intermittent "Index not found" errors on startup.
*   **Ignoring 'queryable' Status**: Assuming `status: "READY"` means `queryable: true`. In some edge cases (updates), status might be ready while the index is still swapping. Always check `queryable`.
*   **Blocking Main Loop**: Using synchronous `pymongo` methods inside `async def` functions. This pauses the entire server for the duration of the DB call.

## 5. Reference Implementation

For a production-grade example of these patterns, examine `async_mongo_wrapper.py`:

*   **`AsyncAtlasIndexManager`**: Implements the `create_search_index` -> `_wait_for_search_index_ready` flow.
*   **`AutoIndexManager`**: Demonstrates how to analyze queries and create standard indexes dynamically.
*   **Idempotency Checks**: See how it compares `latestDefinition` to avoid unnecessary updates.
