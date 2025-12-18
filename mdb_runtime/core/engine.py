"""
Runtime Engine

The core orchestration engine for MDB_RUNTIME that manages:
- Database connections
- Experiment registration
- Authentication/authorization
- Index management
- Resource lifecycle

This module is part of MDB_RUNTIME - MongoDB Multi-Tenant Runtime Engine.
"""

import os
import asyncio
import logging
from typing import Optional, Dict, Any, List
from pathlib import Path

from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

# Import runtime components
from ..database import ScopedMongoWrapper
from .manifest import ManifestValidator, ManifestParser
from ..auth import AuthorizationProvider
from ..indexes import run_index_creation_for_collection

logger = logging.getLogger(__name__)


class RuntimeEngine:
    """
    Core runtime engine for managing multi-tenant experiments.
    
    This class orchestrates all runtime components including:
    - Database connections and scoping
    - Manifest validation and parsing
    - Experiment registration
    - Index management
    - Authentication/authorization setup
    """
    
    def __init__(
        self,
        mongo_uri: str,
        db_name: str,
        experiments_dir: Optional[Path] = None,
        authz_provider: Optional[AuthorizationProvider] = None,
        max_pool_size: int = 50,
        min_pool_size: int = 10,
    ):
        """
        Initialize the runtime engine.
        
        Args:
            mongo_uri: MongoDB connection URI
            db_name: Database name
            experiments_dir: Path to experiments directory (optional)
            authz_provider: Authorization provider instance (optional, can be set later)
            max_pool_size: Maximum MongoDB connection pool size
            min_pool_size: Minimum MongoDB connection pool size
        """
        self.mongo_uri = mongo_uri
        self.db_name = db_name
        self.experiments_dir = experiments_dir
        self.authz_provider = authz_provider
        self.max_pool_size = max_pool_size
        self.min_pool_size = min_pool_size
        
        # Runtime state
        self._mongo_client: Optional[AsyncIOMotorClient] = None
        self._mongo_db: Optional[AsyncIOMotorDatabase] = None
        self._initialized = False
        self._experiments: Dict[str, Dict[str, Any]] = {}
        
        # Validators
        self.manifest_validator = ManifestValidator()
        self.manifest_parser = ManifestParser()
    
    async def initialize(self) -> None:
        """
        Initialize the runtime engine.
        
        This method:
        1. Connects to MongoDB
        2. Validates the connection
        3. Sets up initial state
        
        Raises:
            RuntimeError: If initialization fails
        """
        if self._initialized:
            logger.warning("RuntimeEngine already initialized. Skipping re-initialization.")
            return
        
        logger.info(f"Initializing RuntimeEngine (MongoDB: {self.mongo_uri}, DB: {self.db_name})...")
        
        try:
            # Connect to MongoDB
            self._mongo_client = AsyncIOMotorClient(
                self.mongo_uri,
                serverSelectionTimeoutMS=5000,
                appname="MDB_RUNTIME",
                maxPoolSize=self.max_pool_size,
                minPoolSize=self.min_pool_size,
                maxIdleTimeMS=45000,
                retryWrites=True,
                retryReads=True,
            )
            
            # Verify connection
            await self._mongo_client.admin.command("ping")
            self._mongo_db = self._mongo_client[self.db_name]
            
            self._initialized = True
            logger.info(
                f"✔️ RuntimeEngine initialized successfully "
                f"(Database: '{self.db_name}', pool: {self.min_pool_size}-{self.max_pool_size})"
            )
        except Exception as e:
            logger.critical(f"❌ RuntimeEngine initialization failed: {e}", exc_info=True)
            raise RuntimeError(f"RuntimeEngine initialization failed: {e}") from e
    
    @property
    def mongo_client(self) -> AsyncIOMotorClient:
        """Get the MongoDB client."""
        if not self._initialized:
            raise RuntimeError("RuntimeEngine not initialized. Call initialize() first.")
        return self._mongo_client
    
    @property
    def mongo_db(self) -> AsyncIOMotorDatabase:
        """Get the MongoDB database."""
        if not self._initialized:
            raise RuntimeError("RuntimeEngine not initialized. Call initialize() first.")
        return self._mongo_db
    
    def get_scoped_db(
        self,
        experiment_slug: str,
        read_scopes: Optional[List[str]] = None,
        write_scope: Optional[str] = None,
        auto_index: bool = True
    ) -> ScopedMongoWrapper:
        """
        Get a scoped database wrapper for an experiment.
        
        Args:
            experiment_slug: Experiment slug
            read_scopes: List of experiment slugs to read from (defaults to [experiment_slug])
            write_scope: Experiment slug to write to (defaults to experiment_slug)
            auto_index: Whether to enable automatic index creation
        
        Returns:
            ScopedMongoWrapper instance
        """
        if not self._initialized:
            raise RuntimeError("RuntimeEngine not initialized. Call initialize() first.")
        
        if read_scopes is None:
            read_scopes = [experiment_slug]
        if write_scope is None:
            write_scope = experiment_slug
        
        return ScopedMongoWrapper(
            real_db=self._mongo_db,
            read_scopes=read_scopes,
            write_scope=write_scope,
            auto_index=auto_index
        )
    
    async def validate_manifest(self, manifest: Dict[str, Any]) -> tuple[bool, Optional[str], Optional[List[str]]]:
        """
        Validate a manifest against the schema.
        
        Args:
            manifest: Manifest dictionary
        
        Returns:
            Tuple of (is_valid, error_message, error_paths)
        """
        return self.manifest_validator.validate(manifest)
    
    async def load_manifest(self, path: Path) -> Dict[str, Any]:
        """
        Load and validate a manifest from a file.
        
        Args:
            path: Path to manifest.json file
        
        Returns:
            Validated manifest dictionary
        
        Raises:
            FileNotFoundError: If file doesn't exist
            ValueError: If validation fails
        """
        return await self.manifest_parser.load_from_file(path, validate=True)
    
    async def register_experiment(
        self,
        manifest: Dict[str, Any],
        create_indexes: bool = True
    ) -> bool:
        """
        Register an experiment from its manifest.
        
        Args:
            manifest: Validated manifest dictionary
            create_indexes: Whether to create managed indexes
        
        Returns:
            True if registration successful, False otherwise
        """
        if not self._initialized:
            raise RuntimeError("RuntimeEngine not initialized. Call initialize() first.")
        
        slug = manifest.get("slug")
        if not slug:
            logger.error("Cannot register experiment: missing 'slug' in manifest")
            return False
        
        # Validate manifest
        is_valid, error, paths = await self.validate_manifest(manifest)
        if not is_valid:
            error_path_str = f" (errors in: {', '.join(paths[:3])})" if paths else ""
            logger.error(
                f"[{slug}] ❌ Registration BLOCKED: Manifest validation failed: {error}{error_path_str}"
            )
            return False
        
        # Store experiment config
        self._experiments[slug] = manifest
        
        # Create indexes if requested
        if create_indexes and "managed_indexes" in manifest:
            await self._create_experiment_indexes(slug, manifest)
        
        logger.info(f"[{slug}] ✔️ Experiment registered successfully")
        return True
    
    async def _create_experiment_indexes(
        self,
        slug: str,
        manifest: Dict[str, Any]
    ) -> None:
        """
        Create managed indexes for an experiment.
        
        Args:
            slug: Experiment slug
            manifest: Experiment manifest
        """
        try:
            from ..indexes import validate_managed_indexes
        except ImportError:
            from mdb_runtime.indexes import validate_managed_indexes
        
        managed_indexes = manifest.get("managed_indexes", {})
        if not managed_indexes:
            return
        
        # Validate indexes
        is_valid, error = validate_managed_indexes(managed_indexes)
        if not is_valid:
            logger.warning(
                f"[{slug}] ⚠️ Invalid 'managed_indexes' configuration: {error}. "
                f"Skipping index creation."
            )
            return
        
        # Create indexes for each collection
        for collection_base_name, indexes in managed_indexes.items():
            if not collection_base_name or not isinstance(indexes, list):
                logger.warning(f"[{slug}] Invalid 'managed_indexes' for '{collection_base_name}'.")
                continue
            
            prefixed_collection_name = f"{slug}_{collection_base_name}"
            prefixed_defs = []
            
            for idx_def in indexes:
                idx_n = idx_def.get("name")
                if not idx_n or not idx_def.get("type"):
                    logger.warning(f"[{slug}] Skipping malformed index def in '{collection_base_name}'.")
                    continue
                
                idx_copy = idx_def.copy()
                idx_copy["name"] = f"{slug}_{idx_n}"
                prefixed_defs.append(idx_copy)
            
            if not prefixed_defs:
                continue
            
            logger.info(f"[{slug}] Creating indexes for '{prefixed_collection_name}'...")
            try:
                await run_index_creation_for_collection(
                    db=self._mongo_db,
                    slug=slug,
                    collection_name=prefixed_collection_name,
                    index_definitions=prefixed_defs
                )
            except Exception as e:
                logger.error(
                    f"[{slug}] Error creating indexes for '{prefixed_collection_name}': {e}",
                    exc_info=True
                )
    
    async def reload_experiments(self) -> int:
        """
        Reload all active experiments from the database.
        
        Returns:
            Number of experiments registered
        """
        if not self._initialized:
            raise RuntimeError("RuntimeEngine not initialized. Call initialize() first.")
        
        logger.info("Reloading active experiments from database...")
        
        try:
            # Fetch active experiments
            active_cfgs = await self._mongo_db.experiments_config.find(
                {"status": "active"}
            ).limit(500).to_list(None)
            
            logger.info(f"Found {len(active_cfgs)} active experiment(s).")
            
            # Clear existing registrations
            self._experiments.clear()
            
            # Register each experiment
            registered_count = 0
            for cfg in active_cfgs:
                success = await self.register_experiment(cfg, create_indexes=True)
                if success:
                    registered_count += 1
            
            logger.info(f"✔️ Experiment reload complete. {registered_count} experiment(s) registered.")
            return registered_count
        except Exception as e:
            logger.error(f"❌ Error reloading experiments: {e}", exc_info=True)
            return 0
    
    def get_experiment(self, slug: str) -> Optional[Dict[str, Any]]:
        """
        Get experiment configuration by slug.
        
        Args:
            slug: Experiment slug
        
        Returns:
            Experiment manifest dict or None if not found
        """
        return self._experiments.get(slug)
    
    def list_experiments(self) -> List[str]:
        """
        List all registered experiment slugs.
        
        Returns:
            List of experiment slugs
        """
        return list(self._experiments.keys())
    
    async def shutdown(self) -> None:
        """
        Shutdown the runtime engine and clean up resources.
        """
        if not self._initialized:
            return
        
        logger.info("Shutting down RuntimeEngine...")
        
        # Close MongoDB connection
        if self._mongo_client:
            self._mongo_client.close()
            logger.info("MongoDB connection closed.")
        
        self._initialized = False
        self._experiments.clear()
        logger.info("✔️ RuntimeEngine shutdown complete.")
    
    def __enter__(self):
        """Context manager entry (synchronous)."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit (synchronous)."""
        # Note: This is synchronous, so we can't await shutdown
        # Users should call await shutdown() explicitly
        pass
    
    async def __aenter__(self):
        """Async context manager entry."""
        await self.initialize()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.shutdown()

