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
from typing import Any, Dict, Optional
from pathlib import Path
from .actor import ExperimentActor

logger = logging.getLogger(__name__)
bp = APIRouter()

# Backend API router with CORS support for *.oblivio-company.com
# Note: prefix is added when mounting, so don't include it here
backend_bp = APIRouter(tags=["Game Portal Backend API"])

# Path setup
EXPERIMENT_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(EXPERIMENT_DIR / "templates"))

# Connection manager for WebSocket connections
# { "game_id": { "player_id": WebSocket } }
active_connections: Dict[str, Dict[str, WebSocket]] = {}


def get_actor_handle(request: Request) -> "ray.actor.ActorHandle":
    """FastAPI Dependency to get the Game Portal actor handle."""
    if not getattr(request.app.state, "ray_is_available", False):
        logger.error("Ray is globally unavailable, blocking actor handle request.")
        raise HTTPException(
            status_code=503,
            detail="Ray service is unavailable. Check Ray cluster status."
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
    
    if not getattr(websocket.app.state, "ray_is_available", False):
        logger.error("Ray is globally unavailable in WebSocket")
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
    game_type: str = Field(default="blackjack", description="e.g., 'dominoes' or 'blackjack'. Defaults to 'blackjack'")
    game_mode: Optional[str] = Field(default=None, description="For dominoes: 'classic' or 'boricua'. For blackjack: 'best_of_5' or 'best_of_10'. Defaults based on game_type")
    ai_count: int = Field(default=0, ge=0, le=3, description="Number of AI players to add (0-3, max 3, 4 players total max)")
    
    def __init__(self, **data):
        super().__init__(**data)
        # Set default game_mode based on game_type if not provided
        if self.game_mode is None:
            if self.game_type == "dominoes":
                self.game_mode = "classic"
            else:  # blackjack or unknown
                self.game_mode = "best_of_5"


class JoinGameRequest(BaseModel):
    player_id: str = Field(..., min_length=1, max_length=200)
    replace_ai: Optional[str] = Field(None, description="AI player ID to replace (for mid-game joining)")
    as_spectator: bool = Field(False, description="Join as spectator (for mid-game joining)")


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
    """Allows a new player to join a waiting game or mid-game (as spectator or replacing AI)."""
    actor = get_actor_handle(request)
    result = await actor.join_game.remote(
        game_id, 
        join_req.player_id,
        join_req.replace_ai,
        join_req.as_spectator
    )
    
    if result.get("error"):
        return result
    
    # Notify lobby via WebSocket
    await broadcast_to_game(game_id, {
        "type": "player_joined",
        "player_id": join_req.player_id,
        "role": result.get("role", "player"),
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
        "spectators": game.get('spectators', []),
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
        from sub_auth import get_experiment_sub_user
        from experiment_db import get_experiment_db
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
        from sub_auth import get_experiment_sub_user
        from experiment_db import get_experiment_db
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


@bp.get("/new/{game_type}", response_class=HTMLResponse, name="auto_create_game")
async def auto_create_game(
    request: Request, 
    game_type: str,
    game_mode: Optional[str] = None,
    ai_count: int = 0
):
    """
    Automatically create a new game of the specified type, auto-join the player, and redirect to the lobby.
    
    Query parameters:
    - game_mode: Optional game mode (e.g., 'classic', 'boricua' for dominoes; 'best_of_5', 'best_of_10' for blackjack)
    - ai_count: Number of AI players to add (0-3, defaults to 0)
    """
    # Validate game_type
    if game_type not in ["blackjack", "dominoes"]:
        raise HTTPException(status_code=400, detail=f"Invalid game_type: {game_type}. Must be 'blackjack' or 'dominoes'")
    
    # Get or generate player_id
    player_id = await _get_or_generate_player_id(request)
    
    # Get actor handle
    actor = get_actor_handle(request)
    
    # Set default game_mode if not provided
    if game_mode is None:
        if game_type == "dominoes":
            game_mode = "classic"
        else:  # blackjack
            game_mode = "best_of_5"
    
    # Clamp ai_count to valid range
    ai_count = max(0, min(3, ai_count))
    
    # Create game
    try:
        result = await actor.create_game.remote(
            player_id=player_id,
            game_type=game_type,
            game_mode=game_mode,
            ai_count=ai_count
        )
        
        if result.get("error"):
            raise HTTPException(status_code=400, detail=result["error"])
        
        game_id = result.get("game_id")
        if not game_id:
            raise HTTPException(status_code=500, detail="Failed to create game")
        
        # Auto-join the player (they're already the host, but ensure they're in the game)
        # The create_game already adds the player, so we just need to redirect
        
        # Redirect to the game lobby with the game_id
        from fastapi.responses import RedirectResponse
        # Get the base path by removing /new/{game_type} from the path
        base_path = request.url.path.rsplit("/new/", 1)[0]
        if not base_path:
            base_path = "/"
        # Use relative URL for redirect
        redirect_url = f"{base_path}?game={game_id}&player_id={player_id}"
        return RedirectResponse(url=redirect_url, status_code=302)
        
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
        actor = get_actor_handle(request)
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
        actor = get_actor_handle(request)
    except HTTPException as e:
        # Re-raise HTTPException (includes 503 for Ray unavailable)
        raise e
    except Exception as e:
        logger.error(f"Unexpected error getting actor handle: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
    
    result = await actor.join_game.remote(
        game_id, 
        join_req.player_id,
        join_req.replace_ai,
        join_req.as_spectator
    )
    
    if result.get("error"):
        return result
    
    # Notify lobby via WebSocket
    await broadcast_to_game(game_id, {
        "type": "player_joined",
        "player_id": join_req.player_id,
        "role": result.get("role", "player"),
        "replaced_ai": result.get("replaced_ai")
    })
    
    return result

async def _backend_get_game_impl(request: Request, game_id: str):
    """Implementation for getting game info."""
    try:
        actor = get_actor_handle(request)
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
        "spectators": game.get('spectators', []),
        "host_id": game.get('host_id'),
        "min_players": game.get('min_players'),
        "max_players": game.get('max_players'),
    }
    return public_game

async def _backend_get_ai_slots_impl(request: Request, game_id: str):
    """Implementation for getting AI slots."""
    try:
        actor = get_actor_handle(request)
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
        actor = get_actor_handle(request)
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
        actor = get_actor_handle(request)
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
            "spectators": game.get('spectators', []),
        }
    
    # Return limited info if no player_id or no game_state
    return {
        "game_id": game_id,
        "game_type": game_type,
        "game_mode": game.get('game_mode'),
        "status": game.get('status'),
        "players": game.get('players', []),
        "ai_players": game.get('ai_players', []),
        "spectators": game.get('spectators', []),
    }

async def _backend_poll_game_updates_impl(request: Request, game_id: str, last_update: Optional[str] = None):
    """
    Backend API: Poll for game updates (players joined, game started, etc.).
    Returns public game information suitable for external sites to display.
    Accessible from *.oblivio-company.com
    """
    try:
        actor = get_actor_handle(request)
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Unexpected error getting actor handle: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
    
    game = await actor.get_game.remote(game_id)
    if not game:
        raise HTTPException(status_code=404, detail="Game not found")
    
    # Build player list with AI status
    players_list = []
    for p in game.get('players', []):
        player_dict = p.copy() if isinstance(p, dict) else {"player_id": p}
        pid = player_dict.get('player_id', player_dict)
        is_ai = await actor.is_ai_player.remote(game_id, pid)
        players_list.append({
            "player_id": pid,
            "is_ai": is_ai,
            "is_spectator": False
        })
    
    # Add spectators
    for spec_id in game.get('spectators', []):
        players_list.append({
            "player_id": spec_id,
            "is_ai": False,
            "is_spectator": True
        })
    
    # Determine if there are updates since last poll
    # For simplicity, we'll always return current state
    # External sites can compare timestamps or player counts to detect changes
    game_status = game.get('status', 'waiting')
    
    # Build response with all relevant info for external display
    response = {
        "game_id": game_id,
        "game_type": game.get('game_type'),
        "game_mode": game.get('game_mode'),
        "status": game_status,
        "host_id": game.get('host_id'),
        "players": players_list,
        "player_count": len(game.get('players', [])),
        "spectator_count": len(game.get('spectators', [])),
        "min_players": game.get('min_players'),
        "max_players": game.get('max_players'),
        "can_start": game_status == 'waiting' and len(game.get('players', [])) >= game.get('min_players', 2),
        "is_started": game_status in ['in_progress', 'round_finished', 'hand_finished'],
        "is_finished": game_status == 'finished',
        # Include basic game state info if game is in progress (without sensitive data)
        "game_state_summary": None
    }
    
    # Add game state summary if game is in progress (public info only)
    game_state = game.get('game_state')
    if game_state and game_status in ['in_progress', 'round_finished', 'hand_finished']:
        game_type = game.get('game_type')
        if game_type == 'blackjack':
            # Public blackjack info
            response["game_state_summary"] = {
                "round_number": game_state.get('round_number', 1),
                "current_turn": game_state.get('players', [])[game_state.get('current_turn_index', 0)] if game_state.get('current_turn_index') is not None else None,
                "dealer_value": game_state.get('dealer_value', 0) if game_status != 'in_progress' else None,  # Only show if round finished
                "scores": game_state.get('scores', {}),
                "hand_wins": game_state.get('hand_wins', {})
            }
        elif game_type == 'dominoes':
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
        actor = get_actor_handle(request)
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Unexpected error getting actor handle: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
    
    # Validate game_type
    if game_type not in ["blackjack", "dominoes"]:
        raise HTTPException(status_code=400, detail=f"Invalid game_type: {game_type}. Must be 'blackjack' or 'dominoes'")
    
    # Get or generate player_id
    if not player_id:
        player_id = await _get_or_generate_player_id(request)
    
    # Set default game_mode if not provided
    if game_mode is None:
        if game_type == "dominoes":
            game_mode = "classic"
        else:  # blackjack
            game_mode = "best_of_5"
    
    # Clamp ai_count to valid range
    ai_count = max(0, min(3, ai_count))
    
    # Create game
    result = await actor.create_game.remote(
        player_id=player_id,
        game_type=game_type,
        game_mode=game_mode,
        ai_count=ai_count
    )
    
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    
    return result

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

@backend_bp.get("/game/{game_id}/poll")
async def backend_poll_game_updates(request: Request, game_id: str, last_update: Optional[str] = None):
    """Backend API: Poll for game updates (players joined, game started, etc.). Accessible from *.oblivio-company.com"""
    return await _backend_poll_game_updates_impl(request, game_id, last_update)

@backend_bp.get("/api/game/{game_id}/poll")
async def backend_poll_game_updates_api(request: Request, game_id: str, last_update: Optional[str] = None):
    """Backend API: Poll for game updates (players joined, game started, etc., with /api prefix). Accessible from *.oblivio-company.com"""
    return await _backend_poll_game_updates_impl(request, game_id, last_update)


# --- WebSocket Endpoint ---

@bp.websocket("/ws/game/{game_id}/{player_id}")
async def websocket_endpoint(websocket: WebSocket, game_id: str, player_id: str):
    """WebSocket endpoint for real-time game communication."""
    actor = await get_actor_handle_ws(websocket)
    if not actor:
        return
    
    # Verify game and player
    game = await actor.get_game.remote(game_id)
    if not game:
        await websocket.close(code=1008, reason="Game not found")
        return
    
    player_ids = [p.get('player_id') if isinstance(p, dict) else p for p in game.get('players', [])]
    spectators = game.get('spectators', [])
    
    # Allow connection if player is in game OR is a spectator
    if player_id not in player_ids and player_id not in spectators:
        await websocket.close(code=1008, reason="Player not in game")
        return
    
    # Connect
    await websocket.accept()
    if game_id not in active_connections:
        active_connections[game_id] = {}
    active_connections[game_id][player_id] = websocket
    logger.info(f"Player {player_id} connected to game {game_id}.")
    
    # Send initial state
    sanitized_state = await actor.sanitize_game_state_for_player.remote(
        game.get('game_type'), game.get('game_state'), player_id
    )
    
    # Mark AI players and spectators
    players_with_ai_status = []
    for p in game.get('players', []):
        player_dict = p.copy() if isinstance(p, dict) else {"player_id": p}
        pid = player_dict.get('player_id', player_dict)
        player_dict['isAI'] = await actor.is_ai_player.remote(game_id, pid)
        player_dict['isSpectator'] = False
        players_with_ai_status.append(player_dict)
    
    # Add spectators
    for spec_id in spectators:
        players_with_ai_status.append({
            "player_id": spec_id,
            "isAI": False,
            "isSpectator": True
        })
    
    # Check if player is a spectator using game state (safer than calling actor)
    is_spectator = player_id in spectators
    is_host = game.get('host_id') == player_id
    game_status = game.get('status', 'waiting')
    
    await websocket.send_json({
        "type": "connection_success",
        "game_state": sanitized_state if not is_spectator else None,  # Spectators don't get game state
        "players": players_with_ai_status,
        "game_type": game.get('game_type'),
        "game_status": game_status,
        "is_spectator": is_spectator,
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
                # Check if player is a spectator using game state
                current_spectators = current_game.get('spectators', [])
                if player_id in current_spectators:
                    await websocket.send_json({"type": "error", "message": "Spectators cannot start games"})
                    continue
                
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
                    player_dict['isSpectator'] = False
                    players_with_ai_status.append(player_dict)
                
                # Add spectators to players list
                spectators = updated_game.get('spectators', [])
                for spec_id in spectators:
                    players_with_ai_status.append({
                        "player_id": spec_id,
                        "isAI": False,
                        "isSpectator": True
                    })
                
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
                
                # Send to spectators (they get limited state)
                for spec_id in spectators:
                    await send_to_player(game_id, spec_id, {
                        "type": "game_started",
                        "game_state": None,  # Spectators don't get game state
                        "players": players_with_ai_status,
                        "game_status": updated_game.get('status', 'in_progress'),
                        "is_spectator": True
                    })
                
                # Process AI moves with state broadcasting
                await process_ai_moves_with_broadcast(actor, game_id, player_ids)
            
            elif action_type == 'make_move':
                # Check if player is a spectator using game state
                current_spectators = current_game.get('spectators', [])
                if player_id in current_spectators:
                    await websocket.send_json({"type": "error", "message": "Spectators cannot make moves"})
                    continue
                
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
                            player_dict['isSpectator'] = False
                            players_with_ai_status.append(player_dict)
                        
                        spectators = updated_game.get('spectators', [])
                        for spec_id in spectators:
                            players_with_ai_status.append({
                                "player_id": spec_id,
                                "isAI": False,
                                "isSpectator": True
                            })
                        
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
                            player_dict['isSpectator'] = False
                            players_with_ai_status.append(player_dict)
                        
                        spectators = updated_game.get('spectators', [])
                        for spec_id in spectators:
                            players_with_ai_status.append({
                                "player_id": spec_id,
                                "isAI": False,
                                "isSpectator": True
                            })
                        
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
                        player_dict['isSpectator'] = False
                        players_with_ai_status.append(player_dict)
                    
                    # Add spectators
                    spectators = updated_game.get('spectators', [])
                    for spec_id in spectators:
                        players_with_ai_status.append({
                            "player_id": spec_id,
                            "isAI": False,
                            "isSpectator": True
                        })
                    
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
                    
                    # Send to spectators (without game state)
                    for spec_id in spectators:
                        await send_to_player(game_id, spec_id, {
                            "type": "state_update",
                            "game_state": None,
                            "players": players_with_ai_status,
                            "game_status": updated_game.get('status', 'in_progress'),
                            "is_spectator": True
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
                                player_dict['isSpectator'] = False
                                players_with_ai_status.append(player_dict)
                            
                            spectators = updated_game.get('spectators', [])
                            for spec_id in spectators:
                                players_with_ai_status.append({
                                    "player_id": spec_id,
                                    "isAI": False,
                                    "isSpectator": True
                                })
                            
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

