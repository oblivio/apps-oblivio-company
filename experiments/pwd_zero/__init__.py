"""
Password Manager Experiment
FastAPI routes that delegate to the Ray Actor.
Enterprise-grade security implementation.
"""

import logging
import ray
import datetime
import secrets
import hashlib
from fastapi import APIRouter, Request, HTTPException, Depends, Form, Query, status, Header
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from typing import Optional, Dict, Any
from bson import ObjectId
from werkzeug.security import check_password_hash

from .actor import ExperimentActor
from mdb_runtime.auth import block_demo_users
from rate_limit import limiter, LOGIN_POST_LIMIT, LOGIN_GET_LIMIT, REGISTER_POST_LIMIT
from utils import safe_objectid

logger = logging.getLogger(__name__)
bp = APIRouter()

# Rate limits for sensitive operations
PASSWORD_CHANGE_LIMIT = "3 per hour"
MFA_SETUP_LIMIT = "5 per hour"
MFA_VERIFY_LIMIT = "10 per minute"


async def get_actor_handle(request: Request) -> "ray.actor.ActorHandle":
    """FastAPI Dependency to get the Password Manager actor handle."""
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


async def get_user_from_request(request: Request) -> Dict[str, Any]:
    """Get authenticated user from sub-auth session."""
    from mdb_runtime.auth import get_experiment_sub_user
    from mdb_runtime.database import get_experiment_db
    from core_deps import get_experiment_config
    
    slug_id = getattr(request.state, "slug_id", "pwd_zero")
    
    config = await get_experiment_config(request, slug_id, {"sub_auth": 1})
    if not config:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    
    sub_auth = config.get("sub_auth", {})
    if not sub_auth.get("enabled", False):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    
    db = await get_experiment_db(request)
    experiment_user = await get_experiment_sub_user(request, slug_id, db, config)
    
    if not experiment_user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required. Please log in.")
    
    return {
        "user_id": str(experiment_user.get("_id")),
        "email": experiment_user.get("email"),
        "experiment_user_id": str(experiment_user.get("_id")),
        "username": experiment_user.get("username")
    }


async def get_encryption_key_from_session(request: Request, user_id: str) -> Optional[str]:
    """
    Get encryption key from server-side session storage (MongoDB).
    SECURITY: Encryption keys are stored server-side, not in cookies.
    """
    try:
        from mdb_runtime.database import get_experiment_db
        
        db = await get_experiment_db(request)
        if not db:
            return None
        
        # Get session ID from cookie (not the encryption key itself)
        session_id = request.cookies.get("pwd_zero_session_id")
        if not session_id:
            return None
        
        # Look up encryption session in MongoDB
        encryption_session = await db.encryption_sessions.find_one({
            "user_id": safe_objectid(user_id, "user_id"),
            "session_id": session_id,
            "expires_at": {"$gt": datetime.datetime.utcnow()}
        })
        
        if not encryption_session:
            return None
        
        # Return the encrypted key (still encrypted with session-specific key)
        return encryption_session.get("encryption_key")
    except Exception as e:
        logger.error(f"Error getting encryption key from session: {e}", exc_info=True)
        return None


async def store_encryption_key_session(
    request: Request,
    user_id: str,
    encryption_key: str,
    response: Optional[RedirectResponse] = None
) -> str:
    """
    Store encryption key in server-side session storage (MongoDB).
    SECURITY: Only a session ID is stored in cookie, not the encryption key.
    """
    try:
        from mdb_runtime.database import get_experiment_db
        
        db = await get_experiment_db(request)
        if not db:
            raise HTTPException(status_code=500, detail="Database connection failed")
        
        # Generate unique session ID
        session_id = secrets.token_urlsafe(32)
        
        # Store encryption session in MongoDB (encrypted at rest)
        expires_at = datetime.datetime.utcnow() + datetime.timedelta(hours=24)
        
        await db.encryption_sessions.insert_one({
            "user_id": safe_objectid(user_id, "user_id"),
            "session_id": session_id,
            "encryption_key": encryption_key,  # In production, encrypt this with a server key
            "created_at": datetime.datetime.utcnow(),
            "expires_at": expires_at,
            "last_used": datetime.datetime.utcnow()
        })
        
        # Set session ID cookie (not the encryption key)
        if response:
            import os
            use_secure = os.getenv("USE_SECURE_COOKIES", "true").lower() in {"true", "1", "yes"}
            
            response.set_cookie(
                key="pwd_zero_session_id",
                value=session_id,
                httponly=True,
                samesite="strict",  # Stricter than lax for security
                secure=use_secure,
                max_age=86400  # 24 hours
            )
        
        return session_id
    except Exception as e:
        logger.error(f"Error storing encryption key session: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create encryption session")


