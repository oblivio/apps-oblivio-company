"""
Game Portal Experiment

FastAPI routes that handle HTTP API and WebSocket connections for multiplayer games.
"""

import logging
import json
import asyncio
import ray
from fastapi import APIRouter, Request, HTTPException, WebSocket, WebSocketDisconnect, status, Body
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from typing import Any, Dict, Optional, Tuple
from pathlib import Path
from datetime import datetime
from .actor import ExperimentActor

logger = logging.getLogger(__name__)
bp = APIRouter()

# Backend API router with CORS support for *.oblivio-company.com
# Note: prefix is added when mounting, so don't include it here
backend_bp = APIRouter(tags=["Game Portal Backend API"])

# ============================================================================
# Validation Helpers - Make it super easy to use correctly
# ============================================================================

VALID_GAME_TYPES = ["dominoes"]
VALID_GAME_MODES = {
    "dominoes": ["classic", "boricua"]
}
DEFAULT_GAME_MODES = {
    "dominoes": "classic"
}
MIN_AI_COUNT = 0
MAX_AI_COUNT = 3
MAX_PLAYERS = 4

def validate_game_type(game_type: str) -> Tuple[bool, Optional[str]]:
    """Validate game type and return (is_valid, error_message)."""
    if not game_type:
        return False, "game_type is required. Valid options: 'dominoes'"
    game_type_lower = game_type.lower().strip()
    if game_type_lower not in VALID_GAME_TYPES:
        return False, f"Invalid game_type: '{game_type}'. Must be one of: {', '.join(VALID_GAME_TYPES)}"
    return True, None

def validate_game_mode(game_type: str, game_mode: Optional[str]) -> Tuple[bool, Optional[str]]:
    """Validate game mode for the given game type. Returns (is_valid, error_message)."""
    if game_mode is None:
        return True, None  # None is valid, will use default
    
    game_type_lower = game_type.lower().strip()
    game_mode_lower = game_mode.lower().strip()
    
    if game_type_lower not in VALID_GAME_MODES:
        return False, f"Invalid game_type: '{game_type}'. Must be one of: {', '.join(VALID_GAME_TYPES)}"
    
    valid_modes = VALID_GAME_MODES[game_type_lower]
    if game_mode_lower not in valid_modes:
        return False, f"Invalid game_mode: '{game_mode}' for game_type '{game_type}'. Valid options: {', '.join(valid_modes)}"
    
    return True, None

def validate_ai_count(ai_count: Optional[int]) -> Tuple[bool, Optional[str], int]:
    """Validate and clamp AI count. Returns (is_valid, error_message, clamped_value)."""
    if ai_count is None:
        return True, None, 0
    
    if not isinstance(ai_count, int):
        return False, f"ai_count must be an integer, got: {type(ai_count).__name__}", 0
    
    if ai_count < MIN_AI_COUNT:
        return False, f"ai_count must be >= {MIN_AI_COUNT}, got: {ai_count}", MIN_AI_COUNT
    
    if ai_count > MAX_AI_COUNT:
        return False, f"ai_count must be <= {MAX_AI_COUNT}, got: {ai_count}", MAX_AI_COUNT
    
    return True, None, ai_count

def get_helpful_error_message(game_type: str, field: str, value: Any) -> str:
    """Generate helpful error messages with examples."""
    base_url = "https://apps.oblivio-company.com/experiments/game_portal/backend"
    
    if field == "game_type":
        examples = "\n".join([
            f"  - {base_url}/lobby/your-circle-id/dominoes"
        ])
        return f"Invalid {field}: '{value}'. Valid options: {', '.join(VALID_GAME_TYPES)}\n\nExample URLs:\n{examples}"
    
    elif field == "game_mode":
        valid_modes = VALID_GAME_MODES.get(game_type.lower(), [])
        default = DEFAULT_GAME_MODES.get(game_type.lower(), "unknown")
        return f"Invalid {field}: '{value}' for game_type '{game_type}'. Valid options: {', '.join(valid_modes)} (default: {default})"
    
    elif field == "ai_count":
        return f"Invalid {field}: {value}. Must be between {MIN_AI_COUNT} and {MAX_AI_COUNT} (inclusive). Example: ai_count=2 for 2 AI players"
    
    return f"Invalid {field}: {value}"

# Path setup
EXPERIMENT_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(EXPERIMENT_DIR / "templates"))

# Connection manager for WebSocket connections
# { "game_id": { "player_id": WebSocket } }
active_connections: Dict[str, Dict[str, WebSocket]] = {}


def get_actor_handle(request: Request) -> "ray.actor.ActorHandle":
    """FastAPI Dependency to get the Game Portal actor handle."""
    # Check if Ray is available in app state
    ray_is_available = getattr(request.app.state, "ray_is_available", False)
    
    # Runtime check: verify Ray is actually initialized (in case it was initialized after startup)
    if not ray_is_available:
        # Double-check if Ray is actually initialized (might have been initialized after app startup)
        try:
            if ray.is_initialized():
                logger.info("Ray is initialized but app.state.ray_is_available was False. Updating state...")
                request.app.state.ray_is_available = True
                ray_is_available = True
        except Exception as e:
            logger.debug(f"Could not check Ray initialization status: {e}")
    
    if not ray_is_available:
        logger.error("Ray is globally unavailable, blocking actor handle request.")
        # Try to get more diagnostic info
        try:
            is_init = ray.is_initialized()
            logger.error(f"Ray initialization check: is_initialized()={is_init}")
        except Exception as diag_e:
            logger.error(f"Could not check Ray status: {diag_e}")
        raise HTTPException(
            status_code=503,
            detail="Ray service is unavailable. Check Ray cluster status and logs for initialization errors."
        )
    
    slug_id = getattr(request.state, "slug_id", None)
    if not slug_id:
        logger.error("Server error: slug_id not found in request state.")
        raise HTTPException(500, "Server error: slug_id not found in request state.")
    
    actor_name = f"{slug_id}-actor"
    
    try:
        handle = ray.get_actor(actor_name, namespace="modular_labs")
        return handle
    except ValueError:
        logger.error(f"CRITICAL: Actor '{actor_name}' found no process running.")
        raise HTTPException(503, f"Experiment service '{actor_name}' is not running.")
    except Exception as e:
        logger.error(f"Failed to get actor handle '{actor_name}': {e}", exc_info=True)
        raise HTTPException(500, "Error connecting to experiment service.")


async def get_actor_handle_ws(websocket: WebSocket) -> "ray.actor.ActorHandle":
    """Get actor handle for WebSocket connections."""
    path_parts = websocket.url.path.strip("/").split("/")
    slug_id = "game_portal"
    if len(path_parts) >= 2 and path_parts[0] == "experiments":
        slug_id = path_parts[1]
    
    # Check if Ray is available in app state
    ray_is_available = getattr(websocket.app.state, "ray_is_available", False)
    
    # Runtime check: verify Ray is actually initialized (in case it was initialized after startup)
    if not ray_is_available:
        # Double-check if Ray is actually initialized (might have been initialized after app startup)
        try:
            if ray.is_initialized():
                logger.info("Ray is initialized but app.state.ray_is_available was False. Updating state...")
                websocket.app.state.ray_is_available = True
                ray_is_available = True
        except Exception as e:
            logger.debug(f"Could not check Ray initialization status: {e}")
    
    if not ray_is_available:
        logger.error("Ray is globally unavailable in WebSocket")
        # Try to get more diagnostic info
        try:
            is_init = ray.is_initialized()
            logger.error(f"Ray initialization check: is_initialized()={is_init}")
        except Exception as diag_e:
            logger.error(f"Could not check Ray status: {diag_e}")
        await websocket.close(code=503, reason="Service unavailable")
        return None
    
    actor_name = f"{slug_id}-actor"
    try:
        return ray.get_actor(actor_name, namespace="modular_labs")
    except ValueError:
        logger.error(f"CRITICAL: Actor '{actor_name}' not found in WebSocket")
        await websocket.close(code=503, reason="Service unavailable")
        return None
    except Exception as e:
        logger.error(f"Failed to get actor in WebSocket: {e}", exc_info=True)
        await websocket.close(code=503, reason="Service unavailable")
        return None


def get_backend_actor_handle(request: Request) -> "ray.actor.ActorHandle":
    """Get actor handle for backend API (separate FastAPI app, doesn't have slug_id in state)."""
    # Backend API is a separate FastAPI app, so we need to get the actor directly
    # The slug_id is always "game_portal" for this experiment
    slug_id = "game_portal"
    actor_name = f"{slug_id}-actor"
    
    # Check if Ray is available in app state
    ray_is_available = getattr(request.app.state, "ray_is_available", False)
    
    # Runtime check: verify Ray is actually initialized (in case it was initialized after startup)
    if not ray_is_available:
        # Double-check if Ray is actually initialized (might have been initialized after app startup)
        try:
            if ray.is_initialized():
                logger.info("Ray is initialized but app.state.ray_is_available was False. Updating state...")
                request.app.state.ray_is_available = True
                ray_is_available = True
        except Exception as e:
            logger.debug(f"Could not check Ray initialization status: {e}")
    
    if not ray_is_available:
        logger.error("Ray is globally unavailable in backend API")
        # Try to get more diagnostic info
        try:
            is_init = ray.is_initialized()
            logger.error(f"Ray initialization check: is_initialized()={is_init}")
        except Exception as diag_e:
            logger.error(f"Could not check Ray status: {diag_e}")
        raise HTTPException(
            status_code=503,
            detail="Ray service is unavailable. Check Ray cluster status and logs for initialization errors."
        )
    
    try:
        return ray.get_actor(actor_name, namespace="modular_labs")
    except ValueError:
        logger.error(f"CRITICAL: Actor '{actor_name}' not found in backend API")
        raise HTTPException(503, f"Experiment service '{actor_name}' is not running.")
    except Exception as e:
        logger.error(f"Failed to get actor handle in backend API: {e}", exc_info=True)
        raise HTTPException(500, "Error connecting to experiment service.")


