"""
Game Portal Actor
A Ray Actor that handles all Game Portal operations.
"""

import logging
import random
import string
import pathlib
from typing import Dict, Any, List, Optional
from datetime import datetime
import ray
from bson import ObjectId

logger = logging.getLogger(__name__)

# Actor-local paths
experiment_dir = pathlib.Path(__file__).parent
templates_dir = experiment_dir / "templates"

# Import game logic modules
from .blackjack_logic import (
    create_new_game as create_blackjack_game,
    play_move as play_blackjack_move
)
from .dominoes_logic import (
    create_new_game as create_dominoes_game,
    play_move as play_dominoes_move,
    get_open_ends
)


@ray.remote
class ExperimentActor:
    """
    Game Portal Ray Actor.
    Handles all Game Portal operations using the experiment database abstraction.
    """

    def __init__(self, mongo_uri: str, db_name: str, write_scope: str, read_scopes: List[str]):
        self.mongo_uri = mongo_uri
        self.db_name = db_name
        self.write_scope = write_scope
        self.read_scopes = read_scopes
        
        # Load templates
        try:
            from fastapi.templating import Jinja2Templates
            
            if templates_dir.is_dir():
                self.templates = Jinja2Templates(directory=str(templates_dir))
            else:
                self.templates = None
                logger.warning(f"[{write_scope}-Actor] Template dir not found at {templates_dir}")
            
            logger.info(f"[{write_scope}-Actor] Successfully loaded templates.")
        except ImportError as e:
            logger.critical(f"[{write_scope}-Actor] ❌ CRITICAL: Failed to load templates: {e}", exc_info=True)
            self.templates = None
        
        # Database initialization
        try:
            from experiment_db import create_actor_database
            self.db = create_actor_database(
                mongo_uri,
                db_name,
                write_scope,
                read_scopes
            )
            logger.info(
                f"[{write_scope}-Actor] initialized with write_scope='{self.write_scope}' "
                f"(DB='{db_name}') using magical database abstraction"
            )
        except Exception as e:
            logger.critical(f"[{write_scope}-Actor] ❌ CRITICAL: Failed to init DB: {e}", exc_info=True)
            self.db = None
        
        # In-memory game storage (for active games)
        self.games: Dict[str, Dict[str, Any]] = {}
        self.ai_players: Dict[str, List[str]] = {}  # game_id -> list of AI player IDs
        self.spectators: Dict[str, List[str]] = {}  # game_id -> list of spectator player IDs

    def _check_ready(self):
        """Check if actor is ready."""
        if not self.db:
            raise RuntimeError("Database not initialized. Check logs for import errors.")
    
    def _generate_game_id(self) -> str:
        """Generate a unique game ID."""
        return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    
    def _get_min_players(self, game_type: str, game_mode: str = "classic") -> int:
        """Get minimum players required for a game type."""
        if game_type == "dominoes":
            return 4 if game_mode == "boricua" else 2
        elif game_type == "blackjack":
            return 2
        return 2
    
    def _get_max_players(self, game_type: str) -> int:
        """Get maximum players allowed for a game type."""
        # Maximum is 4 players total for all game types
        return 4

    async def create_game(self, player_id: str, game_type: str, game_mode: str = "classic", ai_count: int = 0, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Create a new game lobby with optional AI players and metadata."""
        self._check_ready()
        
        game_id = self._generate_game_id()
        while game_id in self.games:
            game_id = self._generate_game_id()
        
        min_players = self._get_min_players(game_type, game_mode)
        max_players = self._get_max_players(game_type)
        
        # Start with just the host
        players = [player_id]
        ai_players = []
        
        # Clamp ai_count to valid range (0-3, max 3 AI players)
        ai_count = max(0, min(3, ai_count))
        
        # Calculate how many AI players we need
        # 1. Ensure minimum players are met (at least min_players - 1 AI, since host is 1)
        min_ai_needed = max(0, min_players - 1)
        
        # 2. Use the requested ai_count, but ensure minimum is met
        # If user wants 0 but minimum requires 2, we add 2
        # If user wants 3 and minimum requires 2, we add 3
        final_ai_count = max(min_ai_needed, ai_count)
        
        # 3. Don't exceed max_players (max_players is 4 total, so max AI is 3)
        final_ai_count = min(final_ai_count, max_players - 1, 3)
        
        # Add AI players
        for i in range(final_ai_count):
            ai_id = f"AI_{game_id}_{i}"
            players.append(ai_id)
            ai_players.append(ai_id)
        
        game = {
            "game_id": game_id,
            "host_id": player_id,
            "game_type": game_type,
            "game_mode": game_mode,
            "players": players,
            "status": "waiting",
            "game_state": None,
            "created_at": datetime.utcnow(),
            "min_players": min_players,
            "max_players": max_players
        }
        
        # Add metadata if provided (e.g., circle_id for mycircles integration)
        if metadata:
            game["metadata"] = metadata
        
        self.games[game_id] = game
        self.ai_players[game_id] = ai_players
        self.spectators[game_id] = []
        
        # Persist game to database (for durability and analytics)
        try:
            game_doc = game.copy()
            game_doc["_id"] = game_id
            game_doc["ai_players"] = ai_players
            game_doc["spectators"] = []
            # Include metadata if present
            if metadata:
                game_doc["metadata"] = metadata
            # Convert datetime to ISO format for JSON serialization
            if game_doc.get("created_at"):
                game_doc["created_at"] = game_doc["created_at"].isoformat()
            await self.db.games.replace_one(
                {"_id": game_id},
                game_doc,
                upsert=True
            )
            logger.debug(f"Persisted game {game_id} to database")
        except Exception as e:
            # Log but don't fail - in-memory state is primary
            logger.warning(f"Failed to persist game {game_id} to database: {e}")
        
        logger.info(f"Created game {game_id} by {player_id} ({game_type}, {game_mode}) with {len(players)} players ({len(ai_players)} AI)")
        
        return {
            "game_id": game_id,
            "player_id": player_id,
            "game_type": game_type,
            "game_mode": game_mode,
            "players": players,
            "ai_players": ai_players
        }

    def join_game(self, game_id: str, player_id: str, replace_ai: Optional[str] = None, as_spectator: bool = False) -> Dict[str, Any]:
        """Join an existing game. Can replace AI or join as spectator if game is in progress."""
        self._check_ready()
        
        if game_id not in self.games:
            return {"error": "Game not found"}
        
        game = self.games[game_id]
        
        # Filter out placeholder players and temporary player IDs from the players list
        game["players"] = [
            p for p in game["players"] 
            if not (isinstance(p, str) and (
                p.startswith("PLACEHOLDER_") or 
                (p.startswith("player_") and len(p) > 7 and p[7:].isalnum())
            ))
        ]
        
        # Also update host_id if it's a placeholder/temporary player
        current_host_id = game.get("host_id")
        if current_host_id and (current_host_id.startswith("PLACEHOLDER_") or (current_host_id.startswith("player_") and len(current_host_id) > 7 and current_host_id[7:].isalnum())):
            # Clear host_id if it's a placeholder/temporary player - first real player will become host
            game["host_id"] = None
            logger.info(f"Cleared temporary host_id {current_host_id} - will be set when first real player joins")
        
        # Check if already in game
        if player_id in game["players"]:
            return {"error": "You are already in this game"}
        
        if player_id in self.spectators.get(game_id, []):
            return {"error": "You are already spectating this game"}
        
        # If game is waiting, join normally
        if game["status"] == "waiting":
            max_players = game.get("max_players", self._get_max_players(game["game_type"]))
            if len(game["players"]) >= max_players:
                return {"error": "Game is full"}
            
            # First, check if there's a placeholder player to replace (legacy support)
            # Also check for temporary player IDs (like "player_xxxxx" from _get_or_generate_player_id)
            placeholder_to_replace = None
            for p in game["players"]:
                if isinstance(p, str):
                    # Check for PLACEHOLDER_ prefix
                    if p.startswith("PLACEHOLDER_"):
                        placeholder_to_replace = p
                        break
                    # Check for temporary player IDs (pattern: "player_" followed by alphanumeric)
                    # These are generated by _get_or_generate_player_id and should be replaced
                    if p.startswith("player_") and len(p) > 7 and p[7:].isalnum():
                        placeholder_to_replace = p
                        break
            
            if placeholder_to_replace:
                # Replace the placeholder/temporary player
                placeholder_index = game["players"].index(placeholder_to_replace)
                game["players"][placeholder_index] = player_id
                # Always update host_id if the placeholder/temporary player was the host
                # This ensures the real browser fingerprint becomes the host
                if game.get("host_id") == placeholder_to_replace:
                    game["host_id"] = player_id
                    logger.info(f"Updated host_id from temporary player {placeholder_to_replace} to real player {player_id}")
                logger.info(f"Player {player_id} replaced placeholder/temporary player {placeholder_to_replace} in game {game_id}")
                return {
                    "game_id": game_id,
                    "player_id": player_id,
                    "game_type": game["game_type"],
                    "game_mode": game["game_mode"],
                    "replaced_placeholder": placeholder_to_replace
                }
            
            # No placeholder, check if we should replace an AI player or just add
            ai_to_replace = None
            if self.ai_players.get(game_id):
                ai_to_replace = self.ai_players[game_id][0]
                # Safety check: ensure AI player is actually in the players list
                if ai_to_replace in game["players"]:
                    ai_index = game["players"].index(ai_to_replace)
                    game["players"][ai_index] = player_id
                    self.ai_players[game_id].remove(ai_to_replace)
                    logger.info(f"Player {player_id} replaced AI {ai_to_replace} in game {game_id}")
                else:
                    # AI player not in players list, just add normally
                    game["players"].append(player_id)
                    logger.info(f"Player {player_id} joined game {game_id} (AI replacement skipped)")
            else:
                # No AI to replace, just add the player
                game["players"].append(player_id)
                logger.info(f"Player {player_id} joined game {game_id}")
            
            # Set host_id if this is the first player OR if host_id is a temporary/placeholder player OR if lobby is empty
            # This ensures the real browser fingerprint becomes the host, not a temporary ID
            # Magical experience: whoever joins an empty lobby becomes the host!
            current_host_id = game.get("host_id")
            # Check if lobby is effectively empty (no real players before this join)
            # Note: player_id was just added, so check if there was only 1 player (the one we just added)
            was_empty = len(game["players"]) == 1  # Just added this player
            
            if current_host_id is None or was_empty:
                # Empty lobby or no host - first player becomes host
                game["host_id"] = player_id
                logger.info(f"✨ Magical! Set host_id to player {player_id} (empty lobby or first player)")
            elif current_host_id.startswith("PLACEHOLDER_") or (current_host_id.startswith("player_") and len(current_host_id) > 7 and current_host_id[7:].isalnum()):
                # Host is a placeholder/temporary player - replace with real player
                game["host_id"] = player_id
                logger.info(f"Replaced temporary host_id {current_host_id} with real player {player_id}")
            elif current_host_id not in game["players"]:
                # Host left the game - new player becomes host
                game["host_id"] = player_id
                logger.info(f"✨ Magical! Previous host left, new player {player_id} becomes host")
            
            return {
                "game_id": game_id,
                "player_id": player_id,
                "game_type": game["game_type"],
                "game_mode": game["game_mode"],
                "replaced_ai": ai_to_replace if ai_to_replace else None
            }
        
        # If game is in progress, handle mid-game joining
        if game["status"] in ["in_progress", "round_finished", "hand_finished"]:
            if as_spectator:
                # Join as spectator
                if game_id not in self.spectators:
                    self.spectators[game_id] = []
                self.spectators[game_id].append(player_id)
                logger.info(f"Player {player_id} joined as spectator in game {game_id}")
                return {
                    "game_id": game_id,
                    "player_id": player_id,
                    "game_type": game["game_type"],
                    "game_mode": game["game_mode"],
                    "role": "spectator"
                }
            
            # Try to replace an AI player
            if replace_ai:
                if replace_ai not in self.ai_players.get(game_id, []):
                    return {"error": "Cannot replace: player is not an AI"}
                if replace_ai not in game["players"]:
                    return {"error": "Cannot replace: AI player not found in game"}
                
                ai_index = game["players"].index(replace_ai)
                game["players"][ai_index] = player_id
                self.ai_players[game_id].remove(replace_ai)
                logger.info(f"Player {player_id} replaced AI {replace_ai} mid-game in {game_id}")
                return {
                    "game_id": game_id,
                    "player_id": player_id,
                    "game_type": game["game_type"],
                    "game_mode": game["game_mode"],
                    "role": "player",
                    "replaced_ai": replace_ai
                }
            
            # No AI to replace, join as spectator
            if game_id not in self.spectators:
                self.spectators[game_id] = []
            self.spectators[game_id].append(player_id)
            logger.info(f"Player {player_id} joined as spectator in game {game_id} (no AI to replace)")
            return {
                "game_id": game_id,
                "player_id": player_id,
                "game_type": game["game_type"],
                "game_mode": game["game_mode"],
                "role": "spectator"
            }
        
        return {"error": "Cannot join game in current state"}

    async def start_game(self, game_id: str, player_id: str) -> Dict[str, Any]:
        """Start a game. Auto-fills missing players with AI to meet minimum requirements."""
        self._check_ready()
        
        if game_id not in self.games:
            return {"error": "Game not found"}
        
        game = self.games[game_id]
        
        # Filter out placeholder players first (before any checks)
        game["players"] = [p for p in game["players"] if not (isinstance(p, str) and p.startswith("PLACEHOLDER_"))]
        
        # Allow any player in the game to start (not just host)
        # This allows players joining from mycircles to start the game
        if player_id not in game["players"]:
            return {"error": "You must be in the game to start it"}
        
        if game["status"] != "waiting":
            return {"error": "Game has already started"}
        
        min_players = game.get("min_players", self._get_min_players(game["game_type"], game["game_mode"]))
        max_players = game.get("max_players", self._get_max_players(game["game_type"]))
        
        # Auto-fill missing players with AI to meet minimum requirements
        current_players = len(game["players"])
        existing_ai_count = len(self.ai_players.get(game_id, []))
        
        if current_players < min_players:
            # Calculate how many AI players we need
            ai_needed = min_players - current_players
            # Don't exceed max_players
            total_after_ai = current_players + ai_needed
            if total_after_ai > max_players:
                ai_needed = max_players - current_players
            
            # Add AI players
            for i in range(ai_needed):
                ai_id = f"AI_{game_id}_{existing_ai_count + i}"
                game["players"].append(ai_id)
                if game_id not in self.ai_players:
                    self.ai_players[game_id] = []
                self.ai_players[game_id].append(ai_id)
                logger.info(f"Auto-filled player slot with AI: {ai_id}")
        
        # Ensure we have at least 1 player (the person starting)
        if len(game["players"]) == 0:
            return {"error": "Cannot start game with 0 players. At least 1 player is required."}
        
        # Update host_id if it was None (first player to start becomes host)
        if game.get("host_id") is None and game["players"]:
            game["host_id"] = game["players"][0]
        
        # Verify we have enough players after auto-fill (should always be true now)
        if len(game["players"]) < min_players:
            return {"error": f"Need at least {min_players} players to start (have {len(game['players'])})"}
        
        # Initialize game state
        try:
            if game["game_type"] == "blackjack":
                game_state = create_blackjack_game(game["players"], game["game_mode"])
            elif game["game_type"] == "dominoes":
                game_state = create_dominoes_game(game["players"], game["game_mode"])
            else:
                return {"error": f"Unknown game type: {game['game_type']}"}
        except ValueError as e:
            # Game creation functions validate player count
            return {"error": f"Failed to create game: {str(e)}"}
        
        game["game_state"] = game_state
        game["status"] = "in_progress"
        game["started_at"] = datetime.utcnow()
        
        # Persist game state update to database
        try:
            game_doc = game.copy()
            game_doc["_id"] = game_id
            game_doc["ai_players"] = self.ai_players.get(game_id, [])
            game_doc["spectators"] = self.spectators.get(game_id, [])
            # Convert datetime to ISO format
            if game_doc.get("created_at") and isinstance(game_doc["created_at"], datetime):
                game_doc["created_at"] = game_doc["created_at"].isoformat()
            if game_doc.get("started_at") and isinstance(game_doc["started_at"], datetime):
                game_doc["started_at"] = game_doc["started_at"].isoformat()
            await self.db.games.replace_one(
                {"_id": game_id},
                game_doc,
                upsert=True
            )
            logger.debug(f"Persisted game {game_id} start to database")
        except Exception as e:
            logger.warning(f"Failed to persist game {game_id} start to database: {e}")
        
        logger.info(f"Game {game_id} started with {len(game['players'])} players ({len(self.ai_players.get(game_id, []))} AI)")
        
        return {"success": True}

    def _serialize_datetime(self, obj: Any) -> Any:
        """Recursively convert datetime objects to ISO format strings for serialization."""
        if isinstance(obj, datetime):
            return obj.isoformat()
        elif isinstance(obj, dict):
            return {key: self._serialize_datetime(value) for key, value in obj.items()}
        elif isinstance(obj, list):
            return [self._serialize_datetime(item) for item in obj]
        return obj

    def get_game(self, game_id: str) -> Optional[Dict[str, Any]]:
        """Get game information."""
        self._check_ready()
        
        if game_id not in self.games:
            return None
        
        game = self.games[game_id].copy()
        # Add AI and spectator info
        game["ai_players"] = self.ai_players.get(game_id, [])
        game["spectators"] = self.spectators.get(game_id, [])
        
        # Filter out placeholder players and temporary player IDs from the players list
        game["players"] = [
            p for p in game.get("players", []) 
            if not (isinstance(p, str) and (
                p.startswith("PLACEHOLDER_") or 
                (p.startswith("player_") and len(p) > 7 and p[7:].isalnum())
            ))
        ]
        
        # Clean up host_id if it's a placeholder/temporary player or if host left
        current_host_id = game.get("host_id")
        if current_host_id:
            # Check if host is a temporary/placeholder player
            is_temporary = (current_host_id.startswith("PLACEHOLDER_") or 
                          (current_host_id.startswith("player_") and len(current_host_id) > 7 and current_host_id[7:].isalnum()))
            
            # Check if host is still in the players list
            host_in_players = current_host_id in game["players"]
            
            if is_temporary or not host_in_players:
                # Host is temporary or left - clear it (next player to join will become host)
                if game["players"]:
                    # If there are players, set the first one as host
                    game["host_id"] = game["players"][0]
                    logger.info(f"✨ Updated host_id from {current_host_id} to first player {game['players'][0]}")
                else:
                    # Empty lobby - clear host_id (magical: next player to join becomes host)
                    game["host_id"] = None
                    logger.info(f"✨ Lobby is empty - host_id cleared. Next player to join will become host!")
        
        # Convert datetime objects to ISO format for serialization (Ray/FastAPI)
        return self._serialize_datetime(game)
    
    def find_waiting_lobby(self, circle_id: str, game_type: str) -> Optional[Dict[str, Any]]:
        """Find an existing waiting lobby for a circle and game type."""
        self._check_ready()
        
        # Search in-memory games first (faster)
        for game_id, game in self.games.items():
            if (game.get("status") == "waiting" and 
                game.get("game_type") == game_type and
                game.get("metadata", {}).get("circle_id") == circle_id):
                # Found a waiting lobby for this circle/game type
                result = game.copy()
                result["ai_players"] = self.ai_players.get(game_id, [])
                result["spectators"] = self.spectators.get(game_id, [])
                return self._serialize_datetime(result)
        
        # If not found in memory, try database (for durability across restarts)
        try:
            # Query database for waiting lobbies with matching circle_id and game_type
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If we're in an async context, we can't use sync database calls
                # Return None and let the caller create a new game
                return None
            else:
                # This is a sync method, so we can't easily query async database
                # For now, just return None - in-memory search is primary
                return None
        except Exception as e:
            logger.debug(f"Could not search database for waiting lobby: {e}")
            return None
    
    async def get_or_create_lobby(self, circle_id: str, game_type: str, game_mode: Optional[str] = None) -> Dict[str, Any]:
        """
        Get or create a persistent lobby for a circle and game type.
        Lobbies always exist - can have 0-4 players. Missing players are auto-filled with AI when starting.
        """
        self._check_ready()
        
        # First, try to find existing waiting lobby
        existing_lobby = self.find_waiting_lobby(circle_id, game_type)
        if existing_lobby:
            game_id = existing_lobby.get("game_id")
            if game_id:
                # Return existing lobby (filter out placeholders from players list)
                result = existing_lobby.copy()
                # Filter out placeholder players
                result["players"] = [p for p in result.get("players", []) if not (isinstance(p, str) and p.startswith("PLACEHOLDER_"))]
                result["player_count"] = len(result["players"])
                return result
        
        # No existing lobby found, create a new empty one
        # Generate a stable game_id based on circle_id and game_type
        import hashlib
        lobby_key = f"{circle_id}_{game_type}"
        lobby_hash = hashlib.sha256(lobby_key.encode()).hexdigest()[:6].upper()
        game_id = f"LOBBY_{lobby_hash}"
        
        # If lobby already exists with this ID, check its status
        if game_id in self.games:
            existing_game = self.games[game_id]
            if existing_game.get("status") == "waiting":
                # Reuse waiting lobby
                result = existing_game.copy()
                result["ai_players"] = self.ai_players.get(game_id, [])
                result["spectators"] = self.spectators.get(game_id, [])
                # Filter out placeholder players
                result["players"] = [p for p in result.get("players", []) if not (isinstance(p, str) and p.startswith("PLACEHOLDER_"))]
                result["player_count"] = len(result["players"])
                return self._serialize_datetime(result)
            elif existing_game.get("status") in ["finished", "in_progress", "round_finished", "hand_finished"]:
                # Game is in progress or finished - reset lobby to waiting state for reuse
                logger.info(f"Resetting finished/in-progress lobby {game_id} to waiting state")
                existing_game["status"] = "waiting"
                existing_game["game_state"] = None
                existing_game["players"] = []  # Clear players - they need to rejoin
                existing_game["host_id"] = None
                self.ai_players[game_id] = []
                self.spectators[game_id] = []
                # Persist reset lobby
                try:
                    game_doc = existing_game.copy()
                    game_doc["_id"] = game_id
                    game_doc["ai_players"] = []
                    game_doc["spectators"] = []
                    if game_doc.get("created_at") and isinstance(game_doc["created_at"], datetime):
                        game_doc["created_at"] = game_doc["created_at"].isoformat()
                    await self.db.games.replace_one(
                        {"_id": game_id},
                        game_doc,
                        upsert=True
                    )
                except Exception as e:
                    logger.warning(f"Failed to persist reset lobby {game_id}: {e}")
                # Return reset lobby
                result = existing_game.copy()
                result["ai_players"] = []
                result["spectators"] = []
                result["player_count"] = 0
                return self._serialize_datetime(result)
        
        # Set default game_mode if not provided
        if game_mode is None:
            if game_type == "dominoes":
                game_mode = "classic"
            else:  # blackjack
                game_mode = "best_of_5"
        
        min_players = self._get_min_players(game_type, game_mode)
        max_players = self._get_max_players(game_type)
        
        # Create empty lobby (0 players initially)
        game = {
            "game_id": game_id,
            "host_id": None,  # No host until first player joins
            "game_type": game_type,
            "game_mode": game_mode,
            "players": [],  # Empty lobby - can have 0-4 players
            "status": "waiting",
            "game_state": None,
            "created_at": datetime.utcnow(),
            "min_players": min_players,
            "max_players": max_players,
            "metadata": {"circle_id": circle_id}
        }
        
        self.games[game_id] = game
        self.ai_players[game_id] = []  # No AI initially
        self.spectators[game_id] = []
        
        # Persist lobby to database
        try:
            game_doc = game.copy()
            game_doc["_id"] = game_id
            game_doc["ai_players"] = []
            game_doc["spectators"] = []
            if game_doc.get("created_at"):
                game_doc["created_at"] = game_doc["created_at"].isoformat()
            await self.db.games.replace_one(
                {"_id": game_id},
                game_doc,
                upsert=True
            )
            logger.debug(f"Persisted lobby {game_id} to database")
        except Exception as e:
            logger.warning(f"Failed to persist lobby {game_id} to database: {e}")
        
        logger.info(f"Created/retrieved lobby {game_id} for circle {circle_id} ({game_type}, {game_mode}) with {len(game['players'])} players")
        
        result = game.copy()
        result["ai_players"] = []
        result["spectators"] = []
        result["player_count"] = 0
        return self._serialize_datetime(result)
    
    def get_available_ai_slots(self, game_id: str) -> List[str]:
        """Get list of AI players that can be replaced."""
        if game_id not in self.games:
            return []
        
        game = self.games[game_id]
        if game["status"] not in ["in_progress", "round_finished", "hand_finished"]:
            return []
        
        return self.ai_players.get(game_id, [])
    
    def is_spectator(self, game_id: str, player_id: str) -> bool:
        """Check if a player is a spectator."""
        if game_id not in self.spectators:
            return False
        return player_id in self.spectators[game_id]

    def is_ai_player(self, game_id: str, player_id: str) -> bool:
        """Check if a player is an AI."""
        if game_id not in self.ai_players:
            return False
        return player_id in self.ai_players[game_id]

    def sanitize_game_state_for_player(self, game_type: str, game_state: Dict[str, Any], player_id: str) -> Optional[Dict[str, Any]]:
        """Sanitize game state to hide information from other players."""
        if not game_state:
            return None
        
        sanitized = game_state.copy()
        
        if game_type == "blackjack":
            # Hide other players' hands
            if "hands" in sanitized:
                hands = sanitized["hands"].copy()
                for pid, hand_data in hands.items():
                    if pid != player_id:
                        # Show only hand value, not cards
                        hands[pid] = {
                            "value": hand_data.get("value", 0),
                            "status": hand_data.get("status", "playing"),
                            "bet": hand_data.get("bet", 0)
                        }
                sanitized["hands"] = hands
            
            # Hide dealer's second card until all players are done
            if "dealer_hand" in sanitized and sanitized.get("status") == "in_progress":
                dealer_hand = sanitized["dealer_hand"].copy()
                if len(dealer_hand) > 1:
                    dealer_hand[1] = {"rank": "?", "suit": "?", "value": 0}
                sanitized["dealer_hand"] = dealer_hand
        
        elif game_type == "dominoes":
            # Hide other players' hands
            if "hands" in sanitized:
                hands = sanitized["hands"].copy()
                for pid, hand in hands.items():
                    if pid != player_id:
                        # Show only hand size
                        hands[pid] = len(hand) if isinstance(hand, list) else 0
                sanitized["hands"] = hands
        
        return sanitized

    async def play_move(self, game_id: str, player_id: str, move_data: Dict[str, Any], allow_ai: bool = False) -> Dict[str, Any]:
        """Process a player's move."""
        self._check_ready()
        
        if game_id not in self.games:
            return {"error": "Game not found"}
        
        game = self.games[game_id]
        
        if game["status"] != "in_progress":
            return {"error": "Game is not in progress"}
        
        if player_id not in game["players"]:
            return {"error": "Player not in game"}
        
        # Only block AI moves if not explicitly allowed (prevents manual AI moves via WebSocket)
        if not allow_ai and self.is_ai_player(game_id, player_id):
            return {"error": "AI players cannot make manual moves"}
        
        game_state = game["game_state"]
        game_type = game["game_type"]
        
        try:
            if game_type == "blackjack":
                new_state = play_blackjack_move(game_state, player_id, move_data)
            elif game_type == "dominoes":
                new_state = play_dominoes_move(game_state, player_id, move_data)
            else:
                return {"error": f"Unknown game type: {game_type}"}
            
            game["game_state"] = new_state
            
            # Check if game is finished
            if new_state.get("status") in ["finished", "hand_finished", "round_finished"]:
                game["status"] = new_state["status"]
                
                # If game is completely finished, persist final state
                if new_state.get("status") == "finished":
                    game["finished_at"] = datetime.utcnow()
                    try:
                        game_doc = game.copy()
                        game_doc["_id"] = game_id
                        game_doc["ai_players"] = self.ai_players.get(game_id, [])
                        game_doc["spectators"] = self.spectators.get(game_id, [])
                        # Convert datetime to ISO format
                        for date_field in ["created_at", "started_at", "finished_at"]:
                            if game_doc.get(date_field) and isinstance(game_doc[date_field], datetime):
                                game_doc[date_field] = game_doc[date_field].isoformat()
                        await self.db.games.replace_one(
                            {"_id": game_id},
                            game_doc,
                            upsert=True
                        )
                        logger.info(f"Persisted finished game {game_id} to database")
                    except Exception as e:
                        logger.warning(f"Failed to persist finished game {game_id} to database: {e}")
                
                # Automatically mark AI players as ready for next round/hand
                if new_state.get("status") == "round_finished":
                    # Auto-ready AI players for next round
                    if "ready_for_next_round" not in new_state:
                        new_state["ready_for_next_round"] = {}
                    ai_players_list = self.ai_players.get(game_id, [])
                    all_players = game.get("players", [])
                    
                    # Mark all AI players as ready
                    for ai_id in ai_players_list:
                        if ai_id in game["players"]:
                            new_state["ready_for_next_round"][ai_id] = True
                            logger.info(f"Auto-marked AI player {ai_id} as ready for next round")
                    
                    # Also auto-mark ALL human players as ready
                    # This allows the round to auto-start immediately
                    for pid in all_players:
                        if pid not in ai_players_list:
                            new_state["ready_for_next_round"][pid] = True
                            logger.info(f"Auto-marked human player {pid} as ready for next round")
                    
                    # Check if all players are ready (including AI and human)
                    ready_players = new_state.get("ready_for_next_round", {})
                    all_ready = len(ready_players) >= len(all_players)
                    logger.info(f"Round finished - Ready check: {len(ready_players)}/{len(all_players)} players ready. AI players: {ai_players_list}, All players: {all_players}, Ready: {list(ready_players.keys())}")
                    if all_ready:
                        logger.info(f"All players ready for next round, will auto-start")
                        return {"success": True, "all_ready_for_next_round": True}
                elif new_state.get("status") == "hand_finished":
                    # Auto-ready AI players for next hand
                    if "ready_for_next_hand" not in new_state:
                        new_state["ready_for_next_hand"] = {}
                    ai_players_list = self.ai_players.get(game_id, [])
                    all_players = game.get("players", [])
                    
                    # Mark all AI players as ready
                    for ai_id in ai_players_list:
                        if ai_id in game["players"]:
                            new_state["ready_for_next_hand"][ai_id] = True
                            logger.info(f"Auto-marked AI player {ai_id} as ready for next hand")
                    
                    # Also auto-mark ALL human players as ready
                    # This allows the hand to auto-start immediately
                    for pid in all_players:
                        if pid not in ai_players_list:
                            new_state["ready_for_next_hand"][pid] = True
                            logger.info(f"Auto-marked human player {pid} as ready for next hand")
                    
                    # Check if all players are ready (including AI and human)
                    ready_players = new_state.get("ready_for_next_hand", {})
                    all_ready = len(ready_players) >= len(all_players)
                    logger.info(f"Hand finished - Ready check: {len(ready_players)}/{len(all_players)} players ready. AI players: {ai_players_list}, All players: {all_players}, Ready: {list(ready_players.keys())}")
                    if all_ready:
                        logger.info(f"All players ready for next hand, will auto-start")
                        return {"success": True, "all_ready_for_next_hand": True}
            
            return {"success": True}
        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            logger.error(f"Error processing move: {e}", exc_info=True)
            return {"error": f"Failed to process move: {str(e)}"}

    async def process_single_ai_move(self, game_id: str) -> Dict[str, Any]:
        """Process a single AI move and return whether to continue."""
        self._check_ready()
        
        if game_id not in self.games:
            return {"continue": False}
        
        game = self.games[game_id]
        
        if game["status"] != "in_progress":
            return {"continue": False}
        
        game_state = game.get("game_state")
        if not game_state:
            return {"continue": False}
        
        game_type = game["game_type"]
        
        # Validate game state has required fields
        if "players" not in game_state or "current_turn_index" not in game_state:
            return {"continue": False}
        
        players = game_state.get("players", [])
        if not players:
            return {"continue": False}
        
        # Find current player
        current_turn_index = game_state.get("current_turn_index", 0)
        if current_turn_index < 0 or current_turn_index >= len(players):
            return {"continue": False}
        
        current_player_id = players[current_turn_index]
        
        # Check if current player is AI
        if not self.is_ai_player(game_id, current_player_id):
            return {"continue": False}
        
        # Make AI move
        try:
            if game_type == "blackjack":
                move_data = self._get_ai_blackjack_move(game_state, current_player_id)
            elif game_type == "dominoes":
                move_data = self._get_ai_dominoes_move(game_state, current_player_id)
            else:
                return {"continue": False}
            
            if not move_data:
                return {"continue": False}
            
            # Allow AI to make moves by passing allow_ai=True
            logger.info(f"Making AI move for {current_player_id}: {move_data}")
            result = await self.play_move(game_id, current_player_id, move_data, allow_ai=True)
            if result.get("error"):
                logger.warning(f"AI move failed for {current_player_id}: {result.get('error')}")
                return {"continue": False}
            
            # Check if turn advanced and next player is also AI
            updated_game_state = game.get("game_state")
            if updated_game_state:
                next_turn_index = updated_game_state.get("current_turn_index", 0)
                next_player_id = updated_game_state.get("players", [])[next_turn_index] if next_turn_index < len(updated_game_state.get("players", [])) else None
                if next_player_id:
                    next_is_ai = self.is_ai_player(game_id, next_player_id)
                    logger.info(f"After AI move, next player is {next_player_id} (AI: {next_is_ai})")
                    return {"continue": next_is_ai}
            
            return {"continue": True}
        except Exception as e:
            logger.error(f"Error processing AI move: {e}", exc_info=True)
            return {"continue": False}

    def _get_ai_blackjack_move(self, game_state: Dict[str, Any], player_id: str) -> Optional[Dict[str, Any]]:
        """Get AI move for blackjack."""
        hand_data = game_state.get("hands", {}).get(player_id, {})
        hand_value = hand_data.get("value", 0)
        status = hand_data.get("status", "playing")
        
        if status != "playing":
            return None
        
        # Simple strategy: hit if below 17, stand otherwise
        if hand_value < 17:
            return {"action": "hit"}
        else:
            return {"action": "stand"}

    def _get_ai_dominoes_move(self, game_state: Dict[str, Any], player_id: str) -> Optional[Dict[str, Any]]:
        """Get AI move for dominoes."""
        hand = game_state.get("hands", {}).get(player_id, [])
        board = game_state.get("board", [])
        boneyard = game_state.get("boneyard", [])
        
        if not hand:
            return None
        
        # If board is empty, play highest double or highest tile
        if not board:
            # Find highest double
            for tile in sorted(hand, key=lambda t: (t[0] == t[1], sum(t)), reverse=True):
                if tile[0] == tile[1]:  # Double
                    return {"action": "play", "tile": list(tile), "side": "right"}
            # Play highest tile
            highest = max(hand, key=lambda t: sum(t))
            return {"action": "play", "tile": list(highest), "side": "right"}
        
        # Find playable tiles
        left_end, right_end = get_open_ends(board)
        playable_tiles = []
        
        for tile in hand:
            if tile[0] == left_end or tile[1] == left_end:
                playable_tiles.append(("left", tile))
            if tile[0] == right_end or tile[1] == right_end:
                playable_tiles.append(("right", tile))
        
        if playable_tiles:
            # Play first playable tile
            side, tile = playable_tiles[0]
            return {"action": "play", "tile": list(tile), "side": side}
        
        # No playable tiles, draw if possible
        if boneyard:
            return {"action": "draw"}
        else:
            return {"action": "pass"}

    def ready_for_next_round(self, game_id: str, player_id: str) -> Dict[str, Any]:
        """Mark player as ready for next round."""
        self._check_ready()
        
        if game_id not in self.games:
            return {"error": "Game not found"}
        
        game = self.games[game_id]
        game_state = game.get("game_state", {})
        
        if game_state.get("status") != "round_finished":
            return {"error": "Game is not in round_finished state"}
        
        if "ready_for_next_round" not in game_state:
            game_state["ready_for_next_round"] = {}
        
        game_state["ready_for_next_round"][player_id] = True
        
        # Check if all players are ready
        all_players = game.get("players", [])
        ready_players = game_state.get("ready_for_next_round", {})
        
        all_ready = len(ready_players) >= len(all_players)
        
        return {"success": True, "all_ready": all_ready}

    def start_next_round(self, game_id: str) -> Dict[str, Any]:
        """Start the next round."""
        self._check_ready()
        
        if game_id not in self.games:
            return {"error": "Game not found"}
        
        game = self.games[game_id]
        game_state = game.get("game_state", {})
        
        if game_state.get("status") != "round_finished":
            return {"error": "Game is not in round_finished state"}
        
        # Create new round
        if game["game_type"] == "blackjack":
            new_state = create_blackjack_game(game["players"], game["game_mode"])
        else:
            return {"error": "start_next_round only supported for blackjack"}
        
        game["game_state"] = new_state
        game["status"] = "in_progress"
        
        return {"success": True}

    def ready_for_next_hand(self, game_id: str, player_id: str) -> Dict[str, Any]:
        """Mark player as ready for next hand."""
        self._check_ready()
        
        if game_id not in self.games:
            return {"error": "Game not found"}
        
        game = self.games[game_id]
        game_state = game.get("game_state", {})
        
        if game_state.get("status") != "hand_finished":
            return {"error": "Game is not in hand_finished state"}
        
        if "ready_for_next_hand" not in game_state:
            game_state["ready_for_next_hand"] = {}
        
        game_state["ready_for_next_hand"][player_id] = True
        
        # Check if all players are ready
        all_players = game.get("players", [])
        ready_players = game_state.get("ready_for_next_hand", {})
        
        all_ready = len(ready_players) >= len(all_players)
        
        return {"success": True, "all_ready": all_ready}

    def start_next_hand(self, game_id: str) -> Dict[str, Any]:
        """Start the next hand."""
        self._check_ready()
        
        if game_id not in self.games:
            return {"error": "Game not found"}
        
        game = self.games[game_id]
        game_state = game.get("game_state", {})
        
        if game_state.get("status") != "hand_finished":
            return {"error": "Game is not in hand_finished state"}
        
        # Create new hand
        if game["game_type"] == "dominoes":
            new_state = create_dominoes_game(game["players"], game["game_mode"])
        else:
            return {"error": "start_next_hand only supported for dominoes"}
        
        game["game_state"] = new_state
        game["status"] = "in_progress"
        
        return {"success": True}

    async def initialize(self):
        """
        Post-initialization hook: performs setup and verification.
        This is called automatically when the actor starts up.
        """
        import sys
        print(f"[{self.write_scope}-Actor] ⚡ INITIALIZE CALLED - Starting post-initialization setup...", flush=True, file=sys.stderr)
        logger.info(f"[{self.write_scope}-Actor] ⚡ INITIALIZE CALLED - Starting post-initialization setup...")
        
        try:
            # Verify database is ready
            if not self.db:
                raise RuntimeError("Database not initialized")
            
            # Verify templates are loaded (if needed)
            if not self.templates:
                logger.warning(f"[{self.write_scope}-Actor] Templates not loaded, but continuing...")
            
            # Verify we can access the database
            try:
                # Test database connection by checking collection access
                test_query = await self.db.games.find_one({}, {"_id": 1})
                logger.info(f"[{self.write_scope}-Actor] Database connection verified. Test query successful.")
            except Exception as test_e:
                logger.error(f"[{self.write_scope}-Actor] Database connection test failed: {test_e}", exc_info=True)
                raise
            
            # Count existing games in database (for monitoring)
            try:
                count = await self.db.games.count_documents({})
                logger.info(f"[{self.write_scope}-Actor] Found {count} existing game records in database.")
            except Exception as count_e:
                logger.warning(f"[{self.write_scope}-Actor] Could not count games: {count_e}")
            
            logger.info(f"[{self.write_scope}-Actor] ✅ Game Portal initialized and ready")
            print(f"[{self.write_scope}-Actor] ✅ Game Portal initialized and ready", flush=True, file=sys.stderr)
                
        except Exception as e:
            import traceback
            print(f"[{self.write_scope}-Actor] ❌ ERROR during initialization: {e}", flush=True, file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            logger.error(f"[{self.write_scope}-Actor] ❌ ERROR during initialization: {e}", exc_info=True)