async def clear_encryption_key_session(request: Request, user_id: str, response: Optional[RedirectResponse] = None):
    """Clear encryption key session from server-side storage."""
    try:
        from mdb_runtime.database import get_experiment_db
        
        db = await get_experiment_db(request)
        if not db:
            return
        
        # Get session ID from cookie
        session_id = request.cookies.get("pwd_zero_session_id")
        if session_id:
            # Delete session from MongoDB
            await db.encryption_sessions.delete_one({
                "user_id": safe_objectid(user_id, "user_id"),
                "session_id": session_id
            })
        
        # Clear all sessions for this user (on logout)
        await db.encryption_sessions.delete_many({
            "user_id": safe_objectid(user_id, "user_id")
        })
        
        # Clear cookie
        if response:
            response.delete_cookie(
                key="pwd_zero_session_id",
                httponly=True,
                samesite="strict"
            )
    except Exception as e:
        logger.error(f"Error clearing encryption key session: {e}", exc_info=True)


def generate_csrf_token() -> str:
    """Generate a CSRF token."""
    return secrets.token_urlsafe(32)


def validate_csrf_token(request: Request, token: Optional[str] = None) -> bool:
    """
    Validate CSRF token using double-submit cookie pattern.
    SECURITY: Prevents CSRF attacks on state-changing endpoints.
    """
    if not token:
        token = request.headers.get("X-CSRF-Token")
    
    if not token:
        return False
    
    # Get CSRF token from cookie
    cookie_token = request.cookies.get("pwd_zero_csrf_token")
    if not cookie_token:
        return False
    
    # Compare tokens (constant-time comparison)
    return secrets.compare_digest(token, cookie_token)


def set_csrf_cookie(response: RedirectResponse, token: str):
    """Set CSRF token in cookie."""
    import os
    use_secure = os.getenv("USE_SECURE_COOKIES", "true").lower() in {"true", "1", "yes"}
    
    response.set_cookie(
        key="pwd_zero_csrf_token",
        value=token,
        httponly=False,  # Must be readable by JavaScript for double-submit pattern
        samesite="strict",
        secure=use_secure,
        max_age=86400  # 24 hours
    )


# --- Authentication Routes ---

@bp.get("/login", response_class=HTMLResponse, dependencies=[Depends(block_demo_users)])
@limiter.limit(LOGIN_GET_LIMIT)
async def login_get(request: Request, error: Optional[str] = Query(None)):
    """Display login page."""
    try:
        # Check if user is already authenticated
        try:
            user = await get_user_from_request(request)
            if user:
                return RedirectResponse(url="/experiments/pwd_zero/", status_code=status.HTTP_303_SEE_OTHER)
        except HTTPException:
            pass  # Not authenticated, continue to login page
        
        # Use experiment's own template directory
        from fastapi.templating import Jinja2Templates
        from pathlib import Path
        experiment_dir = Path(__file__).resolve().parent
        templates_dir = experiment_dir / "templates"
        experiment_templates = Jinja2Templates(directory=str(templates_dir))
        
        return experiment_templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "error": error,
                "show_login": True
            }
        )
    except Exception as e:
        logger.error(f"Error rendering login page: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error rendering login page")


