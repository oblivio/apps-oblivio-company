import logging
import ray
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette import status

logger = logging.getLogger(__name__)
bp = APIRouter()

async def get_actor_handle(request: Request) -> "ray.actor.ActorHandle":
    slug_id = getattr(request.state, "slug_id", None)
    actor_name = f"{slug_id}-actor"
    try:
        return ray.get_actor(actor_name, namespace="modular_labs")
    except ValueError:
        raise HTTPException(503, f"Experiment service '{actor_name}' is not running.")

@bp.get("/", response_class=HTMLResponse)
async def show_gallery(request: Request, actor=Depends(get_actor_handle)):
    try:
        html = await actor.render_gallery_page.remote({"url": str(request.url)})
        return HTMLResponse(html)
    except Exception as e:
        return HTMLResponse(f"<h1>Actor Error</h1><pre>{e}</pre>", status_code=500)

@bp.get("/workout/{workout_id}", response_class=HTMLResponse)
async def show_detail(workout_id: int, request: Request, actor=Depends(get_actor_handle)):
    try:
        html = await actor.render_detail_page.remote(workout_id, {"url": str(request.url)})
        return HTMLResponse(html)
    except Exception as e:
        return HTMLResponse(f"<h1>Actor Error</h1><pre>{e}</pre>", status_code=500)

@bp.post("/generate")
async def generate(request: Request, actor=Depends(get_actor_handle)):
    try:
        suffix = await actor.generate_one.remote()
        return RedirectResponse(request.url_for("show_detail", workout_id=suffix), status_code=303)
    except Exception as e:
        raise HTTPException(500, f"Generation failed: {e}")

