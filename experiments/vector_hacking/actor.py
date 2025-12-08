import logging
import time
import asyncio
import numpy as np
import ray
import pathlib
import os
from typing import Dict, Any, List, Optional

# Configure logging
logger = logging.getLogger(__name__)

TARGET = "Be mindful"
MATCH_ERROR = 0.4
COST_LIMIT = 60.0
# Using a widely supported model. voyage-2 is 1024 dim.
VOYAGE_MODEL = "voyage-2" 

# Actor-local paths
experiment_dir = pathlib.Path(__file__).parent
templates_dir = experiment_dir / "templates"

@ray.remote
class SharedState:
    def __init__(self):
        self.CURRENT_BEST_TEXT = "Be"
        self.CURRENT_BEST_ERROR = np.inf
        self.GUESSES_MADE = 0
        self.TOTAL_COST = 0.0
        self.MATCH_FOUND = False
        self.PREVIOUS_GUESSES = set()
        self.LAST_ERROR = None # Store last error for debugging

    def increment_attempts(self, count=1):
        self.GUESSES_MADE += count

    def update_best_guess(self, text, error, cost=0.0):
        self.TOTAL_COST += cost
        self.PREVIOUS_GUESSES.add(text.lower())
        
        if error < self.CURRENT_BEST_ERROR:
            self.CURRENT_BEST_TEXT = text
            self.CURRENT_BEST_ERROR = error
            
        if error <= MATCH_ERROR:
            self.MATCH_FOUND = True

    def report_error(self, error_msg):
        self.LAST_ERROR = error_msg

    def get_state(self):
        return {
            'CURRENT_BEST_TEXT': self.CURRENT_BEST_TEXT,
            'CURRENT_BEST_ERROR': self.CURRENT_BEST_ERROR,
            'GUESSES_MADE': self.GUESSES_MADE,
            'TOTAL_COST': self.TOTAL_COST,
            'PREVIOUS_GUESSES': self.PREVIOUS_GUESSES,
            'MATCH_FOUND': self.MATCH_FOUND,
            'LAST_ERROR': self.LAST_ERROR
        }

@ray.remote
def generate_and_evaluate_guess(v_target, shared_state_actor, openai_key, voyage_key):
    # Initialize clients inside the worker with passed keys
    if not openai_key or not voyage_key:
        ray.get(shared_state_actor.report_error.remote("Missing API Keys in worker"))
        return

    try:
        import httpx
        import voyageai
        
        vo = voyageai.Client(api_key=voyage_key)
        
    except ImportError as e:
        ray.get(shared_state_actor.report_error.remote(f"ImportError: {e}"))
        return

    prompt_template = f"""User input is last iterative guess of an unknown text string and its vector ERROR from the unknown text.
Determine better text strings having lower vector ERRORs and write one such string in English as your entire output.
The goal is to accurately guess the mystery text.
This is a game of guess-and-check.

[clue]
TWO WORDS; CLUE: FIRST WORD IS `Be`; SECOND WORD YOU HAVE TO GUESS.
[/clue]

[IMPORTANT]
- Do NOT repeat any of the previous guesses provided in [context].
- Do NOT include your thought process in your response.
- Your response should be coherent and exactly two words.
- Output ONLY the guess.
[/IMPORTANT]
"""
    
    try:
        # Get state
        state = ray.get(shared_state_actor.get_state.remote())
        
        # Stop if match found already
        if state['MATCH_FOUND']:
            return

        # Register attempt immediately
        shared_state_actor.increment_attempts.remote()

        # Format error safely (handle np.inf)
        error_val = state['CURRENT_BEST_ERROR']
        if error_val == np.inf or error_val is None:
            error_str = "∞"
        else:
            error_str = f"{error_val:.4f}"

        assist = f"""\nBEST_GUESS: {state['CURRENT_BEST_TEXT']} (ERROR {error_str})"""
        previous_guesses = state['PREVIOUS_GUESSES']
        
        # Safeguard for prompt size
        prev_guesses_list = list(previous_guesses)
        if len(prev_guesses_list) > 50:
             prev_guesses_list = prev_guesses_list[-50:]

        if prev_guesses_list:
            previous_guesses_str = ', '.join(f'"{guess}"' for guess in prev_guesses_list)
            assist += f"\nPrevious guesses: {previous_guesses_str}"
        else:
            assist += "\nNo previous guesses."

        m = f"ERROR {error_str}, \"{state['CURRENT_BEST_TEXT']}\""

        # Call OpenAI using httpx (similar to data_imaging pattern)
        try:
            headers = {
                "Authorization": f"Bearer {openai_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": "gpt-3.5-turbo",
                "messages": [
                    {"role": "system", "content": prompt_template},
                    {"role": "user", "content": f"[context]{assist}[/context] \n\n [user input]{m}[/user input]"}
                ],
                "temperature": 0.7,
                "max_tokens": 10
            }
            
            with httpx.Client(timeout=30) as client:
                resp = client.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                TEXT = data["choices"][0]["message"]["content"].strip()
        except httpx.HTTPStatusError as e:
            ray.get(shared_state_actor.report_error.remote(f"OpenAI API error: {e.response.status_code} - {e.response.text}"))
            return
        except Exception as e:
            ray.get(shared_state_actor.report_error.remote(f"OpenAI Call Failed: {e}"))
            return
        
        TEXT = TEXT.replace('"', '').replace("'", "")
        
        if TEXT.lower() in previous_guesses:
            return

        # Embed with Voyage
        try:
            res = vo.embed([TEXT], model=VOYAGE_MODEL, input_type="document")
            v_text = np.array(res.embeddings[0])
        except Exception as e:
            ray.get(shared_state_actor.report_error.remote(f"Voyage Embed Failed: {e}"))
            return
        
        # Dimension check
        if v_text.shape != v_target.shape:
             ray.get(shared_state_actor.report_error.remote(f"Dim mismatch: Target {v_target.shape}, Guess {v_text.shape}"))
             return

        # Calculate Error
        dv = v_target - v_text
        VECTOR_ERROR = np.sqrt((dv * dv).sum())
        
        # Estimate cost
        cost = 0.0002
        
        ray.get(shared_state_actor.update_best_guess.remote(TEXT, VECTOR_ERROR, cost))

    except Exception as e:
        ray.get(shared_state_actor.report_error.remote(f"Worker Error: {e}"))

