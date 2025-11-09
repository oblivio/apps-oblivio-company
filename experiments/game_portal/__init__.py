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


# --- Backend API Endpoints (CORS-enabled for *.oblivio-company.com) ---

@backend_bp.post("/game/create")
async def backend_create_game(request: Request, create_req: CreateGameRequest):
    """Backend API: Creates a new game lobby. Accessible from *.oblivio-company.com"""
    actor = get_actor_handle(request)
    result = await actor.create_game.remote(
        player_id=create_req.player_id,
        game_type=create_req.game_type,
        game_mode=create_req.game_mode,
        ai_count=create_req.ai_count
    )
    return result


@backend_bp.post("/game/{game_id}/join")
async def backend_join_game(request: Request, game_id: str, join_req: JoinGameRequest):
    """Backend API: Allows a new player to join a waiting game or mid-game. Accessible from *.oblivio-company.com"""
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


@backend_bp.get("/game/{game_id}")
async def backend_get_game(request: Request, game_id: str):
    """Backend API: Get game information. Accessible from *.oblivio-company.com"""
    actor = get_actor_handle(request)
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


@backend_bp.get("/game/{game_id}/ai-slots")
async def backend_get_ai_slots(request: Request, game_id: str):
    """Backend API: Get available AI slots that can be replaced. Accessible from *.oblivio-company.com"""
    actor = get_actor_handle(request)
    ai_slots = await actor.get_available_ai_slots.remote(game_id)
    return {"ai_slots": ai_slots}


@backend_bp.post("/game/{game_id}/start")
async def backend_start_game(request: Request, game_id: str, player_id: str = Body(..., embed=True)):
    """Backend API: Start a game. Accessible from *.oblivio-company.com"""
    actor = get_actor_handle(request)
    result = await actor.start_game.remote(game_id, player_id)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@backend_bp.get("/game/{game_id}/state")
async def backend_get_game_state(request: Request, game_id: str, player_id: Optional[str] = None):
    """Backend API: Get game state (sanitized for player if player_id provided). Accessible from *.oblivio-company.com"""
    actor = get_actor_handle(request)
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