async def broadcast_to_game(game_id: str, message: Dict[str, Any]):
    """Broadcast message to all players in a game."""
    if game_id in active_connections:
        for player_id, connection in active_connections[game_id].items():
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"Error sending to {player_id}: {e}")


async def send_to_player(game_id: str, player_id: str, message: Dict[str, Any]):
    """Send message to a specific player."""
    if game_id in active_connections and player_id in active_connections[game_id]:
        try:
            await active_connections[game_id][player_id].send_json(message)
        except Exception as e:
            logger.error(f"Error sending to {player_id}: {e}")


async def process_ai_moves_with_broadcast(actor, game_id: str, player_ids: list):
    """Processes AI moves and broadcasts state updates after each move."""
    max_iterations = 20
    iteration = 0
    
    while iteration < max_iterations:
        iteration += 1
        
        # Check game state before processing
        current_game = await actor.get_game.remote(game_id)
        if not current_game:
            logger.warning(f"Game {game_id} not found during AI processing")
            break
        
        if current_game.get('status') != 'in_progress':
            logger.debug(f"Game {game_id} status is {current_game.get('status')}, stopping AI processing")
            break
        
        game_state = current_game.get('game_state')
        if not game_state:
            logger.warning(f"Game {game_id} has no game_state during AI processing")
            break
        
        # Check if current player is AI before processing
        current_turn_index = game_state.get('current_turn_index', 0)
        players = game_state.get('players', [])
        if current_turn_index >= len(players):
            logger.warning(f"Invalid current_turn_index {current_turn_index} for game {game_id}")
            break
        
        current_player_id = players[current_turn_index]
        is_ai = await actor.is_ai_player.remote(game_id, current_player_id)
        
        if not is_ai:
            # Current player is not AI, stop processing
            logger.debug(f"Current player {current_player_id} is not AI, stopping AI processing")
            break
        
        # Process a single AI move
        logger.info(f"Processing AI move for {current_player_id} in game {game_id}")
        result = await actor.process_single_ai_move.remote(game_id)
        
        if result.get('error'):
            logger.error(f"AI move processing error: {result.get('error')}")
            # Still broadcast state even if AI move failed
            updated_game = await actor.get_game.remote(game_id)
            if updated_game:
                players_with_ai_status = []
                for p in updated_game.get('players', []):
                    player_dict = p.copy() if isinstance(p, dict) else {"player_id": p}
                    pid = player_dict.get('player_id', player_dict)
                    player_dict['isAI'] = await actor.is_ai_player.remote(game_id, pid)
                    players_with_ai_status.append(player_dict)
                
                for pid in player_ids:
                    sanitized = await actor.sanitize_game_state_for_player.remote(
                        updated_game.get('game_type'), updated_game.get('game_state'), pid
                    )
                    await send_to_player(game_id, pid, {
                        "type": "state_update",
                        "game_state": sanitized,
                        "players": players_with_ai_status,
                        "game_status": updated_game.get('status', 'in_progress')
                    })
            break
        
        logger.info(f"AI move processed successfully for {current_player_id}, continue={result.get('continue', False)}")
        
        # Always broadcast updated state after AI move
        updated_game = await actor.get_game.remote(game_id)
        if not updated_game:
            logger.warning(f"Game {game_id} not found after AI move")
            break
        
        # Check if game is still in progress
        if updated_game.get('status') != 'in_progress':
            # Game finished, broadcast final state
            players_with_ai_status = []
            for p in updated_game.get('players', []):
                player_dict = p.copy() if isinstance(p, dict) else {"player_id": p}
                pid = player_dict.get('player_id', player_dict)
                player_dict['isAI'] = await actor.is_ai_player.remote(game_id, pid)
                players_with_ai_status.append(player_dict)
            
            for pid in player_ids:
                sanitized = await actor.sanitize_game_state_for_player.remote(
                    updated_game.get('game_type'), updated_game.get('game_state'), pid
                )
                await send_to_player(game_id, pid, {
                    "type": "state_update",
                    "game_state": sanitized,
                    "players": players_with_ai_status,
                    "game_status": updated_game.get('status')
                })
            break
        
        players_with_ai_status = []
        for p in updated_game.get('players', []):
            player_dict = p.copy() if isinstance(p, dict) else {"player_id": p}
            pid = player_dict.get('player_id', player_dict)
            player_dict['isAI'] = await actor.is_ai_player.remote(game_id, pid)
            players_with_ai_status.append(player_dict)
        
        for pid in player_ids:
            sanitized = await actor.sanitize_game_state_for_player.remote(
                updated_game.get('game_type'), updated_game.get('game_state'), pid
            )
            await send_to_player(game_id, pid, {
                "type": "state_update",
                "game_state": sanitized,
                "players": players_with_ai_status,
                "game_status": updated_game.get('status', 'in_progress')
            })
        
        # Check if we should continue
        # If continue=True, loop will check if next player is AI and process if so
        if not result.get('continue', False):
            logger.debug(f"AI processing returned continue=False, stopping")
            break
        
        # Continue to next iteration to check if next player is also AI
        # The loop will check if current player is AI at the start of next iteration
        logger.debug(f"AI processing continuing to next iteration")
        
        # No delay - AI moves are instant for better UX


# --- HTTP API Models ---

class CreateGameRequest(BaseModel):
    player_id: str = Field(..., min_length=1, max_length=200)
    game_type: str = Field(default="dominoes", description="e.g., 'dominoes'. Defaults to 'dominoes'")
    game_mode: Optional[str] = Field(default=None, description="For dominoes: 'classic' or 'boricua'. Defaults to 'classic'")
    ai_count: int = Field(default=0, ge=0, le=3, description="Number of AI players to add (0-3, max 3, 4 players total max)")
    
    def __init__(self, **data):
        super().__init__(**data)
        # Set default game_mode based on game_type if not provided
        if self.game_mode is None:
            if self.game_type == "dominoes":
                self.game_mode = "classic"


class JoinGameRequest(BaseModel):
    player_id: str = Field(..., min_length=1, max_length=200)
    replace_ai: Optional[str] = Field(None, description="AI player ID to replace (for mid-game joining)")


# --- HTTP API Endpoints ---

@bp.get("/", response_class=HTMLResponse, name="game_portal_index")
async def index(request: Request):
    """The main UI route for the Game Portal experiment."""
    return templates.TemplateResponse(
        "index.html",
        {"request": request}
    )


@bp.post("/api/game/create")
async def create_game(request: Request, create_req: CreateGameRequest):
    """Creates a new game lobby."""
    actor = get_actor_handle(request)
    result = await actor.create_game.remote(
        player_id=create_req.player_id,
        game_type=create_req.game_type,
        game_mode=create_req.game_mode,
        ai_count=create_req.ai_count
    )
    return result


@bp.post("/api/game/{game_id}/join")
async def join_game(request: Request, game_id: str, join_req: JoinGameRequest):
    """Allows a new player to join a waiting game or mid-game (replacing AI)."""
    actor = get_actor_handle(request)
    result = await actor.join_game.remote(
        game_id, 
        join_req.player_id,
        join_req.replace_ai
    )
    
    if result.get("error"):
        return result
    
    # Notify lobby via WebSocket
    await broadcast_to_game(game_id, {
        "type": "player_joined",
        "player_id": join_req.player_id,
        "replaced_ai": result.get("replaced_ai")
    })
    
    return result


@bp.get("/api/game/{game_id}/ai-slots")
async def get_ai_slots(request: Request, game_id: str):
    """Get available AI slots that can be replaced."""
    actor = get_actor_handle(request)
    ai_slots = await actor.get_available_ai_slots.remote(game_id)
    return {"ai_slots": ai_slots}