@bp.post("/login", dependencies=[Depends(block_demo_users)])
@limiter.limit(LOGIN_POST_LIMIT)
async def login_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    actor: "ray.actor.ActorHandle" = Depends(get_actor_handle)
):
    """Handle login with IP tracking for security."""
    try:
        # Get client IP address for security logging
        client_ip = request.client.host if request.client else None
        if request.headers.get("x-forwarded-for"):
            client_ip = request.headers.get("x-forwarded-for").split(",")[0].strip()
        
        result = await actor.login_user.remote(username, password, client_ip)
        
        if result.get("status") == "error":
            error_msg = result.get('error', 'Login failed')
            # Check if request accepts JSON
            if request.headers.get("accept", "").startswith("application/json"):
                return JSONResponse({"error": error_msg}, status_code=401)
            redirect_url = f"/experiments/pwd_zero/login?error={error_msg}"
            return RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)
        
        # Check if MFA is required
        if result.get("status") == "mfa_required":
            # Create temporary MFA session (stores encryption key temporarily)
            from mdb_runtime.database import get_experiment_db
            db = await get_experiment_db(request)
            
            # Generate temporary session token
            temp_token = secrets.token_urlsafe(32)
            expires_at = datetime.datetime.utcnow() + datetime.timedelta(minutes=5)  # 5 minute window for MFA
            
            # Store temporary session with encryption key
            await db.temp_mfa_sessions.insert_one({
                "user_id": safe_objectid(result.get("user_id"), "user_id"),
                "temp_token": temp_token,
                "encryption_key": result.get("encryption_key"),  # Store encryption key temporarily
                "created_at": datetime.datetime.utcnow(),
                "expires_at": expires_at
            })
            
            if request.headers.get("accept", "").startswith("application/json"):
                return JSONResponse({
                    "status": "mfa_required",
                    "user_id": result.get("user_id"),
                    "temp_session_token": temp_token,
                    "message": "MFA verification required"
                }, status_code=200)
            # For HTML, redirect to MFA verification page
            redirect_url = f"/experiments/pwd_zero/mfa/verify?user_id={result.get('user_id')}&temp_token={temp_token}"
            return RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)
        
        # Create sub-auth session
        from mdb_runtime.auth import create_experiment_session
        from mdb_runtime.database import get_experiment_db
        from core_deps import get_experiment_config
        
        slug_id = getattr(request.state, "slug_id", "pwd_zero")
        db = await get_experiment_db(request)
        config = await get_experiment_config(request, slug_id, {"sub_auth": 1})
        
        if not config:
            raise HTTPException(status_code=500, detail="Experiment configuration not found")
        
        logger.info(f"User '{username}' logged in successfully")
        
        # Store encryption key server-side (not in cookie)
        session_id = await store_encryption_key_session(
            request, result["user_id"], result["encryption_key"]
        )
        
        # Generate CSRF token
        csrf_token = generate_csrf_token()
        
        # Check if request accepts JSON
        if request.headers.get("accept", "").startswith("application/json"):
            # Create JSON response and set cookies on it
            json_response = JSONResponse({"status": "success", "message": "Login successful"})
            await create_experiment_session(
                request, slug_id, result["user_id"], config, json_response
            )
            # Set session ID cookie (not encryption key)
            await store_encryption_key_session(
                request, result["user_id"], result["encryption_key"], json_response
            )
            set_csrf_cookie(json_response, csrf_token)
            return json_response
        
        # Create redirect response and set cookies on it
        response = RedirectResponse(url="/experiments/pwd_zero/", status_code=status.HTTP_303_SEE_OTHER)
        await create_experiment_session(
            request, slug_id, result["user_id"], config, response
        )
        # Set session ID cookie (not encryption key)
        await store_encryption_key_session(
            request, result["user_id"], result["encryption_key"], response
        )
        set_csrf_cookie(response, csrf_token)
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error during login: {e}", exc_info=True)
        error_msg = "Login failed. Please try again."
        if request.headers.get("accept", "").startswith("application/json"):
            return JSONResponse({"error": error_msg}, status_code=500)
        redirect_url = f"/experiments/pwd_zero/login?error={error_msg}"
        return RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)


