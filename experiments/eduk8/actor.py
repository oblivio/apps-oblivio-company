import logging
import ray
import pathlib
import json
import os
import httpx
from fastapi.templating import Jinja2Templates

# Optional Google GenerativeAI import
try:
    import google.generativeai as genai
    GOOGLE_GENAI_AVAILABLE = True
except ImportError:
    genai = None
    GOOGLE_GENAI_AVAILABLE = False

logger = logging.getLogger(__name__)

@ray.remote
class ExperimentActor:
    def __init__(self, mongo_uri: str, db_name: str, write_scope: str, read_scopes: list[str]):
        self.write_scope = write_scope
        self.read_scopes = read_scopes
        
        # Setup templates
        experiment_dir = pathlib.Path(__file__).parent
        self.data_file = experiment_dir / "data.json"
        templates_dir = experiment_dir / "templates"
        if templates_dir.is_dir():
            self.templates = Jinja2Templates(directory=str(templates_dir))
            # Add tojson filter for templates
            if "tojson" not in self.templates.env.filters:
                self.templates.env.filters["tojson"] = lambda x: json.dumps(x)
        else:
            self.templates = None
            logger.warning(f"[{write_scope}-Actor] Template dir not found at {templates_dir}")
            
        # Load Data
        self.courses = {}
        if self.data_file.exists():
            try:
                with open(self.data_file, 'r') as f:
                    self.courses = json.load(f)
                logger.info(f"[{write_scope}-Actor] Loaded {len(self.courses)} courses from {self.data_file}")
            except Exception as e:
                logger.error(f"[{write_scope}-Actor] Error loading data: {e}")
        else:
             logger.warning(f"[{write_scope}-Actor] Data file not found: {self.data_file}")
        
        # Initialize API Keys and Clients
        self.openai_api_key = os.getenv("OPENAI_API_KEY")
        self.google_api_key = os.getenv("GOOGLE_API_KEY")
        
        self.google_client_configured = False
        if self.google_api_key and GOOGLE_GENAI_AVAILABLE:
            try:
                genai.configure(api_key=self.google_api_key)
                self.google_client_configured = True
                logger.info(f"[{write_scope}-Actor] Google GenAI Configured")
            except Exception as e:
                logger.error(f"[{write_scope}-Actor] Failed to configure Google GenAI: {e}")
        elif not GOOGLE_GENAI_AVAILABLE:
             logger.warning(f"[{write_scope}-Actor] google-generativeai library not found.")

        if not self.openai_api_key and not self.google_client_configured:
            logger.warning(f"[{write_scope}-Actor] No LLM credentials found. Module generation disabled.")
        else:
            logger.info(f"[{write_scope}-Actor] LLM ready.")

        logger.info(f"[{write_scope}-Actor] Initialized.")

    async def generate_module(self, topic: str):
        if not self.openai_api_key and not self.google_client_configured:
            return {"error": "LLM client not initialized (missing API keys)"}

        # 1. ARCHITECT AGENT: Design the structure
        architect_prompt = f"""
        You are an educational content architect. Your goal is to design the structure of a high-quality learning module about "{topic}".
        
        The structure must be valid JSON following this schema:
        {{
            "id": "unique_snake_case_id",
            "title": "Engaging Title",
            "subtitle": "Clear Subtitle",
            "theme": "indigo",  // Choose from: indigo, sky, stone, emerald, blue
            "navigation": [
                {{ "id": "intro", "label": "Introduction", "icon": "fa-book-open" }},
                {{ "id": "chapter1", "label": "Concept 1", "icon": "fa-star" }},
                {{ "id": "chapter2", "label": "Concept 2", "icon": "fa-bolt" }},
                {{ "id": "quiz", "label": "Mastery Quiz", "icon": "fa-check-double" }}
            ]
        }}
        
        Output ONLY the JSON. No markdown.
        """
        
        try:
            logger.info(f"[{self.write_scope}-Actor] Step 1: Architecting module for '{topic}'...")
            structure = await self._call_llm(architect_prompt)
            if "error" in structure: return structure
            
            # 2. CONTENT AGENT: Flesh out the content
            # Get a sample of a good module for few-shot prompting
            sample_module_str = ""
            if "algebra_foundation" in self.courses:
                # Use a truncated version of algebra_foundation as a "gold standard" example
                sample_course = self.courses["algebra_foundation"]
                sample_module_str = json.dumps({
                    "pages": {
                        "intro": sample_course["pages"]["intro"]
                    },
                    "quiz_data": sample_course["quiz_data"][:2]
                })

            content_prompt = f"""
            You are an expert educational content creator. 
            Fill in the content for the module structure defined below.
            
            Topic: {topic}
            Structure: {json.dumps(structure)}
            
            REFERENCE QUALITY (Do not copy, but match this depth and style):
            {sample_module_str}
            
            REQUIREMENTS:
            1. "pages": Create a dictionary where keys match the navigation IDs.
            2. "quiz_data": Create an array of 4-5 questions.
            3. Content Types to use in "content" arrays:
               - "hero": {{ "badge", "title", "text" }}
               - "html": {{ "html" }} (Use Tailwind CSS, FontAwesome icons. be creative!)
               - "box": {{ "style" (info/warning), "title", "content" }}
               - "list": {{ "items": [ {{ "label", "value" }} ] }}
               - "card_grid": {{ "cards": [ {{ "title", "text", "icon", "color" }} ] }}
               - "mastery_quiz": {{ "type": "mastery_quiz" }} (ONLY for the quiz page)
            
            CRITICAL: For topics involving graphs, equations, visualizations, or mathematical concepts, 
            you MUST include interactive visual components. Use these types:
               - "interactive_graphing": {{ "title", "subtitle" }} - For graphing linear functions (y=mx+b)
               - "interactive_visual_systems": {{ "title", "subtitle" }} - For systems of equations with visual graphs
               - "interactive_inequalities": {{ "title", "subtitle" }} - For graphing inequalities and shading regions
               - "interactive_balance_lab": {{ "title", "subtitle" }} - For solving equations with variables on both sides
               - "interactive_literal_eq": {{ "title", "subtitle" }} - For rearranging literal equations/formulas
               - "interactive_slope_machine": {{ "title", "subtitle" }} - For exploring slope relationships (parallel/perpendicular)
            
            If the topic involves any mathematical visualization, graphing, or interactive exploration, 
            you MUST include at least one interactive component. Do NOT skip visuals for math topics!
            
            Output the FULL JSON object (merging the structure with the new "pages" and "quiz_data").
            Output valid JSON only.
            """
            
            logger.info(f"[{self.write_scope}-Actor] Step 2: Generating content...")
            draft_module = await self._call_llm(content_prompt)
            if "error" in draft_module: return draft_module

            # 3. REVIEW AGENT: Critique and Polish
            review_prompt = f"""
            You are a strict educational editor. Review the JSON module below.
            
            Module:
            {json.dumps(draft_module)}
            
            Your Job:
            1. Ensure all Tailwind classes are valid and look good (modern UI).
            2. Ensure the "id" is snake_case and unique.
            3. Check that "quiz_data" exists and matches the quiz page.
            4. Improve the tone to be engaging, not dry.
            5. Ensure HTML content is safe and well-structured.
            6. CRITICAL: If this is a math/STEM topic, verify that interactive visual components 
               (interactive_graphing, interactive_visual_systems, interactive_inequalities, etc.) 
               are included. If missing, ADD them to appropriate pages. Visuals are essential for learning!
            
            Output the FINAL, polished JSON object. No markdown.
            """
            
            logger.info(f"[{self.write_scope}-Actor] Step 3: Reviewing and polishing...")
            final_module = await self._call_llm(review_prompt)
            return final_module

        except Exception as e:
            logger.error(f"[{self.write_scope}-Actor] Generation pipeline failed: {e}")
            return {"error": str(e)}

    async def _call_llm(self, prompt: str):
        """Helper to make LLM calls, preferring Google GenAI if available"""
        # Strategy: Prefer Google GenAI, fallback to OpenAI
        
        if self.google_client_configured:
            try:
                # Use Google GenAI
                model_name = os.getenv("GOOGLE_MODEL", "gemini-3-pro-preview") 
                
                def _do_google_generate():
                    model = genai.GenerativeModel(model_name)
                    # Set response mime type to json if supported by the model/SDK version, 
                    # or just rely on the prompt instructions.
                    # google-generativeai v0.5+ supports generation_config response_mime_type
                    response = model.generate_content(
                        prompt,
                        generation_config=genai.types.GenerationConfig(
                            response_mime_type="application/json",
                            temperature=0.7
                        )
                    )
                    return response.text

                import asyncio
                json_text = await asyncio.to_thread(_do_google_generate)
                return json.loads(json_text)
                
            except Exception as e:
                logger.error(f"Google GenAI call failed: {e}. Falling back to OpenAI if available.")
                if not self.openai_api_key:
                    return {"error": f"Google GenAI failed: {str(e)}"}
                # Fallthrough to OpenAI

        if self.openai_api_key:
            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    response = await client.post(
                        "https://api.openai.com/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {self.openai_api_key}",
                            "Content-Type": "application/json"
                        },
                        json={
                            "model": os.getenv("LLM_MODEL", "gpt-4o"),
                            "messages": [
                                {"role": "system", "content": "You are a JSON generator for an educational platform. Output pure JSON."},
                                {"role": "user", "content": prompt}
                            ],
                            "response_format": { "type": "json_object" }
                        }
                    )
                    
                    if response.status_code != 200:
                        logger.error(f"OpenAI API Error: {response.status_code} - {response.text}")
                        return {"error": f"OpenAI API Error: {response.status_code}"}
                    
                    result_json = response.json()
                    content = result_json["choices"][0]["message"]["content"]
                    return json.loads(content)
            except Exception as e:
                return {"error": str(e)}
        
        return {"error": "No available LLM client configured."}

    def save_module(self, module_data: dict):
        try:
            module_id = module_data.get("id")
            if not module_id:
                return {"error": "Module data missing ID"}
            
            # Update in-memory
            self.courses[module_id] = module_data
            
            # Persist to disk
            with open(self.data_file, 'w') as f:
                json.dump(self.courses, f, indent=2)
                
            logger.info(f"[{self.write_scope}-Actor] Saved module {module_id}")
            return {"success": True, "id": module_id}
        except Exception as e:
            logger.error(f"[{self.write_scope}-Actor] Save failed: {e}")
            return {"error": str(e)}

    def render_home(self, context: dict):
        if not self.templates:
            return "<h1>Error: Templates not found</h1>"
        
        template = self.templates.get_template("index.html")
        # Pass courses to the template
        return template.render(request=None, courses=self.courses, **context)

    def render_course(self, course_id: str, context: dict):
        if not self.templates:
            return "<h1>Error: Templates not found</h1>"
            
        if course_id not in self.courses:
            return None # Signal 404
            
        template = self.templates.get_template("base.html")
        return template.render(request=None, course=self.courses[course_id], **context)

    def render_preview(self, context: dict):
        if not self.templates:
            return "<h1>Error: Templates not found</h1>"
            
        template = self.templates.get_template("preview.html")
        return template.render(request=None, **context)

