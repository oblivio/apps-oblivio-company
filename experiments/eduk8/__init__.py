import logging
import ray
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from starlette import status
from pydantic import BaseModel
from typing import Dict, Any

from .actor import ExperimentActor

logger = logging.getLogger(__name__)
bp = APIRouter()

class GenerateRequest(BaseModel):
    topic: str

class SaveRequest(BaseModel):
    module: Dict[str, Any]

async def get_actor_handle(request: Request) -> "ray.actor.ActorHandle":
    """
    FastAPI Dependency to get the handle for the experiment's dedicated Ray actor.
    """
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
async def home(
    request: Request, 
    actor: "ray.actor.ActorHandle" = Depends(get_actor_handle)
):
    """
    Renders the home page by calling the actor.
    """
    context = {"url": str(request.url)}
    try:
        html = await actor.render_home.remote(context)
        return HTMLResponse(html)
    except Exception as e:
        logger.error(f"Actor call failed for render_home: {e}", exc_info=True)
        return HTMLResponse(f"<h1>Actor Error</h1><pre>{e}</pre>", status_code=500)

@bp.post("/api/generate")
async def generate_module(
    payload: GenerateRequest,
    request: Request,
    actor: "ray.actor.ActorHandle" = Depends(get_actor_handle)
):
    """
    Generate a new module based on a topic using LLM.
    """
    try:
        result = await actor.generate_module.remote(payload.topic)
        if "error" in result:
             # Return error as JSON but with 200 OK so frontend can display it nicely, 
             # or 500 if it's a system failure. 
             # For functional errors (e.g. no API key), let's return 400.
             return JSONResponse(status_code=400, content=result)
        return result
    except Exception as e:
        logger.error(f"Generate module failed: {e}", exc_info=True)
        raise HTTPException(500, f"Generation failed: {str(e)}")

@bp.post("/api/save")
async def save_module(
    payload: SaveRequest,
    request: Request,
    actor: "ray.actor.ActorHandle" = Depends(get_actor_handle)
):
    """
    Save a generated module to the persistent storage.
    """
    try:
        result = await actor.save_module.remote(payload.module)
        if "error" in result:
             return JSONResponse(status_code=500, content=result)
        return result
    except Exception as e:
        logger.error(f"Save module failed: {e}", exc_info=True)
        raise HTTPException(500, f"Save failed: {str(e)}")

@bp.get("/preview", response_class=HTMLResponse)
async def preview_course(
    request: Request,
    actor: "ray.actor.ActorHandle" = Depends(get_actor_handle)
):
    """
    Renders a preview page that loads course data from localStorage.
    """
    context = {"url": str(request.url)}
    try:
        html = await actor.render_preview.remote(context)
        return HTMLResponse(html)
    except Exception as e:
        logger.error(f"Actor call failed for render_preview: {e}", exc_info=True)
        return HTMLResponse(f"<h1>Actor Error</h1><pre>{e}</pre>", status_code=500)

@bp.get("/{course_id}", response_class=HTMLResponse)
async def read_course(
    course_id: str,
    request: Request,
    actor: "ray.actor.ActorHandle" = Depends(get_actor_handle)
):
    context = {"url": str(request.url)}
    try:
        html = await actor.render_course.remote(course_id, context)
        if html is None:
             raise HTTPException(status_code=404, detail="Course not found")
        return HTMLResponse(html)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Actor call failed for render_course: {e}", exc_info=True)
        return HTMLResponse(f"<h1>Actor Error</h1><pre>{e}</pre>", status_code=500)