@bp.get("/api/game/{game_id}")
async def get_game(request: Request, game_id: str):
    """Get game information."""
    actor = get_actor_handle(request)
    game = await actor.get_game.remote(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    
    # Sanitize game state for public access (hide sensitive info)
    game_type = game.get('game_type')
    game_state = game.get('game_state')
    
    # Return public game info without sensitive game state
    public_game = {
        "game_id": game.get('game_id'),
        "game_type": game_type,
        "game_mode": game.get('game_mode'),
        "status": game.get('status'),
        "players": game.get('players', []),
        "ai_players": game.get('ai_players', []),
        "host_id": game.get('host_id'),
        "min_players": game.get('min_players'),
        "max_players": game.get('max_players'),
        # Don't expose game_state in public endpoint
    }
    return public_game


@bp.get("/api/current-user")
async def get_current_user_id(request: Request):
    """
    Get current user ID - returns authenticated user if available, otherwise indicates to use browser fingerprint.
    Game portal is public, so player_id should be a browser fingerprint.
    """
    slug_id = getattr(request.state, "slug_id", "game_portal")
    
    # Try to get user from sub-auth session (optional - for authenticated users)
    try:
        from mdb_runtime.auth import get_experiment_sub_user
        from mdb_runtime.database import get_experiment_db
        from core_deps import get_experiment_config
        
        config = await get_experiment_config(request, slug_id, {"sub_auth": 1})
        if config:
            sub_auth = config.get("sub_auth", {})
            if sub_auth.get("enabled", False):
                db = await get_experiment_db(request)
                experiment_user = await get_experiment_sub_user(request, slug_id, db, config, allow_demo_fallback=True)
                if experiment_user:
                    user_id = str(experiment_user.get("_id"))
                    return {
                        "user_id": user_id,
                        "experiment_user_id": user_id,
                        "email": experiment_user.get("email"),
                        "username": experiment_user.get("username"),
                        "is_authenticated": True
                    }
    except Exception as e:
        logger.debug(f"Could not get user from sub-auth: {e}")
    
    # Try to get user from platform auth (optional - for authenticated users)
    try:
        from core_deps import get_current_user
        
        # Get token from cookie
        token = request.cookies.get("token")
        if token:
            platform_user = await get_current_user(token=token)
            if platform_user:
                user_id = str(platform_user.get("user_id", platform_user.get("_id", "")))
                if user_id:
                    return {
                        "user_id": user_id,
                        "email": platform_user.get("email"),
                        "username": platform_user.get("username"),
                        "is_authenticated": True
                    }
    except Exception as e:
        logger.debug(f"Could not get user from platform auth: {e}")
    
    # No authenticated user - game portal is public, use browser fingerprint
    return {
        "user_id": None,
        "is_authenticated": False,
        "message": "Use browser fingerprint for player_id"
    }


async def _get_or_generate_player_id(request: Request) -> str:
    """Get player ID from authenticated user or generate a temporary one."""
    slug_id = getattr(request.state, "slug_id", "game_portal")
    
    # Try to get user from sub-auth session
    try:
        from mdb_runtime.auth import get_experiment_sub_user
        from mdb_runtime.database import get_experiment_db
        from core_deps import get_experiment_config
        
        config = await get_experiment_config(request, slug_id, {"sub_auth": 1})
        if config:
            sub_auth = config.get("sub_auth", {})
            if sub_auth.get("enabled", False):
                db = await get_experiment_db(request)
                experiment_user = await get_experiment_sub_user(request, slug_id, db, config, allow_demo_fallback=True)
                if experiment_user:
                    return str(experiment_user.get("_id"))
    except Exception as e:
        logger.debug(f"Could not get user from sub-auth: {e}")
    
    # Try to get user from platform auth
    try:
        from core_deps import get_current_user
        
        token = request.cookies.get("token")
        if token:
            platform_user = await get_current_user(token=token)
            if platform_user:
                user_id = str(platform_user.get("user_id", platform_user.get("_id", "")))
                if user_id:
                    return user_id
    except Exception as e:
        logger.debug(f"Could not get user from platform auth: {e}")
    
    # Generate a temporary player ID based on session/IP
    # This will be replaced by browser fingerprint on the frontend
    import hashlib
    import secrets
    session_id = request.cookies.get("session_id") or secrets.token_hex(16)
    ip_address = request.client.host if request.client else "unknown"
    fingerprint_data = f"{session_id}|{ip_address}|{request.headers.get('user-agent', 'unknown')}"
    player_id = "temp_" + hashlib.sha256(fingerprint_data.encode()).hexdigest()[:16]
    return player_id


@bp.get("/new", response_class=HTMLResponse, name="auto_create_game_default")
async def auto_create_game_default(request: Request):
    """Default /new endpoint - redirects to /new/dominoes"""
    from fastapi.responses import RedirectResponse
    base_path = request.url.path.replace("/new", "")
    if not base_path:
        base_path = "/experiments/game_portal"
    return RedirectResponse(url=f"{base_path}/new/dominoes", status_code=302)


@bp.get("/new/{game_type}", response_class=HTMLResponse, name="auto_create_game")
async def auto_create_game(
    request: Request, 
    game_type: str,
    game_mode: Optional[str] = None,
    ai_count: int = 0
):
    """
    Automatically create a new game of the specified type and show a shareable link page.
    Perfect for social media sharing!
    
    Query parameters:
    - game_mode: Optional game mode (e.g., 'classic', 'boricua' for dominoes)
    - ai_count: Number of AI players to add (0-3, defaults to 2 for better social experience)
    """
    # Validate game_type with helpful error messages
    is_valid, error_msg = validate_game_type(game_type)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error_msg)
    game_type = game_type.lower().strip()  # Normalize
    
    # Get actor handle
    actor = get_actor_handle(request)
    
    # Set default game_mode if not provided
    if game_mode is None:
        if game_type == "dominoes":
            game_mode = "classic"
    
    # Clamp ai_count to valid range, default to 2 for better social experience
    if ai_count == 0:
        ai_count = 2  # Default to 2 AI players for instant play
    ai_count = max(0, min(3, ai_count))
    
    # Create a placeholder player_id that will be replaced by the frontend
    import secrets
    placeholder_player_id = f"PLACEHOLDER_{secrets.token_hex(8)}"
    
    # Create game with placeholder player
    try:
        result = await actor.create_game.remote(
            player_id=placeholder_player_id,
            game_type=game_type,
            game_mode=game_mode,
            ai_count=ai_count
        )
        
        if result.get("error"):
            raise HTTPException(status_code=400, detail=result["error"])
        
        game_id = result.get("game_id")
        if not game_id:
            raise HTTPException(status_code=500, detail="Failed to create game")
        
        # Build the shareable URL
        base_url = str(request.base_url).rstrip('/')
        base_path = request.url.path.rsplit("/new/", 1)[0]
        if not base_path:
            base_path = "/experiments/game_portal"
        shareable_url = f"{base_url}{base_path}?game={game_id}"
        
        # Get game info for display
        game_mode_display = game_mode.replace('_', ' ').title() if game_mode else "Classic"
        game_type_display = game_type.title()
        
        # Render share page with social media meta tags
        return templates.TemplateResponse(
            "share_game.html",
            {
                "request": request,
                "game_id": game_id,
                "game_type": game_type,
                "game_type_display": game_type_display,
                "game_mode": game_mode,
                "game_mode_display": game_mode_display,
                "shareable_url": shareable_url,
                "ai_count": ai_count,
                "base_path": base_path
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in auto_create_game: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to create game: {str(e)}")


# --- Backend API Endpoints (CORS-enabled for *.oblivio-company.com) ---
# Support both /game/... and /api/game/... paths for compatibility

async def _backend_create_game_impl(request: Request, create_req: CreateGameRequest):
    """Implementation for creating a game."""
    try:
        actor = get_backend_actor_handle(request)
    except HTTPException as e:
        # Re-raise HTTPException (includes 503 for Ray unavailable)
        raise e
    except Exception as e:
        logger.error(f"Unexpected error getting actor handle: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
    
    result = await actor.create_game.remote(
        player_id=create_req.player_id,
        game_type=create_req.game_type,
        game_mode=create_req.game_mode,
        ai_count=create_req.ai_count
    )
    return result

async def _backend_join_game_impl(request: Request, game_id: str, join_req: JoinGameRequest):
    """Implementation for joining a game."""
    try:
        actor = get_backend_actor_handle(request)
    except HTTPException as e:
        # Re-raise HTTPException (includes 503 for Ray unavailable)
        raise e
    except Exception as e:
        logger.error(f"Unexpected error getting actor handle: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
    
    result = await actor.join_game.remote(
        game_id, 
        join_req.player_id,
        join_req.replace_ai
    )
    
    if result.get("error"):
        return result
    
    # Notify lobby via WebSocket
    await broadcast_to_game(game_id, {
        "type": "player_joined",
        "player_id": join_req.player_id,
        "role": result.get("role", "player"),
        "replaced_ai": result.get("replaced_ai"),
        "replaced_placeholder": result.get("replaced_placeholder")
    })
    
    return result

async def _backend_get_game_impl(request: Request, game_id: str):
    """Implementation for getting game info."""
    try:
        actor = get_backend_actor_handle(request)
    except HTTPException as e:
        # Re-raise HTTPException (includes 503 for Ray unavailable)
        raise e
    except Exception as e:
        logger.error(f"Unexpected error getting actor handle: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
    
    game = await actor.get_game.remote(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    
    # Return public game info
    public_game = {
        "game_id": game.get('game_id'),
        "game_type": game.get('game_type'),
        "game_mode": game.get('game_mode'),
        "status": game.get('status'),
        "players": game.get('players', []),
        "ai_players": game.get('ai_players', []),
        "host_id": game.get('host_id'),
        "min_players": game.get('min_players'),
        "max_players": game.get('max_players'),
    }
    return public_game

async def _backend_get_ai_slots_impl(request: Request, game_id: str):
    """Implementation for getting AI slots."""
    try:
        actor = get_backend_actor_handle(request)
    except HTTPException as e:
        # Re-raise HTTPException (includes 503 for Ray unavailable)
        raise e
    except Exception as e:
        logger.error(f"Unexpected error getting actor handle: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
    
    ai_slots = await actor.get_available_ai_slots.remote(game_id)
    return {"ai_slots": ai_slots}

async def _backend_start_game_impl(request: Request, game_id: str, player_id: str = Body(..., embed=True)):
    """Implementation for starting a game."""
    try:
        actor = get_backend_actor_handle(request)
    except HTTPException as e:
        # Re-raise HTTPException (includes 503 for Ray unavailable)
        raise e
    except Exception as e:
        logger.error(f"Unexpected error getting actor handle: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
    
    result = await actor.start_game.remote(game_id, player_id)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result

async def _backend_get_game_state_impl(request: Request, game_id: str, player_id: Optional[str] = None):
    """Backend API: Get game state (sanitized for player if player_id provided). Accessible from *.oblivio-company.com"""
    try:
        actor = get_backend_actor_handle(request)
    except HTTPException as e:
        # Re-raise HTTPException (includes 503 for Ray unavailable)
        raise e
    except Exception as e:
        logger.error(f"Unexpected error getting actor handle: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
    
    game = await actor.get_game.remote(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    
    game_state = game.get('game_state')
    game_type = game.get('game_type')
    
    if player_id and game_state:
        # Sanitize game state for specific player
        sanitized = await actor.sanitize_game_state_for_player.remote(game_type, game_state, player_id)
        return {
            "game_id": game_id,
            "game_type": game_type,
            "game_mode": game.get('game_mode'),
            "status": game.get('status'),
            "game_state": sanitized,
            "players": game.get('players', []),
            "ai_players": game.get('ai_players', []),
        }
    
    # Return limited info if no player_id or no game_state
    return {
        "game_id": game_id,
        "game_type": game_type,
        "game_mode": game.get('game_mode'),
        "status": game.get('status'),
        "players": game.get('players', []),
        "ai_players": game.get('ai_players', []),
    }

async def _backend_poll_game_updates_impl(request: Request, game_id: str, last_update: Optional[str] = None):
    """
    Backend API: Poll for game updates (players joined, game started, etc.).
    Returns public game information suitable for external sites to display.
    Accessible from *.oblivio-company.com
    """
    logger.debug(f"Poll endpoint called for game_id: {game_id}, last_update: {last_update}")
    try:
        actor = get_backend_actor_handle(request)
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Unexpected error getting actor handle: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
    
    game = await actor.get_game.remote(game_id)
    if not game:
        logger.warning(f"Game not found for poll: game_id={game_id}")
        raise HTTPException(status_code=404, detail=f"Game not found: {game_id}")
    
    # Build player list with AI status - return actual game state accurately
    players_list = []
    for p in game.get('players', []):
        # Handle both string and dict player formats
        if isinstance(p, str):
            pid = p
        elif isinstance(p, dict):
            pid = p.get('player_id', p.get('playerId', p.get('id')))
            if not pid:
                logger.warning(f"Invalid player dict format (no player_id): {p}")
                continue
        else:
            logger.warning(f"Invalid player format (not string or dict): {p}")
            continue
        
        is_ai = await actor.is_ai_player.remote(game_id, pid)
        players_list.append({
            "player_id": pid,
            "is_ai": is_ai
        })
    
    # Determine if there are updates since last poll
    # For simplicity, we'll always return current state
    # External sites can compare timestamps or player counts to detect changes
    game_status = game.get('status', 'waiting')
    
    # Build response with all relevant info for external display
    # Magical experience: lobbies persist even when empty (0 players)!
    player_count = len(game.get('players', []))
    max_players = game.get('max_players', 4)
    
    response = {
        "game_id": game_id,
        "game_type": game.get('game_type'),
        "game_mode": game.get('game_mode'),
        "status": game_status,
        "host_id": game.get('host_id'),  # Will be None if empty - next player becomes host!
        "players": players_list,
        "player_count": player_count,  # Can be 0 - magical empty lobby!
        "min_players": game.get('min_players'),
        "max_players": max_players,
        "can_start": game_status == 'waiting' and player_count >= game.get('min_players', 2),
        "is_started": game_status in ['in_progress', 'round_finished', 'hand_finished'],
        "is_finished": game_status == 'finished',
        # Include basic game state info if game is in progress (without sensitive data)
        "game_state_summary": None
    }
    
    # Add game state summary if game is in progress (public info only)
    game_state = game.get('game_state')
    if game_state and game_status in ['in_progress', 'round_finished', 'hand_finished']:
        game_type = game.get('game_type')
        if game_type == 'dominoes':
            # Public dominoes info
            from .dominoes_logic import get_open_ends
            board = game_state.get('board', [])
            response["game_state_summary"] = {
                "board_length": len(board),
                "current_turn": game_state.get('players', [])[game_state.get('current_turn_index', 0)] if game_state.get('current_turn_index') is not None else None,
                "open_ends": get_open_ends(board) if board else None,
                "scores": game_state.get('scores', {})
            }
    
    return response

async def _backend_auto_create_game_impl(
    request: Request,
    game_type: str,
    game_mode: Optional[str] = None,
    ai_count: int = 0,
    player_id: Optional[str] = None
):
    """Backend API: Automatically create a new game and auto-join the player. Accessible from *.oblivio-company.com"""
    try:
        actor = get_backend_actor_handle(request)
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Unexpected error getting actor handle: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
    
    # Validate game_type with helpful error messages
    is_valid, error_msg = validate_game_type(game_type)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error_msg)
    game_type = game_type.lower().strip()  # Normalize
    
    # Create a placeholder player_id that will be replaced by the frontend
    # Use a special prefix so the frontend knows to replace it
    import secrets
    placeholder_player_id = f"PLACEHOLDER_{secrets.token_hex(8)}"
    
    # Set default game_mode if not provided
    if game_mode is None:
        if game_type == "dominoes":
            game_mode = "classic"
    
    # Clamp ai_count to valid range
    ai_count = max(0, min(3, ai_count))
    
    # Create game with placeholder player
    result = await actor.create_game.remote(
        player_id=placeholder_player_id,
        game_type=game_type,
        game_mode=game_mode,
        ai_count=ai_count
    )
    
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    
    return result

async def _backend_lobby_create_or_join_impl(
    request: Request,
    circle_id: str,
    game_type: str,
    player_id: str,
    game_mode: Optional[str] = None,
    ai_count: Optional[int] = None
):
    """
    Third-Party Integration API: Get or create a persistent lobby for a context and game type.
    
    This is a unified, flexible implementation for any third-party system to integrate games.
    Works with any context identifier - could be a circle ID, group ID, room ID, organization ID, etc.
    
    Lobbies always exist - can have 0-4 players. Missing players are auto-filled with AI when starting.
    
    If player_id is provided, the player will join the lobby and a redirect_url will be included in the response.
    
    Accessible from *.oblivio-company.com
    """
    try:
        actor = get_backend_actor_handle(request)
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Unexpected error getting actor handle: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
    
    # Validate game_type with helpful error messages
    is_valid, error_msg = validate_game_type(game_type)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error_msg)
    game_type = game_type.lower().strip()  # Normalize
    
    # Validate game_mode if provided
    if game_mode is not None:
        is_valid, error_msg = validate_game_mode(game_type, game_mode)
        if not is_valid:
            raise HTTPException(status_code=400, detail=error_msg)
        game_mode = game_mode.lower().strip()  # Normalize
    
    # Validate and clamp ai_count if provided
    if ai_count is not None:
        is_valid, error_msg, clamped_value = validate_ai_count(ai_count)
        if not is_valid:
            raise HTTPException(status_code=400, detail=error_msg)
        ai_count = clamped_value
    
    try:
        # Get or create persistent lobby (always exists, can have 0-4 players)
        lobby = await actor.get_or_create_lobby.remote(circle_id, game_type)
        
        # Check if the lobby response contains an error
        if isinstance(lobby, dict) and "error" in lobby:
            error_msg = lobby.get("error", "Unknown error")
            logger.error(f"Failed to get or create lobby for {circle_id}/{game_type}: {error_msg}")
            raise HTTPException(status_code=500, detail=f"Failed to get or create lobby: {error_msg}")
        
        game_id = lobby.get("game_id")
        if not game_id:
            logger.error(f"Lobby response missing game_id for {circle_id}/{game_type}. Response: {lobby}")
            raise HTTPException(status_code=500, detail="Failed to get or create lobby: lobby response missing game_id")
        
        # If player_id is provided, join the lobby (if not already in it) with optional settings
        action = "retrieved"  # Default action
        if player_id:
            # Check if player is already in the lobby
            current_players = lobby.get("players", [])
            if player_id not in current_players:
                # Join the lobby with optional settings
                join_result = await actor.join_game.remote(
                    game_id,
                    player_id,
                    replace_ai=None,
                    game_mode=game_mode,
                    ai_count=ai_count
                )
                
                if join_result.get("error"):
                    # If join failed (e.g., game is full), still return lobby info
                    logger.warning(f"Failed to join lobby {game_id}: {join_result.get('error')}")
                else:
                    # Successfully joined lobby
                    action = "joined"
                    # Notify lobby via WebSocket
                    await broadcast_to_game(game_id, {
                        "type": "player_joined",
                        "player_id": player_id,
                        "role": join_result.get("role", "player"),
                        "replaced_ai": join_result.get("replaced_ai")
                    })
        
        # Get updated lobby info
        updated_lobby = await actor.get_game.remote(game_id)
        if not updated_lobby:
            raise HTTPException(status_code=500, detail="Failed to retrieve lobby")
        
        # Filter out placeholder players from response
        players = [p for p in updated_lobby.get("players", []) if not (isinstance(p, str) and p.startswith("PLACEHOLDER_"))]
        
        # Build response
        response = {
            "game_id": game_id,
            "player_id": player_id if player_id else None,
            "game_type": game_type,
            "game_mode": updated_lobby.get("game_mode"),
            "action": action,
            "ai_count": len(updated_lobby.get("ai_players", [])),
            "players": players,
            "ai_players": updated_lobby.get("ai_players", []),
            "player_count": len(players),
            "status": updated_lobby.get("status", "waiting"),
            "min_players": updated_lobby.get("min_players"),
            "max_players": updated_lobby.get("max_players")
        }
        
        # Include redirect_url if player_id was provided (for seamless redirect)
        if player_id:
            base_url = str(request.base_url).rstrip('/')
            response["redirect_url"] = f"{base_url}/experiments/game_portal/?game={game_id}"
        
        # Add helpful hints
        response["_help"] = {
            "next_steps": [
                f"Visit {response.get('redirect_url', 'the game URL')} to play" if player_id and response.get("redirect_url") else None,
                f"GET /game/{game_id}/poll to poll for updates",
                f"POST /game/{game_id}/start to start the game (host only)" if updated_lobby.get("status") == "waiting" else None,
                f"POST /lobby/{circle_id}/{game_type}/settings to update settings (host only)" if updated_lobby.get("status") == "waiting" else None
            ],
            "info": f"Action: {action}. Lobby has {len(players)}/{updated_lobby.get('max_players', 4)} players. " +
                   ("Ready to start!" if len(players) >= updated_lobby.get("min_players", 2) else f"Need {updated_lobby.get('min_players', 2) - len(players)} more player(s).")
        }
        # Filter out None values from next_steps
        response["_help"]["next_steps"] = [step for step in response["_help"]["next_steps"] if step is not None]
        
        return response
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in lobby create/join for {circle_id}/{game_type}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to create or join lobby: {str(e)}")

# ============================================================================
# Health Check & Status Endpoints - Easy diagnostics
# ============================================================================

@backend_bp.get("/health")
@backend_bp.get("/status")
async def backend_health_check(request: Request):
    """
    Health check endpoint - Super easy way to verify the Game Portal is working.
    
    Returns:
    - status: "healthy" if everything is working
    - actor_available: Whether the Ray actor is available
    - supported_games: List of supported game types
    - game_modes: Available game modes for each game type
    - max_players: Maximum players per game
    - max_ai: Maximum AI players
    
    Example:
    GET /experiments/game_portal/backend/health
    """
    try:
        actor = get_backend_actor_handle(request)
        actor_available = True
        actor_error = None
    except HTTPException as e:
        actor_available = False
        actor_error = e.detail
    except Exception as e:
        actor_available = False
        actor_error = str(e)
    
    return {
        "status": "healthy" if actor_available else "degraded",
        "actor_available": actor_available,
        "actor_error": actor_error,
        "supported_games": VALID_GAME_TYPES,
        "game_modes": VALID_GAME_MODES,
        "default_game_modes": DEFAULT_GAME_MODES,
        "max_players": MAX_PLAYERS,
        "max_ai_players": MAX_AI_COUNT,
        "min_ai_players": MIN_AI_COUNT,
        "api_base": "https://apps.oblivio-company.com/experiments/game_portal/backend",
        "quick_start_examples": {
            "get_lobby": "/lobby/{circle_id}/{game_type}",
            "create_or_join": "POST /lobby/{circle_id}/{game_type}",
            "auto_create": "/new/{game_type}?ai_count=2"
        }
    }

@backend_bp.get("/test")
async def backend_test_endpoint(request: Request):
    """
    Simple test endpoint - Quick way to test the API and see example responses.
    
    This endpoint demonstrates:
    - How to validate game types
    - How to validate game modes
    - How to validate AI counts
    - Example API calls
    
    Query Parameters:
    - game_type (optional): Test validation with a game type
    - game_mode (optional): Test validation with a game mode
    - ai_count (optional): Test validation with an AI count
    """
    game_type = request.query_params.get("game_type", "dominoes")
    game_mode = request.query_params.get("game_mode")
    ai_count_str = request.query_params.get("ai_count")
    
    results = {
        "test": "Game Portal API Test Endpoint",
        "timestamp": str(datetime.utcnow()),
        "validations": {}
    }
    
    # Test game_type validation
    is_valid, error = validate_game_type(game_type)
    results["validations"]["game_type"] = {
        "value": game_type,
        "is_valid": is_valid,
        "error": error,
        "valid_options": VALID_GAME_TYPES
    }
    
    # Test game_mode validation
    is_valid, error = validate_game_mode(game_type, game_mode)
    results["validations"]["game_mode"] = {
        "value": game_mode,
        "is_valid": is_valid,
        "error": error,
        "valid_options": VALID_GAME_MODES.get(game_type.lower(), []),
        "default": DEFAULT_GAME_MODES.get(game_type.lower())
    }
    
    # Test ai_count validation
    ai_count = None
    if ai_count_str:
        try:
            ai_count = int(ai_count_str)
        except ValueError:
            ai_count = None
    
    is_valid, error, clamped = validate_ai_count(ai_count)
    results["validations"]["ai_count"] = {
        "value": ai_count,
        "is_valid": is_valid,
        "error": error,
        "clamped_value": clamped,
        "valid_range": f"{MIN_AI_COUNT}-{MAX_AI_COUNT}"
    }
    
    # Add example API calls
    base_url = "https://apps.oblivio-company.com/experiments/game_portal/backend"
    results["example_api_calls"] = {
        "get_lobby": f"{base_url}/lobby/test-circle-123/{game_type}",
        "create_or_join": f"POST {base_url}/lobby/test-circle-123/{game_type}",
        "body": {
            "player_id": "test-player-123",
            "game_mode": game_mode or DEFAULT_GAME_MODES.get(game_type.lower(), "unknown"),
            "ai_count": clamped
        }
    }
    
    return results

# Register routes with both /game/... and /api/game/... paths for compatibility
@backend_bp.post("/game/create")
async def backend_create_game(request: Request, create_req: CreateGameRequest):
    """Backend API: Creates a new game lobby. Accessible from *.oblivio-company.com"""
    return await _backend_create_game_impl(request, create_req)

@backend_bp.post("/api/game/create")
async def backend_create_game_api(request: Request, create_req: CreateGameRequest):
    """Backend API: Creates a new game lobby (with /api prefix). Accessible from *.oblivio-company.com"""
    return await _backend_create_game_impl(request, create_req)

@backend_bp.post("/game/{game_id}/join")
async def backend_join_game(request: Request, game_id: str, join_req: JoinGameRequest):
    """Backend API: Allows a new player to join a waiting game or mid-game. Accessible from *.oblivio-company.com"""
    return await _backend_join_game_impl(request, game_id, join_req)

@backend_bp.post("/api/game/{game_id}/join")
async def backend_join_game_api(request: Request, game_id: str, join_req: JoinGameRequest):
    """Backend API: Allows a new player to join a waiting game or mid-game (with /api prefix). Accessible from *.oblivio-company.com"""
    return await _backend_join_game_impl(request, game_id, join_req)

@backend_bp.get("/game/{game_id}")
async def backend_get_game(request: Request, game_id: str):
    """Backend API: Get game information. Accessible from *.oblivio-company.com"""
    return await _backend_get_game_impl(request, game_id)

@backend_bp.get("/api/game/{game_id}")
async def backend_get_game_api(request: Request, game_id: str):
    """Backend API: Get game information (with /api prefix). Accessible from *.oblivio-company.com"""
    return await _backend_get_game_impl(request, game_id)

@backend_bp.get("/game/{game_id}/ai-slots")
async def backend_get_ai_slots(request: Request, game_id: str):
    """Backend API: Get available AI slots that can be replaced. Accessible from *.oblivio-company.com"""
    return await _backend_get_ai_slots_impl(request, game_id)

@backend_bp.get("/api/game/{game_id}/ai-slots")
async def backend_get_ai_slots_api(request: Request, game_id: str):
    """Backend API: Get available AI slots that can be replaced (with /api prefix). Accessible from *.oblivio-company.com"""
    return await _backend_get_ai_slots_impl(request, game_id)

@backend_bp.post("/game/{game_id}/start")
async def backend_start_game(request: Request, game_id: str, player_id: str = Body(..., embed=True)):
    """Backend API: Start a game. Accessible from *.oblivio-company.com"""
    return await _backend_start_game_impl(request, game_id, player_id)

@backend_bp.post("/api/game/{game_id}/start")
async def backend_start_game_api(request: Request, game_id: str, player_id: str = Body(..., embed=True)):
    """Backend API: Start a game (with /api prefix). Accessible from *.oblivio-company.com"""
    return await _backend_start_game_impl(request, game_id, player_id)

@backend_bp.get("/game/{game_id}/state")
async def backend_get_game_state(request: Request, game_id: str, player_id: Optional[str] = None):
    """Backend API: Get game state (sanitized for player if player_id provided). Accessible from *.oblivio-company.com"""
    return await _backend_get_game_state_impl(request, game_id, player_id)

@backend_bp.get("/api/game/{game_id}/state")
async def backend_get_game_state_api(request: Request, game_id: str, player_id: Optional[str] = None):
    """Backend API: Get game state (sanitized for player if player_id provided, with /api prefix). Accessible from *.oblivio-company.com"""
    return await _backend_get_game_state_impl(request, game_id, player_id)

@backend_bp.get("/new/{game_type}")
async def backend_auto_create_game(
    request: Request,
    game_type: str,
    game_mode: Optional[str] = None,
    ai_count: int = 0,
    player_id: Optional[str] = None
):
    """Backend API: Automatically create a new game and auto-join the player. Accessible from *.oblivio-company.com"""
    return await _backend_auto_create_game_impl(request, game_type, game_mode, ai_count, player_id)

@backend_bp.get("/api/new/{game_type}")
async def backend_auto_create_game_api(
    request: Request,
    game_type: str,
    game_mode: Optional[str] = None,
    ai_count: int = 0,
    player_id: Optional[str] = None
):
    """Backend API: Automatically create a new game and auto-join the player (with /api prefix). Accessible from *.oblivio-company.com"""
    return await _backend_auto_create_game_impl(request, game_type, game_mode, ai_count, player_id)

@backend_bp.get("/health")
async def backend_health_check(request: Request):
    """Health check endpoint for backend API"""
    return {"status": "ok", "service": "game_portal_backend"}

@backend_bp.get("/game/{game_id}/poll")
async def backend_poll_game_updates(request: Request, game_id: str, last_update: Optional[str] = None):
    """Backend API: Poll for game updates (players joined, game started, etc.). Accessible from *.oblivio-company.com"""
    logger.info(f"Poll route hit: game_id={game_id}, path={request.url.path}")
    return await _backend_poll_game_updates_impl(request, game_id, last_update)

@backend_bp.get("/api/game/{game_id}/poll")
async def backend_poll_game_updates_api(request: Request, game_id: str, last_update: Optional[str] = None):
    """Backend API: Poll for game updates (players joined, game started, etc., with /api prefix). Accessible from *.oblivio-company.com"""
    return await _backend_poll_game_updates_impl(request, game_id, last_update)

@backend_bp.post("/lobby/{circle_id}/{game_type}")
async def backend_lobby_create_or_join(
    request: Request, 
    circle_id: str, 
    game_type: str, 
    player_id: Optional[str] = Body(None, embed=True),
    game_mode: Optional[str] = Body(None, embed=True),
    ai_count: Optional[int] = Body(None, embed=True)
):
    """
    Third-Party Integration API: Get or create a persistent lobby and optionally join a player.
    
    This is a unified, flexible endpoint for any third-party system to integrate games.
    Works with any context identifier - could be a circle ID, group ID, room ID, organization ID, etc.
    
    Path Parameters:
    - circle_id: Any unique context identifier (string) - your system's ID for the context/group/room
    - game_type: "dominoes"
    
    Request Body:
    - player_id (optional): Player ID to join the lobby. If provided, includes redirect_url in response.
    - game_mode (optional): Game style - "classic" or "boricua" for dominoes
    - ai_count (optional): Number of AI players (0-3). Only applied if player is host or lobby is empty.
    
    Response includes redirect_url if player_id is provided, allowing seamless redirect to game portal.
    
    Accessible from *.oblivio-company.com
    """
    return await _backend_lobby_create_or_join_impl(request, circle_id, game_type, player_id or "", game_mode, ai_count)

@backend_bp.get("/lobby/{circle_id}/{game_type}")
async def backend_lobby_get(request: Request, circle_id: str, game_type: str):
    """
    Third-Party Integration API: Get a persistent lobby for a context and game type.
    
    Returns lobby state even if it has 0 players. Perfect for displaying "X players waiting" in third-party UIs.
    
    This is a unified, flexible endpoint for any third-party system to check lobby status.
    Works with any context identifier - could be a circle ID, group ID, room ID, organization ID, etc.
    
    Path Parameters:
    - circle_id: Any unique context identifier (string) - your system's ID for the context/group/room
    - game_type: "dominoes"
    
    Accessible from *.oblivio-company.com
    """
    try:
        actor = get_backend_actor_handle(request)
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Unexpected error getting actor handle: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
    
    # Validate game_type with helpful error messages
    is_valid, error_msg = validate_game_type(game_type)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error_msg)
    game_type = game_type.lower().strip()  # Normalize
    
    try:
        # Get or create persistent lobby
        lobby = await actor.get_or_create_lobby.remote(circle_id, game_type)
        
        # Check if the lobby response contains an error
        if isinstance(lobby, dict) and "error" in lobby:
            error_msg = lobby.get("error", "Unknown error")
            logger.error(f"Failed to get or create lobby for {circle_id}/{game_type}: {error_msg}")
            raise HTTPException(status_code=500, detail=f"Failed to get or create lobby: {error_msg}")
        
        game_id = lobby.get("game_id")
        if not game_id:
            logger.error(f"Lobby response missing game_id for {circle_id}/{game_type}. Response: {lobby}")
            raise HTTPException(status_code=500, detail="Failed to get or create lobby: lobby response missing game_id")
        
        # Filter out placeholder players from response
        players = [p for p in lobby.get("players", []) if not (isinstance(p, str) and p.startswith("PLACEHOLDER_"))]
        
        max_players = lobby.get("max_players", 4)
        
        return {
            "game_id": game_id,
            "game_type": game_type,
            "game_mode": lobby.get("game_mode"),
            "ai_count": len(lobby.get("ai_players", [])),
            "players": players,
            "ai_players": lobby.get("ai_players", []),
            "player_count": len(players),
            "status": lobby.get("status", "waiting"),
            "min_players": lobby.get("min_players"),
            "max_players": max_players,
            "can_join": len(players) < max_players,
            "_help": {
                "next_steps": [
                    f"POST /lobby/{circle_id}/{game_type} with player_id to join",
                    f"GET /game/{game_id}/poll to poll for updates",
                    f"POST /lobby/{circle_id}/{game_type}/settings to update settings (host only)"
                ] if len(players) < max_players else [
                    f"GET /game/{game_id}/poll to poll for game start",
                    f"POST /game/{game_id}/start to start the game (host only)"
                ],
                "info": f"Lobby has {len(players)}/{max_players} players. " + 
                       ("Ready to join!" if len(players) < max_players else "Full - waiting to start.")
            }
        }
    except Exception as e:
        logger.error(f"Error getting lobby for {circle_id}/{game_type}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get lobby: {str(e)}")

@backend_bp.post("/api/lobby/{circle_id}/{game_type}")
async def backend_lobby_create_or_join_api(
    request: Request, 
    circle_id: str, 
    game_type: str, 
    player_id: Optional[str] = Body(None, embed=True),
    game_mode: Optional[str] = Body(None, embed=True),
    ai_count: Optional[int] = Body(None, embed=True)
):
    """
    Third-Party Integration API: Get or create a persistent lobby and optionally join a player (with /api prefix).
    Accessible from *.oblivio-company.com
    """
    return await _backend_lobby_create_or_join_impl(request, circle_id, game_type, player_id or "", game_mode, ai_count)

@backend_bp.get("/api/lobby/{circle_id}/{game_type}")
async def backend_lobby_get_api(request: Request, circle_id: str, game_type: str):
    """
    Third-Party Integration API: Get a persistent lobby for a context and game type (with /api prefix).
    Returns lobby state even if it has 0 players.
    Accessible from *.oblivio-company.com
    """
    return await backend_lobby_get(request, circle_id, game_type)

@backend_bp.post("/lobby/{circle_id}/{game_type}/settings")
async def backend_lobby_update_settings(
    request: Request,
    circle_id: str,
    game_type: str,
    player_id: str = Body(..., embed=True),
    game_mode: Optional[str] = Body(None, embed=True),
    ai_count: Optional[int] = Body(None, embed=True)
):
    """
    Third-Party Integration API: Update lobby settings (host only).
    
    This is a unified, flexible endpoint for any third-party system to update lobby settings.
    Works with any context identifier - could be a circle ID, group ID, room ID, organization ID, etc.
    
    Path Parameters:
    - circle_id: Any unique context identifier (string) - your system's ID for the context/group/room
    - game_type: "dominoes"
    
    Request Body:
    - player_id (required): Player ID (must be the host)
    - game_mode (optional): Game style - "classic" or "boricua" for dominoes
    - ai_count (optional): Number of AI players (0-3)
    
    Only the host can update lobby settings. If the player is not the host, returns an error.
    
    Accessible from *.oblivio-company.com
    """
    try:
        actor = get_backend_actor_handle(request)
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Unexpected error getting actor handle: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
    
    # Validate game_type with helpful error messages
    is_valid, error_msg = validate_game_type(game_type)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error_msg)
    game_type = game_type.lower().strip()  # Normalize
    
    # Validate game_mode if provided
    if game_mode is not None:
        is_valid, error_msg = validate_game_mode(game_type, game_mode)
        if not is_valid:
            raise HTTPException(status_code=400, detail=error_msg)
        game_mode = game_mode.lower().strip()  # Normalize
    
    # Validate and clamp ai_count if provided
    if ai_count is not None:
        is_valid, error_msg, clamped_value = validate_ai_count(ai_count)
        if not is_valid:
            raise HTTPException(status_code=400, detail=error_msg)
        ai_count = clamped_value
    
    try:
        # Get or create persistent lobby
        lobby = await actor.get_or_create_lobby.remote(circle_id, game_type)
        
        # Check if the lobby response contains an error
        if isinstance(lobby, dict) and "error" in lobby:
            error_msg = lobby.get("error", "Unknown error")
            logger.error(f"Failed to get or create lobby for {circle_id}/{game_type}: {error_msg}")
            raise HTTPException(status_code=500, detail=f"Failed to get or create lobby: {error_msg}")
        
        game_id = lobby.get("game_id")
        if not game_id:
            logger.error(f"Lobby response missing game_id for {circle_id}/{game_type}. Response: {lobby}")
            raise HTTPException(status_code=500, detail="Failed to get or create lobby: lobby response missing game_id")
        
        # Get current game state
        game = await actor.get_game.remote(game_id)
        if not game:
            raise HTTPException(status_code=404, detail="Lobby not found")
        
        # Check if player is the host
        if game.get("host_id") != player_id:
            raise HTTPException(status_code=403, detail="Only the host can update lobby settings")
        
        # Check if game is in waiting state
        if game.get("status") != "waiting":
            raise HTTPException(status_code=400, detail="Can only update settings when lobby is waiting")
        
        # Update settings by joining with new settings (host can always update)
        join_result = await actor.join_game.remote(
            game_id,
            player_id,
            replace_ai=None,
            game_mode=game_mode,
            ai_count=ai_count
        )
        
        if join_result.get("error"):
            raise HTTPException(status_code=400, detail=join_result.get("error"))
        
        # Get updated lobby info
        updated_lobby = await actor.get_game.remote(game_id)
        if not updated_lobby:
            raise HTTPException(status_code=500, detail="Failed to retrieve updated lobby")
        
        # Filter out placeholder players from response
        players = [p for p in updated_lobby.get("players", []) if not (isinstance(p, str) and p.startswith("PLACEHOLDER_"))]
        
        return {
            "game_id": game_id,
            "game_type": game_type,
            "game_mode": updated_lobby.get("game_mode"),
            "ai_count": len(updated_lobby.get("ai_players", [])),
            "players": players,
            "player_count": len(players),
            "status": updated_lobby.get("status", "waiting"),
            "min_players": updated_lobby.get("min_players"),
            "max_players": updated_lobby.get("max_players"),
            "message": "Lobby settings updated successfully"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating lobby settings for {circle_id}/{game_type}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update lobby settings: {str(e)}")

@backend_bp.post("/api/lobby/{circle_id}/{game_type}/settings")
async def backend_lobby_update_settings_api(
    request: Request,
    circle_id: str,
    game_type: str,
    player_id: str = Body(..., embed=True),
    game_mode: Optional[str] = Body(None, embed=True),
    ai_count: Optional[int] = Body(None, embed=True)
):
    """
    Third-Party Integration API: Update lobby settings (host only, with /api prefix).
    Accessible from *.oblivio-company.com
    """
    return await backend_lobby_update_settings(request, circle_id, game_type, player_id, game_mode, ai_count)

@backend_bp.get("/websocket/url/{game_id}/{player_id}")
async def get_websocket_url(request: Request, game_id: str, player_id: str):
    """
    Third-Party Integration API: Get WebSocket URL for real-time game updates.
    
    Returns the WebSocket URL that third-party systems can use to connect for real-time updates.
    The WebSocket connection provides:
    - Real-time game state updates
    - Player join/leave notifications
    - Game start notifications
    - Move updates
    - State synchronization
    
    Path Parameters:
    - game_id: The game ID from the lobby
    - player_id: The player ID to connect as
    
    Accessible from *.oblivio-company.com
    """
    # Get the base URL from the request
    base_url = str(request.base_url).rstrip('/')
    
    # Construct WebSocket URL (convert http/https to ws/wss)
    ws_url = base_url.replace('http://', 'ws://').replace('https://', 'wss://')
    websocket_path = f"/experiments/game_portal/ws/game/{game_id}/{player_id}"
    full_ws_url = f"{ws_url}{websocket_path}"
    
    return {
        "websocket_url": full_ws_url,
        "game_id": game_id,
        "player_id": player_id,
        "protocol": "websocket",
        "message_format": "json"
    }

@backend_bp.get("/api/websocket/url/{game_id}/{player_id}")
async def get_websocket_url_api(request: Request, game_id: str, player_id: str):
    """
    Third-Party Integration API: Get WebSocket URL for real-time game updates (with /api prefix).
    Accessible from *.oblivio-company.com
    """
    return await get_websocket_url(request, game_id, player_id)




# --- WebSocket Endpoint ---

@bp.websocket("/ws/game/{game_id}/{player_id}")
async def websocket_endpoint(websocket: WebSocket, game_id: str, player_id: str):
    """WebSocket endpoint for real-time game communication."""
    # Accept connection FIRST before doing any validation
    # This is required - you cannot close a WebSocket before accepting it
    await websocket.accept()
    
    try:
        actor = await get_actor_handle_ws(websocket)
        if not actor:
            await websocket.close(code=503, reason="Service unavailable")
            return
        
        # Verify game and player
        game = await actor.get_game.remote(game_id)
        if not game:
            await websocket.close(code=1008, reason="Game not found")
            return
        
        player_ids = [p.get('player_id') if isinstance(p, dict) else p for p in game.get('players', [])]
        
        # Allow connection only if player is in game
        if player_id not in player_ids:
            await websocket.close(code=1008, reason="Player not in game")
            return
        
        # Store connection
        if game_id not in active_connections:
            active_connections[game_id] = {}
        active_connections[game_id][player_id] = websocket
        logger.info(f"Player {player_id} connected to game {game_id}.")
        
        # Send initial state
        sanitized_state = await actor.sanitize_game_state_for_player.remote(
            game.get('game_type'), game.get('game_state'), player_id
        )
        
        # Mark AI players - return actual game state accurately
        players_with_ai_status = []
        for p in game.get('players', []):
            # Handle both string and dict player formats
            if isinstance(p, str):
                pid = p
                player_dict = {"player_id": pid}
            elif isinstance(p, dict):
                player_dict = p.copy()
                pid = player_dict.get('player_id', player_dict.get('playerId', player_dict.get('id')))
                if not pid:
                    logger.warning(f"Invalid player dict format (no player_id): {p}")
                    continue
            else:
                logger.warning(f"Invalid player format (not string or dict): {p}")
                continue
            
            # Ensure player_id is set correctly
            player_dict['player_id'] = pid
            player_dict['isAI'] = await actor.is_ai_player.remote(game_id, pid)
            players_with_ai_status.append(player_dict)
        
        is_host = game.get('host_id') == player_id
        game_status = game.get('status', 'waiting')
        
        await websocket.send_json({
            "type": "connection_success",
            "game_state": sanitized_state,
            "players": players_with_ai_status,
            "game_type": game.get('game_type'),
            "game_status": game_status,
            "is_host": is_host
        })
        
        await broadcast_to_game(game_id, {
            "type": "player_connected",
            "player_id": player_id
        })
        
        try:
            while True:
                data = await websocket.receive_json()
                action_type = data.get('type')
                
                # Get fresh game state
                current_game = await actor.get_game.remote(game_id)
                if not current_game:
                    await websocket.send_json({"type": "error", "message": "Game not found"})
                    continue
                
                if action_type == 'start_game':
                    # Allow any player in the game to start (not just host)
                    # This allows players joining from mycircles to start the game
                    player_ids = [p.get('player_id') if isinstance(p, dict) else p for p in current_game.get('players', [])]
                    if player_id not in player_ids:
                        await websocket.send_json({"type": "error", "message": "You must be in the game to start it."})
                        continue
                    
                    result = await actor.start_game.remote(game_id, player_id)
                    if result.get('error'):
                        await websocket.send_json({"type": "error", "message": result['error']})
                        continue
                    
                    # Broadcast game started
                    updated_game = await actor.get_game.remote(game_id)
                    players_with_ai_status = []
                    for p in updated_game.get('players', []):
                        player_dict = p.copy() if isinstance(p, dict) else {"player_id": p}
                        pid = player_dict.get('player_id', player_dict)
                        player_dict['isAI'] = await actor.is_ai_player.remote(game_id, pid)
                        players_with_ai_status.append(player_dict)
                    
                    player_ids = [p.get('player_id') if isinstance(p, dict) else p for p in updated_game.get('players', [])]
                    
                    # Send to all players
                    for pid in player_ids:
                        sanitized = await actor.sanitize_game_state_for_player.remote(
                            updated_game.get('game_type'), updated_game.get('game_state'), pid
                        )
                        await send_to_player(game_id, pid, {
                            "type": "game_started",
                            "game_state": sanitized,
                            "players": players_with_ai_status,
                            "game_status": updated_game.get('status', 'in_progress')
                        })
                    
                    # Process AI moves with state broadcasting
                    await process_ai_moves_with_broadcast(actor, game_id, player_ids)
                
                elif action_type == 'make_move':
                    try:
                        result = await actor.play_move.remote(
                            game_id, player_id, data.get('move_data', {})
                        )
                        if result.get('error'):
                            await websocket.send_json({"type": "error", "message": result['error']})
                            continue
                        
                        # Check if all players are ready and auto-start next round/hand
                        auto_started = False
                        if result.get('all_ready_for_next_round'):
                            logger.info(f"All players ready for next round, auto-starting immediately")
                            await actor.start_next_round.remote(game_id)
                            auto_started = True
                            # Get fresh state after starting next round
                            updated_game = await actor.get_game.remote(game_id)
                            # Rebuild players_with_ai_status for the new round
                            players_with_ai_status = []
                            for p in updated_game.get('players', []):
                                player_dict = p.copy() if isinstance(p, dict) else {"player_id": p}
                                pid = player_dict.get('player_id', player_dict)
                                player_dict['isAI'] = await actor.is_ai_player.remote(game_id, pid)
                                players_with_ai_status.append(player_dict)
                            
                            
                            player_ids = [p.get('player_id') if isinstance(p, dict) else p for p in updated_game.get('players', [])]
                            
                            # Broadcast the new round state
                            for pid in player_ids:
                                sanitized = await actor.sanitize_game_state_for_player.remote(
                                    updated_game.get('game_type'), updated_game.get('game_state'), pid
                                )
                                await send_to_player(game_id, pid, {
                                    "type": "state_update",
                                    "game_state": sanitized,
                                    "players": players_with_ai_status,
                                    "game_status": updated_game.get('status', 'in_progress')
                                })
                            
                            # Process AI moves for the new round
                            await process_ai_moves_with_broadcast(actor, game_id, player_ids)
                            continue  # Skip the rest of the move processing
                        elif result.get('all_ready_for_next_hand'):
                            logger.info(f"All players ready for next hand, auto-starting immediately")
                            await actor.start_next_hand.remote(game_id)
                            auto_started = True
                            # Get fresh state after starting next hand
                            updated_game = await actor.get_game.remote(game_id)
                            # Rebuild players_with_ai_status for the new hand
                            players_with_ai_status = []
                            for p in updated_game.get('players', []):
                                player_dict = p.copy() if isinstance(p, dict) else {"player_id": p}
                                pid = player_dict.get('player_id', player_dict)
                                player_dict['isAI'] = await actor.is_ai_player.remote(game_id, pid)
                                players_with_ai_status.append(player_dict)
                            
                            
                            player_ids = [p.get('player_id') if isinstance(p, dict) else p for p in updated_game.get('players', [])]
                            
                            # Broadcast the new hand state
                            for pid in player_ids:
                                sanitized = await actor.sanitize_game_state_for_player.remote(
                                    updated_game.get('game_type'), updated_game.get('game_state'), pid
                                )
                                await send_to_player(game_id, pid, {
                                    "type": "state_update",
                                    "game_state": sanitized,
                                    "players": players_with_ai_status,
                                    "game_status": updated_game.get('status', 'in_progress')
                                })
                            
                            # Process AI moves for the new hand
                            await process_ai_moves_with_broadcast(actor, game_id, player_ids)
                            continue  # Skip the rest of the move processing
                        
                        # Broadcast updated state (get fresh state after auto-start if needed)
                        updated_game = await actor.get_game.remote(game_id)
                        players_with_ai_status = []
                        for p in updated_game.get('players', []):
                            player_dict = p.copy() if isinstance(p, dict) else {"player_id": p}
                            pid = player_dict.get('player_id', player_dict)
                            player_dict['isAI'] = await actor.is_ai_player.remote(game_id, pid)
                            players_with_ai_status.append(player_dict)
                        
                        player_ids = [p.get('player_id') if isinstance(p, dict) else p for p in updated_game.get('players', [])]
                        
                        # Send to players
                        for pid in player_ids:
                            sanitized = await actor.sanitize_game_state_for_player.remote(
                                updated_game.get('game_type'), updated_game.get('game_state'), pid
                            )
                            await send_to_player(game_id, pid, {
                                "type": "state_update",
                                "game_state": sanitized,
                                "players": players_with_ai_status,
                                "game_status": updated_game.get('status', 'in_progress')
                            })
                        
                        # Note: Auto-start is already handled above when result contains all_ready_for_next_round/hand
                        # This duplicate check is removed to avoid conflicts
                        
                        # Process AI moves with state broadcasting
                        # This will handle AI moves and broadcast state updates
                        # If we auto-started next round/hand, we already processed AI moves above
                        # Otherwise, process AI moves normally
                        if not auto_started:
                            try:
                                await process_ai_moves_with_broadcast(actor, game_id, player_ids)
                            except Exception as e:
                                logger.error(f"Error in AI move processing: {e}", exc_info=True)
                            # Even if AI processing fails, broadcast current state
                            updated_game = await actor.get_game.remote(game_id)
                            if updated_game:
                                players_with_ai_status = []
                                for p in updated_game.get('players', []):
                                    player_dict = p.copy() if isinstance(p, dict) else {"player_id": p}
                                    pid = player_dict.get('player_id', player_dict)
                                    player_dict['isAI'] = await actor.is_ai_player.remote(game_id, pid)
                                    players_with_ai_status.append(player_dict)
                                
                                
                                for pid in player_ids:
                                    sanitized = await actor.sanitize_game_state_for_player.remote(
                                        updated_game.get('game_type'), updated_game.get('game_state'), pid
                                    )
                                    await send_to_player(game_id, pid, {
                                        "type": "state_update",
                                        "game_state": sanitized,
                                        "players": players_with_ai_status,
                                        "game_status": updated_game.get('status', 'in_progress')
                                    })
                    except Exception as e:
                        logger.error(f"Error processing move: {e}", exc_info=True)
                        await websocket.send_json({"type": "error", "message": f"Failed to process move: {str(e)}"})
                
                elif action_type == 'ready_for_next_round':
                    result = await actor.ready_for_next_round.remote(game_id, player_id)
                    if result.get('error'):
                        await websocket.send_json({"type": "error", "message": result['error']})
                        continue
                    
                    # Broadcast updated state
                    updated_game = await actor.get_game.remote(game_id)
                    players_with_ai_status = []
                    for p in updated_game.get('players', []):
                        player_dict = p.copy() if isinstance(p, dict) else {"player_id": p}
                        player_dict['isAI'] = await actor.is_ai_player.remote(game_id, player_dict.get('player_id', player_dict))
                        players_with_ai_status.append(player_dict)
                    
                    player_ids = [p.get('player_id') if isinstance(p, dict) else p for p in updated_game.get('players', [])]
                    for pid in player_ids:
                        sanitized = await actor.sanitize_game_state_for_player.remote(
                            updated_game.get('game_type'), updated_game.get('game_state'), pid
                        )
                        await send_to_player(game_id, pid, {
                            "type": "state_update",
                            "game_state": sanitized,
                            "players": players_with_ai_status
                        })
                    
                    # Check if all ready and start next round
                    if result.get('all_ready'):
                        await actor.start_next_round.remote(game_id)
                        updated_game = await actor.get_game.remote(game_id)
                        players_with_ai_status = []
                        for p in updated_game.get('players', []):
                            player_dict = p.copy() if isinstance(p, dict) else {"player_id": p}
                            player_dict['isAI'] = await actor.is_ai_player.remote(game_id, player_dict.get('player_id', player_dict))
                            players_with_ai_status.append(player_dict)
                        
                        for pid in player_ids:
                            sanitized = await actor.sanitize_game_state_for_player.remote(
                                updated_game.get('game_type'), updated_game.get('game_state'), pid
                            )
                            await send_to_player(game_id, pid, {
                                "type": "state_update",
                                "game_state": sanitized,
                                "players": players_with_ai_status
                            })
                        
                        await process_ai_moves_with_broadcast(actor, game_id, player_ids)
                
                elif action_type == 'ready_for_next_hand':
                    result = await actor.ready_for_next_hand.remote(game_id, player_id)
                    if result.get('error'):
                        await websocket.send_json({"type": "error", "message": result['error']})
                        continue
                    
                    # Broadcast updated state
                    updated_game = await actor.get_game.remote(game_id)
                    players_with_ai_status = []
                    for p in updated_game.get('players', []):
                        player_dict = p.copy() if isinstance(p, dict) else {"player_id": p}
                        player_dict['isAI'] = await actor.is_ai_player.remote(game_id, player_dict.get('player_id', player_dict))
                        players_with_ai_status.append(player_dict)
                    
                    player_ids = [p.get('player_id') if isinstance(p, dict) else p for p in updated_game.get('players', [])]
                    for pid in player_ids:
                        sanitized = await actor.sanitize_game_state_for_player.remote(
                            updated_game.get('game_type'), updated_game.get('game_state'), pid
                        )
                        await send_to_player(game_id, pid, {
                            "type": "state_update",
                            "game_state": sanitized,
                            "players": players_with_ai_status
                        })
                    
                    # Check if all ready and start next hand
                    if result.get('all_ready'):
                        await actor.start_next_hand.remote(game_id)
                        updated_game = await actor.get_game.remote(game_id)
                        players_with_ai_status = []
                        for p in updated_game.get('players', []):
                            player_dict = p.copy() if isinstance(p, dict) else {"player_id": p}
                            player_dict['isAI'] = await actor.is_ai_player.remote(game_id, player_dict.get('player_id', player_dict))
                            players_with_ai_status.append(player_dict)
                        
                        for pid in player_ids:
                            sanitized = await actor.sanitize_game_state_for_player.remote(
                                updated_game.get('game_type'), updated_game.get('game_state'), pid
                            )
                            await send_to_player(game_id, pid, {
                                "type": "state_update",
                                "game_state": sanitized,
                                "players": players_with_ai_status
                            })
                        
                        await process_ai_moves_with_broadcast(actor, game_id, player_ids)
        
        except WebSocketDisconnect:
            logger.info(f"Player {player_id} disconnected from game {game_id}.")
            if game_id in active_connections and player_id in active_connections[game_id]:
                del active_connections[game_id][player_id]
            await broadcast_to_game(game_id, {
                "type": "player_disconnected",
                "player_id": player_id
            })
        except Exception as e:
            logger.error(f"WebSocket error: {e}", exc_info=True)
            if game_id in active_connections and player_id in active_connections[game_id]:
                del active_connections[game_id][player_id]
    except Exception as e:
        logger.error(f"WebSocket connection error: {e}", exc_info=True)
        if game_id in active_connections and player_id in active_connections[game_id]:
            del active_connections[game_id][player_id]

