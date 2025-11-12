# Thin Client Router for data_imaging_advanced

import logging
import ray
from fastapi import APIRouter, Request, HTTPException, Depends, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from starlette import status
from typing import Any 

from .actor import ExperimentActor

logger = logging.getLogger(__name__)
bp = APIRouter()

async def get_actor_handle(
    request: Request
) -> "ray.actor.ActorHandle":
    """
    FastAPI Dependency to get the handle for the experiment's
    dedicated Ray actor.
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


# --- Thin Client Routes (Pure Forwarders) ---

@bp.get("/", response_class=HTMLResponse)
async def show_gallery(
    request: Request, 
    actor: "ray.actor.ActorHandle" = Depends(get_actor_handle)
):
    """Renders the main gallery by calling the actor."""
    context = {"url": str(request.url)}
    try:
        html = await actor.render_gallery_page.remote(context)
        return HTMLResponse(html)
    except Exception as e:
        logger.error(f"Actor call failed for render_gallery_page: {e}", exc_info=True)
        return HTMLResponse(f"<h1>Actor Error</h1><pre>{e}</pre>", status_code=500)


@bp.get("/workout/{workout_id}", response_class=HTMLResponse)
async def show_detail(
    request: Request, 
    workout_id: int, 
    actor: "ray.actor.ActorHandle" = Depends(get_actor_handle),
    r: str = Query("heart_rate", alias="r_key"),
    g: str = Query("calories_per_min", alias="g_key"),
    b: str = Query("speed_kph", alias="b_key"),
    alpha_mode: str = Query("none", alias="alpha_mode"),
    alpha_key: str = Query("cadence", alias="alpha_key"),
    use_voyage: str = Query("true", alias="use_voyage")
):
    """Renders the full HTML page on initial load, respecting query params."""
    context = {"url": str(request.url)}
    use_voyage_bool = use_voyage.lower() != "false"
    try:
        html = await actor.render_detail_page.remote(
            workout_id, 
            context,
            r_key=r,
            g_key=g,
            b_key=b,
            alpha_mode=alpha_mode,
            alpha_key=alpha_key,
            use_voyage=use_voyage_bool
        )
        return HTMLResponse(html)
    except Exception as e:
        logger.error(f"Actor call failed for render_detail_page: {e}", exc_info=True)
        return HTMLResponse(f"<h1>Actor Error</h1><pre>{e}</pre>", status_code=500)


@bp.get("/workout/{workout_id}/viz", response_class=JSONResponse)
async def get_viz_data(
    request: Request, 
    workout_id: int, 
    actor: "ray.actor.ActorHandle" = Depends(get_actor_handle),
    r_key: str = Query(...),
    g_key: str = Query(...),
    b_key: str = Query(...),
    alpha_mode: str = Query("none", alias="alpha_mode"),
    alpha_key: str = Query("cadence", alias="alpha_key")
):
    """Returns only the updated visualization data as JSON for client-side updates."""
    try:
        viz_json = await actor.get_dynamic_viz_data.remote(
            workout_id, 
            r_key=r_key,
            g_key=g_key,
            b_key=b_key,
            alpha_mode=alpha_mode,
            alpha_key=alpha_key
        )
        return JSONResponse(content=viz_json)
    except Exception as e:
        logger.error(f"Actor call failed for get_dynamic_viz_data: {e}", exc_info=True)
        raise HTTPException(500, f"Actor viz data failed: {e}")


@bp.post("/workout/{workout_id}/analyze")
async def analyze_workout(
    request: Request, 
    workout_id: int, 
    actor: "ray.actor.ActorHandle" = Depends(get_actor_handle),
    use_voyage: str = Query("true", alias="use_voyage")
):
    """Calls the actor to run analysis, then redirects."""
    try:
        use_voyage_bool = use_voyage.lower() != "false"
        await actor.run_analysis.remote(workout_id, use_voyage=use_voyage_bool)
        redirect_url = request.url_for("show_detail", workout_id=workout_id)
        if request.url.query:
            redirect_url = f"{redirect_url}?{request.url.query}"

        return RedirectResponse(
            url=redirect_url, 
            status_code=status.HTTP_303_SEE_OTHER
        )
    except Exception as e:
        logger.error(f"Actor call failed for run_analysis: {e}", exc_info=True)
        raise HTTPException(500, f"Actor analysis failed: {e}")


@bp.post("/generate-demo")
async def generate_demo(
    request: Request,
    actor: "ray.actor.ActorHandle" = Depends(get_actor_handle)
):
    """Calls the Ray Actor to create multiple new workout docs for a demo."""
    NUM_GENERATIONS = 100
    logger.info(f"Initiating bulk generation of {NUM_GENERATIONS} workout docs.")
    try:
        BATCH_SIZE = 25
        total_generated = 0
        
        for batch_start in range(0, NUM_GENERATIONS, BATCH_SIZE):
            batch_end = min(batch_start + BATCH_SIZE, NUM_GENERATIONS)
            batch_size = batch_end - batch_start
            logger.info(f"Generating batch {batch_start // BATCH_SIZE + 1}: workouts {batch_start} to {batch_end - 1}")
            
            tasks = [actor.generate_one.remote() for _ in range(batch_size)]
            batch_results = await ray.get(tasks)
            total_generated += len(batch_results)
            
        logger.info(f"Successfully generated {total_generated} workout docs.")
        redirect_url = request.url_for("show_gallery")
        return RedirectResponse(
            url=redirect_url, 
            status_code=status.HTTP_303_SEE_OTHER
        )
    except Exception as e:
        logger.error(f"Actor call failed during bulk generation: {e}", exc_info=True)
        raise HTTPException(500, f"Actor failed to generate demo docs: {e}")
        

@bp.post("/generate")
async def generate_one(
    request: Request, 
    actor: "ray.actor.ActorHandle" = Depends(get_actor_handle)
):
    """Calls the Ray Actor to create a new workout doc in isolation."""
    try:
        new_suffix = await actor.generate_one.remote()
        redirect_url = request.url_for("show_detail", workout_id=new_suffix)
        return RedirectResponse(
            url=redirect_url, 
            status_code=status.HTTP_303_SEE_OTHER
        )
    except Exception as e:
        logger.error(f"Actor call failed for generate_one: {e}", exc_info=True)
        raise HTTPException(500, f"Actor failed to generate doc: {e}")


@bp.post("/clear")
async def clear_all(
    request: Request, 
    actor: "ray.actor.ActorHandle" = Depends(get_actor_handle)
):
    """Calls the actor to clear all docs in the scoped collection."""
    try:
        await actor.clear_all.remote()
        redirect_url = request.url_for("show_gallery")
        return RedirectResponse(
            url=redirect_url, 
            status_code=status.HTTP_303_SEE_OTHER
        )
    except Exception as e:
        logger.error(f"Actor call failed for clear_all: {e}", exc_info=True)
        raise HTTPException(500, f"Actor failed to clear docs: {e}")


@bp.get("/workout/{workout_id}/compare-encodings", response_class=JSONResponse)
async def compare_encodings(
    request: Request,
    workout_id: int,
    actor: "ray.actor.ActorHandle" = Depends(get_actor_handle)
):
    """
    Compares the hardcoded 192-dim encoding vs the learned 64-dim encoding.
    Shows both vectors side by side with statistics and neighbor comparisons.
    """
    try:
        comparison = await actor.compare_encodings.remote(workout_id)
        return JSONResponse(content=comparison)
    except Exception as e:
        logger.error(f"Actor call failed for compare_encodings: {e}", exc_info=True)
        raise HTTPException(500, f"Comparison failed: {e}")


@bp.get("/model-status", response_class=JSONResponse)
async def get_model_status(
    request: Request,
    actor: "ray.actor.ActorHandle" = Depends(get_actor_handle)
):
    """
    Returns the status of the PyTorch model loading.
    Useful for debugging model loading issues.
    """
    try:
        # Check if actor has model_status method, otherwise return basic info
        if hasattr(actor, 'get_model_status'):
            status = await actor.get_model_status.remote()
        else:
            # Fallback: try to get model info via a test call
            status = {
                "model_loaded": False,
                "error": "Actor does not expose model_status method"
            }
        return JSONResponse(content=status)
    except Exception as e:
        logger.error(f"Failed to get model status: {e}", exc_info=True)
        return JSONResponse(content={"error": str(e)}, status_code=500)