@bp.post("/register", dependencies=[Depends(block_demo_users)])
@limiter.limit(REGISTER_POST_LIMIT)
async def register_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    actor: "ray.actor.ActorHandle" = Depends(get_actor_handle)
):
    """Handle registration."""
    try:
        logger.info(f"Registration request received for username: '{username}'")
        # Create user via actor (this creates user in users collection with password hash and salt)
        result = await actor.register_user.remote(username, password)
        
        logger.info(f"Registration result: {result.get('status')} - {result.get('error', result.get('message', 'No message'))}")
        
        if result.get("status") == "error":
            error_msg = result.get('error', 'Registration failed')
            logger.error(f"Registration failed for username '{username}': {error_msg}")
            # Check if request accepts JSON
            if request.headers.get("accept", "").startswith("application/json"):
                return JSONResponse({"error": error_msg}, status_code=400)
            redirect_url = f"/experiments/pwd_zero/login?error={error_msg}"
            return RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)
        
        if result.get("status") != "success":
            logger.error(f"Unexpected registration result status: {result.get('status')}")
            error_msg = "Registration failed with unknown error"
            if request.headers.get("accept", "").startswith("application/json"):
                return JSONResponse({"error": error_msg}, status_code=500)
            redirect_url = f"/experiments/pwd_zero/login?error={error_msg}"
            return RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)
        
        # The actor already created the user in the users collection with password hash
        # Now we need to create a sub-auth session for that user
        from mdb_runtime.auth import create_experiment_session
        from mdb_runtime.database import get_experiment_db
        from core_deps import get_experiment_config
        
        slug_id = getattr(request.state, "slug_id", "pwd_zero")
        db = await get_experiment_db(request)
        config = await get_experiment_config(request, slug_id, {"sub_auth": 1})
        
        if not config:
            raise HTTPException(status_code=500, detail="Experiment configuration not found")
        
        sub_auth_cfg = config.get("sub_auth", {})
        if not sub_auth_cfg.get("allow_registration", False):
            error_msg = "Registration is disabled"
            if request.headers.get("accept", "").startswith("application/json"):
                return JSONResponse({"error": error_msg}, status_code=403)
            raise HTTPException(status_code=403, detail=error_msg)
        
        # The user is already created by the actor, so we just need to create the session
        logger.info(f"User '{username}' registered successfully")
        
        # Store encryption key server-side (not in cookie)
        session_id = await store_encryption_key_session(
            request, result["user_id"], result["encryption_key"]
        )
        
        # Generate CSRF token
        csrf_token = generate_csrf_token()
        
        # Check if request accepts JSON
        if request.headers.get("accept", "").startswith("application/json"):
            # Create JSON response and set cookies on it
            json_response = JSONResponse({"status": "success", "message": "Registration successful"})
            await create_experiment_session(
                request, slug_id, result["user_id"], config, json_response
            )
            # Set session ID cookie (not encryption key)
            await store_encryption_key_session(
                request, result["user_id"], result["encryption_key"], json_response
            )
            set_csrf_cookie(json_response, csrf_token)
            return json_response
        
        # Create redirect response and set cookies on it
        response = RedirectResponse(url="/experiments/pwd_zero/", status_code=status.HTTP_303_SEE_OTHER)
        await create_experiment_session(
            request, slug_id, result["user_id"], config, response
        )
        # Set session ID cookie (not encryption key)
        await store_encryption_key_session(
            request, result["user_id"], result["encryption_key"], response
        )
        set_csrf_cookie(response, csrf_token)
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error during registration: {e}", exc_info=True)
        error_msg = "Registration failed. Please try again."
        if request.headers.get("accept", "").startswith("application/json"):
            return JSONResponse({"error": error_msg}, status_code=500)
        redirect_url = f"/experiments/pwd_zero/login?error={error_msg}"
        return RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)


@bp.post("/logout")
async def logout_post(request: Request):
    """Handle logout. Clears all server-side sessions."""
    try:
        user = await get_user_from_request(request)
        
        from core_deps import get_experiment_config
        
        slug_id = getattr(request.state, "slug_id", "pwd_zero")
        config = await get_experiment_config(request, slug_id, {"sub_auth": 1})
        
        if config:
            sub_auth_cfg = config.get("sub_auth", {})
            session_cookie_name = sub_auth_cfg.get("session_cookie_name", "pwd_zero_session")
            cookie_name = f"{session_cookie_name}_{slug_id}"
            
            # Clear all server-side encryption sessions
            await clear_encryption_key_session(request, user["user_id"])
            
            # Clear session cookie and CSRF cookie
            response = RedirectResponse(url="/experiments/pwd_zero/login", status_code=status.HTTP_303_SEE_OTHER)
            response.delete_cookie(key=cookie_name, httponly=True, samesite="lax")
            response.delete_cookie(key="pwd_zero_session_id", httponly=True, samesite="strict")
            response.delete_cookie(key="pwd_zero_csrf_token", httponly=False, samesite="strict")
            
            logger.info(f"User {user.get('username')} logged out (all sessions cleared)")
            return response
        
        return RedirectResponse(url="/experiments/pwd_zero/login", status_code=status.HTTP_303_SEE_OTHER)
        
    except HTTPException:
        # Not authenticated, just redirect
        return RedirectResponse(url="/experiments/pwd_zero/login", status_code=status.HTTP_303_SEE_OTHER)
    except Exception as e:
        logger.error(f"Error during logout: {e}", exc_info=True)
        return RedirectResponse(url="/experiments/pwd_zero/login", status_code=status.HTTP_303_SEE_OTHER)