@ray.remote
class ExperimentActor:
    def __init__(self, mongo_uri: str, db_name: str, write_scope: str, read_scopes: List[str]):
        self.mongo_uri = mongo_uri
        self.db_name = db_name
        self.write_scope = write_scope
        self.running = False
        self.task = None
        self.shared_state = None
        self.v_target = None
        self.voyage_client = None
        self.openai_key = os.getenv("OPENAI_API_KEY")
        self.voyage_key = os.getenv("VOYAGE_API_KEY")
        
        # Lazy-load httpx (similar to data_imaging pattern)
        try:
            import httpx
            self.httpx = httpx
            logger.info(f"[{write_scope}-Actor] Successfully loaded httpx.")
        except ImportError as e:
            logger.warning(f"[{write_scope}-Actor] httpx not available: {e}")
            self.httpx = None
        
        # Load templates
        try:
            from fastapi.templating import Jinja2Templates
            if templates_dir.is_dir():
                self.templates = Jinja2Templates(directory=str(templates_dir))
            else:
                self.templates = None
        except ImportError:
            self.templates = None

        # Init Voyage for Target
        if self.voyage_key:
            try:
                import voyageai
                self.voyage_client = voyageai.Client(api_key=self.voyage_key)
                
                # Embed target
                res = self.voyage_client.embed([TARGET], model=VOYAGE_MODEL, input_type="document")
                self.v_target = np.array(res.embeddings[0])
                logger.info(f"Target vector initialized using Voyage AI (dim: {len(self.v_target)})")
                
            except Exception as e:
                logger.error(f"Failed to init target embedding with Voyage: {e}")
                self.v_target = None # Ensure it's None if failed
        else:
            logger.error("VOYAGE_API_KEY not set")

    async def render_index(self):
        if self.templates:
            return self.templates.TemplateResponse(
                "index.html",
                {
                    "request": type('Request', (), {'url': type('URL', (), {'path': '/'})()})()
                }
            ).body.decode('utf-8')
        return "<h1>Vector Hacking Demo</h1><p>Templates not loaded.</p>"

    async def start_hacking(self):
        if self.running:
            return {"status": "already_running"}
        
        if self.v_target is None:
             return {"status": "error", "error": "Target vector not initialized. Check VOYAGE_API_KEY and logs."}

        # Always create a fresh SharedState for each new attack
        self.shared_state = SharedState.remote()
        
        self.running = True
        self.task = asyncio.create_task(self._run_loop())
        return {"status": "started"}

    async def stop_hacking(self):
        self.running = False
        if self.task:
            try:
                self.task.cancel()
            except Exception:
                pass
        # Reset shared state to ensure fresh start on next attack
        self.shared_state = None
        return {"status": "stopped"}

    async def _run_loop(self):
        if not self.shared_state or self.v_target is None:
            return

        try:
            while self.running:
                state = await self.shared_state.get_state.remote()
                if state['MATCH_FOUND'] or state['TOTAL_COST'] >= COST_LIMIT:
                    self.running = False
                    break
                
                # Parallelism
                NUM_PARALLEL_GUESSES = 3 
                futures = [
                    generate_and_evaluate_guess.remote(
                        self.v_target, 
                        self.shared_state,
                        self.openai_key,
                        self.voyage_key
                    ) 
                    for _ in range(NUM_PARALLEL_GUESSES)
                ]
                
                # Use ray.get() to wait for all futures (non-blocking in async context via to_thread)
                await asyncio.to_thread(ray.get, futures)
                
                # Rate limit
                await asyncio.sleep(0.5)
                
        except Exception as e:
            logger.error(f"Error in loop: {e}")
            self.running = False

    async def get_status(self):
        if not self.shared_state:
            return {"status": "not_started", "running": False}
        
        state = await self.shared_state.get_state.remote()
        
        response = {
            "status": "running" if self.running else "stopped",
            "running": self.running,
            "CURRENT_BEST_TEXT": state['CURRENT_BEST_TEXT'],
            "CURRENT_BEST_ERROR": float(state['CURRENT_BEST_ERROR']) if state['CURRENT_BEST_ERROR'] != np.inf else None,
            "GUESSES_MADE": state['GUESSES_MADE'],
            "TOTAL_COST": state['TOTAL_COST'],
            "MATCH_FOUND": state['MATCH_FOUND'],
            "LAST_ERROR": state['LAST_ERROR'],
            "MODEL_USED": "gpt-3.5-turbo"
        }
        return response
