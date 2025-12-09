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
            # Get examples of interactive components in context
            interactive_examples = {}
            if "algebra_foundation" in self.courses:
                # Example: interactive_balance_lab for "Variables on Both Sides"
                interactive_examples["balance_lab"] = {
                    "topic": "Variables on Both Sides",
                    "page": self.courses["algebra_foundation"]["pages"]["phase1"]
                }
                # Example: interactive_literal_eq for "Literal Equations"
                interactive_examples["literal_eq"] = {
                    "topic": "Literal Equations",
                    "page": self.courses["algebra_foundation"]["pages"]["phase2"]
                }
            
            if "mastering_lines" in self.courses:
                # Example: interactive_graphing for "Graphing Linear Functions"
                interactive_examples["graphing"] = {
                    "topic": "Graphing Linear Functions",
                    "page": self.courses["mastering_lines"]["pages"]["graphing"]
                }
                # Example: interactive_slope_machine for "Parallel & Perpendicular"
                interactive_examples["slope_machine"] = {
                    "topic": "Parallel & Perpendicular Lines",
                    "page": self.courses["mastering_lines"]["pages"]["parallel"]
                }
            
            if "systems_inequalities" in self.courses:
                # Example: interactive_visual_systems for "Systems of Equations"
                interactive_examples["visual_systems"] = {
                    "topic": "Systems of Equations",
                    "page": self.courses["systems_inequalities"]["pages"]["visual-systems"]
                }
                # Example: interactive_inequalities for "Linear Inequalities"
                interactive_examples["inequalities"] = {
                    "topic": "Linear Inequalities",
                    "page": self.courses["systems_inequalities"]["pages"]["inequalities"]
                }
            
            # Build examples string
            examples_str = "\n\nEXAMPLES FROM EXISTING MODULES (Study these patterns):\n"
            for key, ex in interactive_examples.items():
                examples_str += f"\n--- Example: {ex['topic']} uses {key} ---\n"
                examples_str += json.dumps(ex["page"], indent=2)
                examples_str += "\n"

            # Get sample module for general style reference
            sample_module_str = ""
            if "algebra_foundation" in self.courses:
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
            {examples_str}
            
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
            you MUST include interactive visual components that DIRECTLY RELATE to the topic being taught.
            
            MATCHING GUIDE - Choose the interactive component that matches your topic:
               Study the examples above to see how each component is used in context!
               
               - "interactive_graphing": {{ "title", "subtitle" }} 
                 → ONLY use for: Graphing linear functions, plotting points, y=mx+b, coordinate planes
                 → See example above: "Graphing Linear Functions" topic
                 → Example structure: {{"type": "interactive_graphing", "title": "Interactive Graph", "subtitle": "The \\"Begin and Move\\" Method for y = mx + b"}}
                 
               - "interactive_visual_systems": {{ "title", "subtitle" }} 
                 → ONLY use for: Systems of equations, finding intersection points, two equations with two variables
                 → See example above: "Systems of Equations" topic
                 → Example structure: {{"type": "interactive_visual_systems", "title": "Visual Systems", "subtitle": "Adjust the sliders. The answer is the purple dot."}}
                 
               - "interactive_inequalities": {{ "title", "subtitle" }} 
                 → ONLY use for: Graphing inequalities, shading regions, < > ≤ ≥ symbols
                 → See example above: "Linear Inequalities" topic
                 → Example structure: {{"type": "interactive_inequalities", "title": "Linear Inequalities", "subtitle": "It's not just a line, it's a <strong>region</strong> (a shaded yard)."}}
                 
               - "interactive_balance_lab": {{ "title", "subtitle" }} 
                 → ONLY use for: Solving equations with variables on both sides, balancing equations
                 → See example above: "Variables on Both Sides" topic
                 → Example structure: {{"type": "interactive_balance_lab", "title": "Variables on Both Sides", "subtitle": "The Conceptual Framework: The Balance Scale Analogy"}}
                 
               - "interactive_literal_eq": {{ "title", "subtitle" }} 
                 → ONLY use for: Rearranging formulas, solving for a specific variable, literal equations
                 → See example above: "Literal Equations" topic
                 → Example structure: {{"type": "interactive_literal_eq", "title": "Literal Equations", "subtitle": "Goal: Isolate $y$ for graphing ($y = mx + b$)."}}
                 
               - "interactive_slope_machine": {{ "title", "subtitle" }} 
                 → ONLY use for: Slope relationships, parallel lines, perpendicular lines, slope calculations
                 → See example above: "Parallel & Perpendicular Lines" topic
                 → Example structure: {{"type": "interactive_slope_machine", "title": "The Slope Machine", "subtitle": "How slopes relate to each other."}}
            
            RULES:
            1. ONLY include an interactive component if it DIRECTLY teaches the topic. Do NOT add random graphs!
            2. If the topic is about graphing → use interactive_graphing or interactive_visual_systems
            3. If the topic is about solving equations → use interactive_balance_lab or interactive_literal_eq
            4. If the topic is about inequalities → use interactive_inequalities
            5. If the topic is about slopes/parallel/perpendicular → use interactive_slope_machine
            6. If the topic does NOT involve graphs/equations/visualization → DO NOT add interactive components
            
            The interactive component MUST be relevant to the actual topic "{topic}". 
            If you cannot find a relevant match, do NOT force an interactive component!
            
            Output the FULL JSON object (merging the structure with the new "pages" and "quiz_data").
            Output valid JSON only.
            """
            
            logger.info(f"[{self.write_scope}-Actor] Step 2: Generating content...")
            draft_module = await self._call_llm(content_prompt)
            if "error" in draft_module: return draft_module

            # 3. ENHANCEMENT AGENT: Add More & Make It Better
            enhancement_prompt = f"""
            You are an educational content enhancement agent. Your job is to analyze the draft module and 
            make it SIGNIFICANTLY BETTER by adding missing elements and improving what exists.
            
            Topic: {topic}
            Current Draft Module:
            {json.dumps(draft_module, indent=2)}
            
            REFERENCE EXAMPLES (for quality comparison):
            {examples_str}
            
            YOUR MISSION - Think critically and act like an agent:
            
            1. ANALYZE what's missing:
               - Are there enough interactive visual components for this topic? (Check examples above)
               - Are pages too sparse? Do they need more content blocks?
               - Are there gaps in the learning progression?
               - Is the quiz_data comprehensive enough? (Should have 4-5 questions minimum)
               - Are there opportunities for more engaging content types (hero, card_grid, html, etc.)?
            
            2. IDENTIFY what could be better:
               - Are titles/subtitles engaging enough?
               - Could pages benefit from additional explanatory content?
               - Are there places where visual aids (html blocks with diagrams) would help?
               - Could the content be more interactive or hands-on?
               - Are there real-world examples or analogies missing?
            
            3. ENHANCE the module by:
               - ADDING missing interactive components if the topic warrants them (use examples as reference)
               - ADDING more content blocks to sparse pages (hero sections, explanatory html, examples)
               - EXPANDING quiz_data if it's too short (aim for 4-5 quality questions)
               - IMPROVING existing content with better explanations, examples, or visuals
               - ADDING helpful content types like:
                 * "box" blocks with tips/warnings
                 * "card_grid" for key concepts
                 * "html" blocks with visual diagrams or step-by-step guides
                 * Additional interactive components where appropriate
            
            4. QUALITY CHECKS:
               - Each page should have substantial content (not just 1-2 blocks)
               - Math/STEM topics MUST have relevant interactive components (see examples)
               - Content should be engaging, not dry
               - Learning should progress logically from intro → concepts → practice → quiz
            
            IMPORTANT RULES:
            - DO NOT remove good content, only ADD and IMPROVE
            - Keep all existing good content, just enhance it
            - If adding interactive components, ensure they match the topic (see examples)
            - Make sure quiz_data has at least 4-5 questions
            - Ensure pages have rich, varied content types
            
            Output the ENHANCED JSON module with all improvements. Output valid JSON only, no markdown.
            """
            
            logger.info(f"[{self.write_scope}-Actor] Step 3: Enhancing and improving content...")
            enhanced_module = await self._call_llm(enhancement_prompt)
            if "error" in enhanced_module: 
                logger.warning(f"[{self.write_scope}-Actor] Enhancement failed, using draft module")
                enhanced_module = draft_module

            # 4. REVIEW AGENT: Critique and Polish
            review_prompt = f"""
            You are a strict educational editor. Review the ENHANCED JSON module below.
            
            Topic: {topic}
            Enhanced Module:
            {json.dumps(enhanced_module)}
            
            REFERENCE EXAMPLES (for interactive component matching):
            {examples_str}
            
            Your Job:
            1. Ensure all Tailwind classes are valid and look good (modern UI).
            2. Ensure the "id" is snake_case and unique.
            3. Check that "quiz_data" exists and matches the quiz page.
            4. Improve the tone to be engaging, not dry.
            5. Ensure HTML content is safe and well-structured.
            6. CRITICAL: Check interactive component RELEVANCE using the examples above:
               - Compare the topic "{topic}" to the example topics in the reference section
               - If interactive components exist, verify they DIRECTLY relate to "{topic}" like the examples show
               - interactive_graphing → only for graphing/plotting topics (see "Graphing Linear Functions" example)
               - interactive_visual_systems → only for systems of equations (see "Systems of Equations" example)
               - interactive_inequalities → only for inequality topics (see "Linear Inequalities" example)
               - interactive_balance_lab → only for equations with variables on both sides (see "Variables on Both Sides" example)
               - interactive_literal_eq → only for rearranging formulas (see "Literal Equations" example)
               - interactive_slope_machine → only for slope/parallel/perpendicular topics (see "Parallel & Perpendicular" example)
               - If an interactive component doesn't match the topic like in the examples, REMOVE it or REPLACE it with the correct one
               - If the topic needs visuals (like the examples show) but none exist, ADD the appropriate one matching the example patterns
               - If the topic doesn't need visuals, ensure no irrelevant interactive components are included
            
            Output the FINAL, polished JSON object. No markdown.
            """
            
            logger.info(f"[{self.write_scope}-Actor] Step 4: Reviewing and polishing...")
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

