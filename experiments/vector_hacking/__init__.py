import logging
import ray
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from starlette import status

from .actor import ExperimentActor

logger = logging.getLogger(__name__)
bp = APIRouter()

async def get_actor_handle(request: Request) -> "ray.actor.ActorHandle":
    """FastAPI Dependency to get the Vector Hacking actor handle."""
    if not getattr(request.app.state, "ray_is_available", False):
        logger.error("Ray is globally unavailable, blocking actor handle request.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
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


@bp.get("/", response_class=HTMLResponse)
async def index(request: Request, actor: "ray.actor.ActorHandle" = Depends(get_actor_handle)):
    """Display the main vector hacking demo page."""
    try:
        html = await actor.render_index.remote()
        return HTMLResponse(html)
    except Exception as e:
        logger.error(f"Actor call failed for render_index: {e}", exc_info=True)
        return HTMLResponse(f"<h1>Actor Error</h1><pre>{e}</pre>", status_code=500)


@bp.post("/start")
async def start_hacking(request: Request, actor: "ray.actor.ActorHandle" = Depends(get_actor_handle)):
    """Start the vector hacking process."""
    try:
        result = await actor.start_hacking.remote()
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"Actor call failed for start_hacking: {e}", exc_info=True)
        raise HTTPException(500, f"Failed to start hacking: {e}")


@bp.post("/stop")
async def stop_hacking(request: Request, actor: "ray.actor.ActorHandle" = Depends(get_actor_handle)):
    """Stop the vector hacking process."""
    try:
        result = await actor.stop_hacking.remote()
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"Actor call failed for stop_hacking: {e}", exc_info=True)
        raise HTTPException(500, f"Failed to stop hacking: {e}")


@bp.get("/api/status")
async def get_status(request: Request, actor: "ray.actor.ActorHandle" = Depends(get_actor_handle)):
    """Get the current status of the vector hacking process."""
    try:
        result = await actor.get_status.remote()
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"Actor call failed for get_status: {e}", exc_info=True)
        raise HTTPException(500, f"Failed to get status: {e}")