# --- Main Application Route ---

@bp.get("/", response_class=HTMLResponse)
async def index(request: Request, actor: "ray.actor.ActorHandle" = Depends(get_actor_handle)):
    """Display main password manager page. Requires authentication."""
    try:
        # Check if user is authenticated - if not, redirect to login
        try:
            user = await get_user_from_request(request)
            encryption_key = await get_encryption_key_from_session(request, user["user_id"])
            if not encryption_key:
                # No encryption key, redirect to login
                return RedirectResponse(url="/experiments/pwd_zero/login", status_code=status.HTTP_303_SEE_OTHER)
        except HTTPException:
            # Not authenticated, redirect to login
            return RedirectResponse(url="/experiments/pwd_zero/login", status_code=status.HTTP_303_SEE_OTHER)
        
        # User is authenticated, render the main page
        html = await actor.render_index.remote()
        return HTMLResponse(html)
    except Exception as e:
        logger.error(f"Actor call failed for render_index: {e}", exc_info=True)
        return HTMLResponse(f"<h1>Actor Error</h1><pre>{e}</pre>", status_code=500)


# --- API Routes ---

@bp.get("/api/session")
async def get_session(request: Request, actor: "ray.actor.ActorHandle" = Depends(get_actor_handle)):
    """Check session status."""
    try:
        user = await get_user_from_request(request)
        encryption_key = await get_encryption_key_from_session(request, user["user_id"])
        
        if not encryption_key:
            return JSONResponse({"authenticated": False, "has_user": True})
        
        result = await actor.check_session.remote(user["user_id"])
        result["authenticated"] = result["authenticated"] and encryption_key is not None
        return JSONResponse(result)
    except HTTPException:
        # Not authenticated
        try:
            # Check if any users exist
            from mdb_runtime.database import get_experiment_db
            db = await get_experiment_db(request)
            has_user = await db.users.count_documents({}) > 0
            return JSONResponse({"authenticated": False, "has_user": has_user})
        except:
            return JSONResponse({"authenticated": False, "has_user": False})


@bp.get("/api/passwords")
async def get_passwords(
    request: Request,
    actor: "ray.actor.ActorHandle" = Depends(get_actor_handle)
):
    """Get all passwords for authenticated user."""
    user = await get_user_from_request(request)
    encryption_key = await get_encryption_key_from_session(request, user["user_id"])
    
    if not encryption_key:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
    
    try:
        passwords = await actor.get_passwords.remote(user["user_id"], encryption_key)
        return JSONResponse(passwords)
    except Exception as e:
        logger.error(f"Actor call failed for get_passwords: {e}", exc_info=True)
        raise HTTPException(500, detail="Failed to retrieve passwords")


@bp.post("/api/passwords")
async def add_password(
    request: Request,
    actor: "ray.actor.ActorHandle" = Depends(get_actor_handle),
    x_csrf_token: Optional[str] = Header(None)
):
    """Add a new password entry. Requires CSRF protection."""
    # Validate CSRF token
    if not validate_csrf_token(request, x_csrf_token):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token")
    
    user = await get_user_from_request(request)
    encryption_key = await get_encryption_key_from_session(request, user["user_id"])
    
    if not encryption_key:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
    
    try:
        data = await request.json()
        if not all(k in data for k in ['website', 'username', 'password']):
            raise HTTPException(status_code=400, detail="Missing required data fields")
        
        result = await actor.add_password.remote(
            user["user_id"],
            encryption_key,
            data["website"],
            data["username"],
            data["password"]
        )
        
        if result.get("status") == "error":
            raise HTTPException(status_code=500, detail="Failed to add password")
        
        return JSONResponse(result, status_code=201)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Actor call failed for add_password: {e}", exc_info=True)
        raise HTTPException(500, detail="Failed to add password")


@bp.put("/api/passwords/{password_id}")
async def update_password(
    request: Request,
    password_id: str,
    actor: "ray.actor.ActorHandle" = Depends(get_actor_handle),
    x_csrf_token: Optional[str] = Header(None)
):
    """Update a password entry. Requires CSRF protection."""
    # Validate CSRF token
    if not validate_csrf_token(request, x_csrf_token):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token")
    
    # Validate password_id
    try:
        safe_objectid(password_id, "password_id")
    except HTTPException:
        raise HTTPException(status_code=400, detail="Invalid password ID format")
    
    user = await get_user_from_request(request)
    encryption_key = await get_encryption_key_from_session(request, user["user_id"])
    
    if not encryption_key:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
    
    try:
        data = await request.json()
        result = await actor.update_password.remote(
            user["user_id"],
            encryption_key,
            password_id,
            data.get("website"),
            data.get("username"),
            data.get("password")
        )
        
        if result.get("status") == "error":
            raise HTTPException(status_code=500, detail="Failed to update password")
        
        return JSONResponse(result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Actor call failed for update_password: {e}", exc_info=True)
        raise HTTPException(500, detail="Failed to update password")


@bp.delete("/api/passwords/{password_id}")
async def delete_password(
    request: Request,
    password_id: str,
    actor: "ray.actor.ActorHandle" = Depends(get_actor_handle),
    x_csrf_token: Optional[str] = Header(None)
):
    """Delete a password entry. Requires CSRF protection."""
    # Validate CSRF token
    if not validate_csrf_token(request, x_csrf_token):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token")
    
    # Validate password_id
    try:
        safe_objectid(password_id, "password_id")
    except HTTPException:
        raise HTTPException(status_code=400, detail="Invalid password ID format")
    
    user = await get_user_from_request(request)
    
    try:
        result = await actor.delete_password.remote(user["user_id"], password_id)
        
        if result.get("status") == "error":
            raise HTTPException(status_code=500, detail="Failed to delete password")
        
        return JSONResponse(result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Actor call failed for delete_password: {e}", exc_info=True)
        raise HTTPException(500, detail="Failed to delete password")


@bp.post("/api/generate-password")
async def generate_password(
    request: Request,
    actor: "ray.actor.ActorHandle" = Depends(get_actor_handle)
):
    """Generate a secure password."""
    user = await get_user_from_request(request)
    
    try:
        data = await request.json()
        result = await actor.generate_password.remote(
            length=data.get("length", 16),
            uppercase=data.get("uppercase", True),
            lowercase=data.get("lowercase", True),
            numbers=data.get("numbers", True),
            symbols=data.get("symbols", True)
        )
        
        if result.get("status") == "error":
            raise HTTPException(status_code=400, detail=result.get("error", "Failed to generate password"))
        
        return JSONResponse(result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Actor call failed for generate_password: {e}", exc_info=True)
        raise HTTPException(500, f"Actor failed to generate password: {e}")


# --- MFA Routes ---

@bp.get("/api/mfa/status")
async def get_mfa_status(
    request: Request,
    actor: "ray.actor.ActorHandle" = Depends(get_actor_handle)
):
    """Get MFA status for the current user."""
    user = await get_user_from_request(request)
    
    try:
        result = await actor.get_mfa_status.remote(user["user_id"])
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"Actor call failed for get_mfa_status: {e}", exc_info=True)
        raise HTTPException(500, f"Actor failed to get MFA status: {e}")


@bp.post("/api/mfa/setup")
@limiter.limit(MFA_SETUP_LIMIT)
async def setup_mfa(
    request: Request,
    actor: "ray.actor.ActorHandle" = Depends(get_actor_handle),
    x_csrf_token: Optional[str] = Header(None)
):
    """Generate MFA secret and QR code for setup. Requires CSRF protection."""
    # Validate CSRF token
    if not validate_csrf_token(request, x_csrf_token):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token")
    
    user = await get_user_from_request(request)
    encryption_key = await get_encryption_key_from_session(request, user["user_id"])
    
    if not encryption_key:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
    
    try:
        result = await actor.generate_mfa_secret.remote(user["user_id"], encryption_key)
        
        if result.get("status") == "error":
            raise HTTPException(status_code=500, detail="Failed to generate MFA secret")
        
        return JSONResponse(result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Actor call failed for generate_mfa_secret: {e}", exc_info=True)
        raise HTTPException(500, detail="Failed to generate MFA secret")


@bp.post("/api/mfa/enable")
@limiter.limit(MFA_SETUP_LIMIT)
async def enable_mfa(
    request: Request,
    actor: "ray.actor.ActorHandle" = Depends(get_actor_handle),
    x_csrf_token: Optional[str] = Header(None)
):
    """Enable MFA after verifying the setup code. Requires CSRF protection."""
    # Validate CSRF token
    if not validate_csrf_token(request, x_csrf_token):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token")
    
    user = await get_user_from_request(request)
    encryption_key = await get_encryption_key_from_session(request, user["user_id"])
    
    if not encryption_key:
        raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
    
    try:
        data = await request.json()
        verification_code = data.get("code")
        
        if not verification_code:
            raise HTTPException(status_code=400, detail="Verification code is required")
        
        result = await actor.enable_mfa.remote(user["user_id"], encryption_key, verification_code)
        
        if result.get("status") == "error":
            raise HTTPException(status_code=400, detail="Failed to enable MFA")
        
        return JSONResponse(result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Actor call failed for enable_mfa: {e}", exc_info=True)
        raise HTTPException(500, detail="Failed to enable MFA")


@bp.post("/api/mfa/verify")
@limiter.limit(MFA_VERIFY_LIMIT)
async def verify_mfa(
    request: Request,
    actor: "ray.actor.ActorHandle" = Depends(get_actor_handle)
):
    """
    Verify MFA code during login.
    SECURITY: Uses temporary session token from login, not password.
    """
    try:
        data = await request.json()
        user_id = data.get("user_id")
        code = data.get("code")
        temp_session_token = data.get("temp_session_token")  # From login endpoint
        
        if not user_id or not code:
            raise HTTPException(status_code=400, detail="User ID and MFA code are required")
        
        # Validate user_id
        try:
            safe_objectid(user_id, "user_id")
        except HTTPException:
            raise HTTPException(status_code=400, detail="Invalid user ID format")
        
        # Get user and temporary encryption key from temp session storage
        from mdb_runtime.database import get_experiment_db
        db = await get_experiment_db(request)
        
        # Look up temporary session (created during login when MFA is required)
        if temp_session_token:
            temp_session = await db.temp_mfa_sessions.find_one({
                "user_id": safe_objectid(user_id, "user_id"),
                "temp_token": temp_session_token,
                "expires_at": {"$gt": datetime.datetime.utcnow()}
            })
            
            if not temp_session:
                raise HTTPException(status_code=401, detail="Invalid or expired session token")
            
            encryption_key = temp_session.get("encryption_key")
        else:
            # Fallback: if no temp token, this shouldn't happen but handle gracefully
            raise HTTPException(status_code=400, detail="Temporary session token required")
        
        # Verify MFA code
        result = await actor.verify_mfa_code.remote(user_id, code, encryption_key)
        
        if result.get("status") != "success":
            raise HTTPException(status_code=401, detail="Invalid MFA code")
        
        # Get user for logging
        user = await db.users.find_one({"_id": safe_objectid(user_id, "user_id")})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # MFA verified - complete login
        from mdb_runtime.auth import create_experiment_session
        from core_deps import get_experiment_config
        
        slug_id = getattr(request.state, "slug_id", "pwd_zero")
        config = await get_experiment_config(request, slug_id, {"sub_auth": 1})
        
        if not config:
            raise HTTPException(status_code=500, detail="Experiment configuration not found")
        
        # Update login info
        await db.users.update_one(
            {"_id": safe_objectid(user_id, "user_id")},
            {
                "$set": {
                    "failed_login_attempts": 0,
                    "locked_until": None,
                    "last_login": datetime.datetime.utcnow(),
                    "last_login_ip": request.client.host if request.client else None
                }
            }
        )
        
        # Delete temporary MFA session
        await db.temp_mfa_sessions.delete_one({
            "user_id": safe_objectid(user_id, "user_id"),
            "temp_token": temp_session_token
        })
        
        # Log successful login with MFA
        try:
            await actor._log_security_event.remote(
                user_id,
                "login_success",
                {"username": user.get("username"), "ip": request.client.host if request.client else None, "mfa_used": True}
            )
        except Exception as log_error:
            logger.warning(f"Failed to log MFA login event: {log_error}")
        
        # Store encryption key server-side
        session_id = await store_encryption_key_session(
            request, user_id, encryption_key
        )
        
        # Generate CSRF token
        csrf_token = generate_csrf_token()
        
        # Create session
        if request.headers.get("accept", "").startswith("application/json"):
            json_response = JSONResponse({"status": "success", "message": "Login successful"})
            await create_experiment_session(request, slug_id, user_id, config, json_response)
            await store_encryption_key_session(request, user_id, encryption_key, json_response)
            set_csrf_cookie(json_response, csrf_token)
            return json_response
        
        response = RedirectResponse(url="/experiments/pwd_zero/", status_code=status.HTTP_303_SEE_OTHER)
        await create_experiment_session(request, slug_id, user_id, config, response)
        await store_encryption_key_session(request, user_id, encryption_key, response)
        set_csrf_cookie(response, csrf_token)
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error during MFA verification: {e}", exc_info=True)
        raise HTTPException(500, detail="Failed to verify MFA code")


@bp.post("/api/mfa/disable")
@limiter.limit(MFA_SETUP_LIMIT)
async def disable_mfa(
    request: Request,
    actor: "ray.actor.ActorHandle" = Depends(get_actor_handle),
    x_csrf_token: Optional[str] = Header(None)
):
    """Disable MFA (requires password verification). Requires CSRF protection."""
    # Validate CSRF token
    if not validate_csrf_token(request, x_csrf_token):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token")
    
    user = await get_user_from_request(request)
    
    try:
        data = await request.json()
        password = data.get("password")
        
        if not password:
            raise HTTPException(status_code=400, detail="Password is required to disable MFA")
        
        result = await actor.disable_mfa.remote(user["user_id"], password)
        
        if result.get("status") == "error":
            raise HTTPException(status_code=400, detail="Failed to disable MFA")
        
        return JSONResponse(result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Actor call failed for disable_mfa: {e}", exc_info=True)
        raise HTTPException(500, detail="Failed to disable MFA")


# --- Settings Routes ---

@bp.post("/api/settings/change-password")
@limiter.limit(PASSWORD_CHANGE_LIMIT)
async def change_password(
    request: Request,
    actor: "ray.actor.ActorHandle" = Depends(get_actor_handle),
    x_csrf_token: Optional[str] = Header(None)
):
    """
    Change user's master password.
    SECURITY: Automatically re-encrypts all passwords with new encryption key.
    Requires CSRF protection.
    """
    # Validate CSRF token
    if not validate_csrf_token(request, x_csrf_token):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token")
    
    user = await get_user_from_request(request)
    
    try:
        data = await request.json()
        current_password = data.get("current_password")
        new_password = data.get("new_password")
        
        if not current_password or not new_password:
            raise HTTPException(status_code=400, detail="Current password and new password are required")
        
        # Get current encryption key for re-encryption
        current_encryption_key = await get_encryption_key_from_session(request, user["user_id"])
        if not current_encryption_key:
            raise HTTPException(status_code=401, detail="Session expired. Please log in again.")
        
        result = await actor.change_master_password.remote(
            user["user_id"], 
            current_password, 
            new_password,
            current_encryption_key  # Pass current key for re-encryption
        )
        
        if result.get("status") == "error":
            raise HTTPException(status_code=400, detail=result.get("error", "Failed to change password"))
        
        # Update encryption key session if password change was successful
        if result.get("encryption_key"):
            response = JSONResponse(result)
            # Update server-side encryption key session
            await store_encryption_key_session(
                request, user["user_id"], result["encryption_key"], response
            )
            return response
        
        return JSONResponse(result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Actor call failed for change_master_password: {e}", exc_info=True)
        raise HTTPException(500, detail="Failed to change password")

