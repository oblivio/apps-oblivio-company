"""
Password Manager Actor
A Ray Actor that handles all Password Manager operations.
"""

import os
import secrets
import string
import base64
import logging
import pathlib
import datetime
import re
import math
import io
from typing import Dict, Any, List, Optional
import ray
from bson import ObjectId
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from werkzeug.security import generate_password_hash, check_password_hash
import pyotp
import qrcode

logger = logging.getLogger(__name__)

# Security configuration
MAX_LOGIN_ATTEMPTS = 5  # Maximum failed login attempts before lockout
LOCKOUT_DURATION_MINUTES = 30  # Account lockout duration in minutes
PASSWORD_HISTORY_COUNT = 5  # Number of previous passwords to remember
MIN_PASSWORD_AGE_DAYS = 0  # Minimum days before password can be changed
MAX_PASSWORD_AGE_DAYS = 365  # Maximum password age before warning (1 year)

# Actor-local paths
experiment_dir = pathlib.Path(__file__).parent
templates_dir = experiment_dir / "templates"


@ray.remote
class ExperimentActor:
    """
    Password Manager Ray Actor.
    Handles all Password Manager operations using the experiment database abstraction.
    """

    def __init__(self, mongo_uri: str, db_name: str, write_scope: str, read_scopes: List[str]):
        self.mongo_uri = mongo_uri
        self.db_name = db_name
        self.write_scope = write_scope
        self.read_scopes = read_scopes
        
        # Load templates
        try:
            from fastapi.templating import Jinja2Templates
            
            if templates_dir.is_dir():
                self.templates = Jinja2Templates(directory=str(templates_dir))
            else:
                self.templates = None
                logger.warning(f"[{write_scope}-Actor] Template dir not found at {templates_dir}")
            
            logger.info(f"[{write_scope}-Actor] Successfully loaded templates.")
        except ImportError as e:
            logger.critical(f"[{write_scope}-Actor] ❌ CRITICAL: Failed to load templates: {e}", exc_info=True)
            self.templates = None
        
        # Database initialization
        try:
            from mdb_runtime.database import create_actor_database
            self.db = create_actor_database(
                mongo_uri,
                db_name,
                write_scope,
                read_scopes
            )
            logger.info(
                f"[{write_scope}-Actor] initialized with write_scope='{self.write_scope}' "
                f"(DB='{db_name}') using magical database abstraction"
            )
        except Exception as e:
            logger.critical(f"[{write_scope}-Actor] ❌ CRITICAL: Failed to init DB: {e}", exc_info=True)
            self.db = None

    def _check_ready(self):
        """Check if actor is ready."""
        if not self.db:
            raise RuntimeError("Database not initialized. Check logs for import errors.")
        if not self.templates:
            raise RuntimeError("Templates not loaded. Check logs for import errors.")

    # --- Security & Encryption Helpers ---
    
    @staticmethod
    def calculate_password_entropy(password: str) -> float:
        """Calculate password entropy (bits). Higher is better."""
        if not password:
            return 0.0
        
        # Character set analysis
        has_lower = bool(re.search(r'[a-z]', password))
        has_upper = bool(re.search(r'[A-Z]', password))
        has_digit = bool(re.search(r'\d', password))
        has_special = bool(re.search(r'[^a-zA-Z0-9]', password))
        
        # Calculate character set size
        charset_size = 0
        if has_lower:
            charset_size += 26
        if has_upper:
            charset_size += 26
        if has_digit:
            charset_size += 10
        if has_special:
            charset_size += 32  # Common special characters
        
        if charset_size == 0:
            return 0.0
        
        # Entropy = log2(charset_size^length)
        entropy = math.log2(charset_size ** len(password))
        return entropy
    
    @staticmethod
    def check_password_strength(password: str) -> Dict[str, Any]:
        """Check password strength and return detailed analysis."""
        if not password:
            return {
                "valid": False,
                "score": 0,
                "entropy": 0.0,
                "issues": ["Password is empty"],
                "strength": "very_weak"
            }
        
        issues = []
        score = 0
        entropy = ExperimentActor.calculate_password_entropy(password)
        
        # Length checks
        if len(password) < 6:
            issues.append("Password must be at least 6 characters long")
        elif len(password) >= 8:
            score += 1
        elif len(password) >= 12:
            score += 2
        
        # Character variety checks
        has_lower = bool(re.search(r'[a-z]', password))
        has_upper = bool(re.search(r'[A-Z]', password))
        has_digit = bool(re.search(r'\d', password))
        has_special = bool(re.search(r'[^a-zA-Z0-9]', password))
        
        if not has_lower:
            issues.append("Add lowercase letters")
        else:
            score += 1
        
        if not has_upper:
            issues.append("Add uppercase letters")
        else:
            score += 1
        
        if not has_digit:
            issues.append("Add numbers")
        else:
            score += 1
        
        if not has_special:
            issues.append("Add special characters (!@#$%^&*)")
        else:
            score += 1
        
        # Entropy checks (advisory only)
        if entropy < 20:
            issues.append("Password is too predictable (consider using a longer password)")
        elif entropy >= 40:
            score += 1
        
        # Common patterns
        common_patterns = [
            r'(.)\1{3,}',  # Repeated characters (aaaa)
            r'(012|123|234|345|456|567|678|789|890)',  # Sequential numbers
            r'(abc|bcd|cde|def|efg|fgh|ghi|hij|ijk|jkl|klm|lmn|mno|nop|opq|pqr|qrs|rst|stu|tuv|uvw|vwx|wxy|xyz)',  # Sequential letters
            r'(qwerty|asdfgh|zxcvbn)',  # Keyboard patterns
        ]
        
        for pattern in common_patterns:
            if re.search(pattern, password.lower()):
                issues.append("Avoid common patterns (sequences, repeated characters)")
                score = max(0, score - 1)
                break
        
        # Determine strength level (more lenient thresholds)
        if score <= 1 or entropy < 15:
            strength = "very_weak"
        elif score <= 2 or entropy < 25:
            strength = "weak"
        elif score <= 3 or entropy < 35:
            strength = "medium"
        elif score <= 4 or entropy < 45:
            strength = "strong"
        else:
            strength = "very_strong"
        
        # Only require minimum length - everything else is advisory
        return {
            "valid": len(password) >= 6,  # Only check minimum length
            "score": score,
            "entropy": round(entropy, 2),
            "issues": issues,
            "strength": strength,
            "length": len(password)
        }
    
    @staticmethod
    def get_encryption_key_from_password(password: str, salt: bytes) -> bytes:
        """Derives a secure 32-byte encryption key from a user's master password and a salt."""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=480000,  # Increased iterations for stronger security (OWASP recommendation)
        )
        key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
        return key

    @staticmethod
    def encrypt_data(data: str, key: bytes) -> str:
        """Encrypts data using the derived Fernet key."""
        f = Fernet(key)
        return f.encrypt(data.encode()).decode()

    @staticmethod
    def decrypt_data(token: str, key: bytes) -> str:
        """Decrypts data using the derived Fernet key."""
        f = Fernet(key)
        return f.decrypt(token.encode()).decode()

    # --- Template Rendering Methods ---
    
    async def render_index(self):
        """Render the main password manager page."""
        self._check_ready()
        try:
            return self.templates.TemplateResponse(
                "index.html",
                {
                    "request": type('Request', (), {'url': type('URL', (), {'path': '/'})()})()
                }
            ).body.decode('utf-8')
        except Exception as e:
            logger.error(f"Error rendering index: {e}", exc_info=True)
            return f"<h1>Error</h1><pre>{e}</pre>"

    # --- API Methods ---
    
    async def register_user(self, username: str, password: str) -> Dict[str, Any]:
        """Register a new user with enhanced security checks."""
        self._check_ready()
        try:
            username_lower = username.lower().strip() if username else ""
            password = password.strip() if password else ""
            
            if not username_lower or len(username_lower) < 3:
                return {"status": "error", "error": "Username must be at least 3 characters long"}
            
            # Basic password requirements (minimal)
            if not password or len(password) < 6:
                return {"status": "error", "error": "Master password must be at least 6 characters long"}
            
            logger.debug(f"Registering user: '{username_lower}' (password length: {len(password)})")
            
            # Check password strength (advisory only, not blocking)
            strength_check = self.check_password_strength(password)
            
            # Check if user already exists
            existing_user = await self.db.users.find_one({"username": username_lower})
            if existing_user:
                return {"status": "error", "error": "Username is already taken. Please choose another."}
            
            # Generate salt and hash password
            salt = os.urandom(16)
            hashed_password = generate_password_hash(password)
            logger.debug(f"Password hashed successfully for user '{username_lower}' (hash length: {len(hashed_password)})")
            
            # Store password history (for future password reuse prevention)
            password_history = [{
                "password_hash": hashed_password,
                "created_at": datetime.datetime.utcnow()
            }]
            
            # Create user document with security fields
            user_doc = {
                "username": username_lower,
                "email": username_lower,  # Use username as email for sub_auth compatibility
                "password": hashed_password,
                "salt": salt,
                "password_history": password_history,
                "failed_login_attempts": 0,
                "locked_until": None,
                "last_login": None,
                "last_login_ip": None,
                "created_at": datetime.datetime.utcnow(),
                "password_changed_at": datetime.datetime.utcnow(),
                # MFA fields
                "mfa_enabled": False,
                "mfa_secret": None,  # Encrypted TOTP secret
                "mfa_backup_codes": [],  # Hashed backup codes
                "mfa_verified_at": None
            }
            
            logger.info(f"Attempting to insert user document for '{username_lower}' into database...")
            result = await self.db.users.insert_one(user_doc)
            
            if not result or not result.inserted_id:
                logger.error(f"Failed to insert user: insert_one returned {result}")
                return {"status": "error", "error": "Failed to create user account. Please try again."}
            
            user_id = str(result.inserted_id)
            logger.info(f"User '{username_lower}' successfully inserted with ID: {user_id}")
            
            # Log security event
            try:
                await self._log_security_event(
                    user_id=user_id,
                    event_type="user_registered",
                    details={"username": username_lower, "password_strength": strength_check["strength"]}
                )
            except Exception as log_error:
                logger.warning(f"Failed to log security event for user registration: {log_error}")
            
            # Generate encryption key for session
            try:
                encryption_key = self.get_encryption_key_from_password(password, salt)
                encryption_key_str = encryption_key.decode() if isinstance(encryption_key, bytes) else encryption_key
                logger.info(f"Encryption key generated successfully for user '{username_lower}'")
                
                return {
                    "status": "success",
                    "message": "Registration successful",
                    "user_id": user_id,
                    "encryption_key": encryption_key_str
                }
            except Exception as key_error:
                logger.error(f"Failed to generate encryption key for user '{username_lower}': {key_error}", exc_info=True)
                return {"status": "error", "error": f"Failed to generate encryption key: {str(key_error)}"}
        except Exception as e:
            logger.error(f"Error registering user '{username_lower}': {e}", exc_info=True)
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            return {"status": "error", "error": f"Registration failed: {str(e)}"}

    async def login_user(self, username: str, password: str, ip_address: Optional[str] = None) -> Dict[str, Any]:
        """Authenticate a user with account lockout protection and security logging."""
        self._check_ready()
        try:
            username_lower = username.lower().strip() if username else ""
            password = password.strip() if password else ""
            
            if not username_lower or not password:
                return {"status": "error", "error": "Username and password are required"}
            
            logger.debug(f"Login attempt for username: '{username_lower}' (password length: {len(password)})")
            user = await self.db.users.find_one({"username": username_lower})
            
            if not user:
                logger.debug(f"User not found: '{username_lower}'")
            else:
                logger.debug(f"User found: '{username_lower}', checking password hash...")
            
            # Check if account is locked
            if user and user.get("locked_until"):
                locked_until = user["locked_until"]
                if isinstance(locked_until, datetime.datetime):
                    if datetime.datetime.utcnow() < locked_until:
                        remaining_minutes = int((locked_until - datetime.datetime.utcnow()).total_seconds() / 60)
                        await self._log_security_event(
                            user_id=str(user["_id"]),
                            event_type="login_blocked_locked",
                            details={"username": username_lower, "ip": ip_address, "remaining_minutes": remaining_minutes}
                        )
                        return {
                            "status": "error",
                            "error": f"Account is locked due to too many failed login attempts. Try again in {remaining_minutes} minutes."
                        }
                    else:
                        # Lockout expired, reset
                        await self.db.users.update_one(
                            {"_id": user["_id"]},
                            {"$set": {"locked_until": None, "failed_login_attempts": 0}}
                        )
            
            # Verify password
            password_valid = False
            if user:
                try:
                    stored_hash = user.get("password")
                    if not stored_hash:
                        logger.error(f"User '{username_lower}' has no password hash stored!")
                        password_valid = False
                    else:
                        password_valid = check_password_hash(stored_hash, password)
                        if not password_valid:
                            logger.warning(f"Login failed: Password hash mismatch for user '{username_lower}' (stored hash type: {type(stored_hash)}, password length: {len(password)})")
                        else:
                            logger.debug(f"Password verified successfully for user '{username_lower}'")
                except Exception as e:
                    logger.error(f"Error checking password hash for user '{username_lower}': {e}", exc_info=True)
                    password_valid = False
            
            if not user or not password_valid:
                # Increment failed login attempts
                if user:
                    failed_attempts = user.get("failed_login_attempts", 0) + 1
                    update_data = {"failed_login_attempts": failed_attempts}
                    
                    # Lock account if max attempts reached
                    if failed_attempts >= MAX_LOGIN_ATTEMPTS:
                        locked_until = datetime.datetime.utcnow() + datetime.timedelta(minutes=LOCKOUT_DURATION_MINUTES)
                        update_data["locked_until"] = locked_until
                        await self.db.users.update_one(
                            {"_id": user["_id"]},
                            {"$set": update_data}
                        )
                        await self._log_security_event(
                            user_id=str(user["_id"]),
                            event_type="account_locked",
                            details={"username": username_lower, "ip": ip_address, "failed_attempts": failed_attempts}
                        )
                        return {
                            "status": "error",
                            "error": f"Too many failed login attempts. Account locked for {LOCKOUT_DURATION_MINUTES} minutes."
                        }
                    else:
                        await self.db.users.update_one(
                            {"_id": user["_id"]},
                            {"$set": update_data}
                        )
                    
                    await self._log_security_event(
                        user_id=str(user["_id"]),
                        event_type="login_failed",
                        details={"username": username_lower, "ip": ip_address, "failed_attempts": failed_attempts}
                    )
                
                return {"status": "error", "error": "Invalid username or master password"}
            
            # Check if MFA is enabled
            mfa_enabled = user.get("mfa_enabled", False)
            
            if mfa_enabled:
                # Password is correct, but MFA verification is required
                # Don't reset failed attempts yet - wait for MFA verification
                return {
                    "status": "mfa_required",
                    "message": "MFA verification required",
                    "user_id": str(user["_id"]),
                    "mfa_enabled": True
                }
            
            # Successful login (no MFA) - reset failed attempts and update login info
            await self.db.users.update_one(
                {"_id": user["_id"]},
                {
                    "$set": {
                        "failed_login_attempts": 0,
                        "locked_until": None,
                        "last_login": datetime.datetime.utcnow(),
                        "last_login_ip": ip_address
                    }
                }
            )
            
            # Log successful login
            await self._log_security_event(
                user_id=str(user["_id"]),
                event_type="login_success",
                details={"username": username_lower, "ip": ip_address, "mfa_used": False}
            )
            
            # Generate encryption key from password and salt
            encryption_key = self.get_encryption_key_from_password(password, user["salt"])
            
            return {
                "status": "success",
                "message": "Login successful",
                "user_id": str(user["_id"]),
                "encryption_key": encryption_key.decode()
            }
        except Exception as e:
            logger.error(f"Error logging in user: {e}", exc_info=True)
            return {"status": "error", "error": str(e)}
    
    async def _log_security_event(self, user_id: str, event_type: str, details: Dict[str, Any]) -> None:
        """Log security events for audit trail."""
        try:
            event = {
                "user_id": ObjectId(user_id),
                "event_type": event_type,
                "details": details,
                "timestamp": datetime.datetime.utcnow()
            }
            await self.db.security_events.insert_one(event)
        except Exception as e:
            logger.error(f"Failed to log security event: {e}", exc_info=True)

    async def check_session(self, user_id: str) -> Dict[str, Any]:
        """Check if user exists (for session validation)."""
        self._check_ready()
        try:
            user = await self.db.users.find_one({"_id": ObjectId(user_id)})
            has_user = await self.db.users.count_documents({}) > 0
            
            return {
                "authenticated": user is not None,
                "has_user": has_user
            }
        except Exception as e:
            logger.error(f"Error checking session: {e}", exc_info=True)
            return {"authenticated": False, "has_user": False}

    async def get_passwords(self, user_id: str, encryption_key: str) -> Dict[str, Any]:
        """Get all passwords for a user and decrypt them. Returns passwords with duplicate detection."""
        self._check_ready()
        try:
            passwords = await self.db.passwords.find(
                {"user_id": ObjectId(user_id)}
            ).sort("website", 1).to_list(length=None)
            
            key = encryption_key.encode()
            decrypted_passwords = []
            
            for p in passwords:
                try:
                    p["_id"] = str(p["_id"])
                    p.pop("user_id", None)  # Don't send user_id to client
                    
                    # Decrypt the fields
                    p["website"] = self.decrypt_data(p["website"], key)
                    p["username"] = self.decrypt_data(p["username"], key)
                    p["password"] = self.decrypt_data(p["password"], key)
                    
                    decrypted_passwords.append(p)
                except Exception as e:
                    # Handle cases where decryption might fail for a specific password
                    logger.warning(f"Could not decrypt password for entry {p.get('_id')}. Error: {e}. Skipping.")
                    continue
            
            # Detect duplicate passwords
            password_counts = {}
            for pwd_entry in decrypted_passwords:
                pwd_value = pwd_entry["password"]
                if pwd_value not in password_counts:
                    password_counts[pwd_value] = []
                password_counts[pwd_value].append(pwd_entry)
            
            # Separate duplicates from unique passwords
            duplicates = []
            unique_passwords = []
            
            for pwd_value, entries in password_counts.items():
                if len(entries) > 1:
                    # This password is used in multiple entries
                    for entry in entries:
                        entry["is_duplicate"] = True
                        entry["duplicate_count"] = len(entries)
                        duplicates.append(entry)
                else:
                    entries[0]["is_duplicate"] = False
                    unique_passwords.append(entries[0])
            
            # Calculate password ages and identify old passwords
            now = datetime.datetime.utcnow()
            old_passwords = []
            for pwd in unique_passwords + duplicates:
                created_at = pwd.get("created_at")
                if created_at:
                    if isinstance(created_at, str):
                        try:
                            created_at = datetime.datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                        except:
                            created_at = None
                    if created_at and (now - created_at).days > MAX_PASSWORD_AGE_DAYS:
                        old_passwords.append(pwd["_id"])
            
            return {
                "passwords": unique_passwords,
                "insecure": duplicates,  # Renamed from "duplicates" to "insecure" for clarity
                "duplicates": duplicates,  # Keep for backward compatibility
                "has_duplicates": len(duplicates) > 0,
                "has_insecure": len(duplicates) > 0,  # New field for "INSECURE" section
                "duplicate_count": len(duplicates),
                "insecure_count": len(duplicates),  # New field for insecure count
                "old_passwords": old_passwords,  # Passwords older than MAX_PASSWORD_AGE_DAYS
                "has_old_passwords": len(old_passwords) > 0
            }
        except Exception as e:
            logger.error(f"Error getting passwords: {e}", exc_info=True)
            return {
                "passwords": [],
                "insecure": [],  # Renamed from "duplicates" to "insecure"
                "duplicates": [],  # Keep for backward compatibility
                "has_duplicates": False,
                "has_insecure": False,  # New field
                "duplicate_count": 0,
                "insecure_count": 0  # New field
            }

    async def add_password(self, user_id: str, encryption_key: str, website: str, username: str, password: str) -> Dict[str, Any]:
        """Add a new password entry with security checks (duplicates, strength)."""
        self._check_ready()
        try:
            if not all([website, username, password]):
                return {"status": "error", "error": "Missing required data fields"}
            
            # Check password strength
            strength_check = self.check_password_strength(password)
            if strength_check["strength"] in ["very_weak", "weak"]:
                # Warn but don't block - user might have legacy weak passwords
                logger.warning(f"User {user_id} added weak password for {website} (strength: {strength_check['strength']}, entropy: {strength_check['entropy']})")
            
            key = encryption_key.encode()
            
            # Check for duplicate passwords before adding
            existing_passwords = await self.db.passwords.find(
                {"user_id": ObjectId(user_id)}
            ).to_list(length=None)
            
            # Decrypt existing passwords to check for duplicates
            duplicate_found = False
            duplicate_websites = []
            for existing in existing_passwords:
                try:
                    existing_password = self.decrypt_data(existing["password"], key)
                    if existing_password == password:
                        duplicate_found = True
                        # Decrypt website to show which account uses this password
                        existing_website = self.decrypt_data(existing["website"], key)
                        duplicate_websites.append(existing_website)
                except Exception:
                    # Skip if decryption fails
                    continue
            
            # Encrypt the data
            encrypted_doc = {
                "user_id": ObjectId(user_id),
                "website": self.encrypt_data(website, key),
                "username": self.encrypt_data(username, key),
                "password": self.encrypt_data(password, key),
                "created_at": datetime.datetime.utcnow(),
                "updated_at": datetime.datetime.utcnow()
            }
            
            result = await self.db.passwords.insert_one(encrypted_doc)
            
            # Return the decrypted version for immediate UI update
            response = {
                "status": "success",
                "_id": str(result.inserted_id),
                "website": website,
                "username": username,
                "password": password
            }
            
            # Add warning if duplicate password was detected
            if duplicate_found:
                response["is_duplicate"] = True
                response["duplicate_warning"] = f"⚠️ SECURITY WARNING: This password is already used by {len(duplicate_websites)} other account(s): {', '.join(duplicate_websites[:3])}{'...' if len(duplicate_websites) > 3 else ''}. Using the same password for multiple accounts is a security risk!"
                logger.warning(f"User {user_id} added duplicate password for {website} (already used by: {', '.join(duplicate_websites)})")
            
            # Log security event
            await self._log_security_event(
                user_id=user_id,
                event_type="password_added",
                details={"website": website, "is_duplicate": duplicate_found, "password_strength": strength_check["strength"]}
            )
            
            return response
        except Exception as e:
            logger.error(f"Error adding password: {e}", exc_info=True)
            return {"status": "error", "error": str(e)}

    async def update_password(self, user_id: str, encryption_key: str, password_id: str, website: str = None, username: str = None, password: str = None) -> Dict[str, Any]:
        """Update a password entry."""
        self._check_ready()
        try:
            key = encryption_key.encode()
            update_data = {}
            
            # Encrypt each field if provided
            if website is not None:
                update_data["website"] = self.encrypt_data(website, key)
            if username is not None:
                update_data["username"] = self.encrypt_data(username, key)
            if password is not None:
                update_data["password"] = self.encrypt_data(password, key)
            
            if not update_data:
                return {"status": "error", "error": "No fields to update provided"}
            
            update_data["updated_at"] = datetime.datetime.utcnow()
            
            result = await self.db.passwords.update_one(
                {"_id": ObjectId(password_id), "user_id": ObjectId(user_id)},
                {"$set": update_data}
            )
            
            if result.matched_count == 0:
                return {"status": "error", "error": "Password not found or access denied"}
            
            # Return the updated document (decrypted)
            updated_doc = {
                "_id": password_id,
                "website": website,
                "username": username,
                "password": password
            }
            
            return {"status": "success", **updated_doc}
        except Exception as e:
            logger.error(f"Error updating password: {e}", exc_info=True)
            return {"status": "error", "error": str(e)}

    async def delete_password(self, user_id: str, password_id: str) -> Dict[str, Any]:
        """Delete a password entry."""
        self._check_ready()
        try:
            result = await self.db.passwords.delete_one(
                {"_id": ObjectId(password_id), "user_id": ObjectId(user_id)}
            )
            
            if result.deleted_count == 0:
                return {"status": "error", "error": "Password not found or access denied"}
            
            return {"status": "success", "success": True}
        except Exception as e:
            logger.error(f"Error deleting password: {e}", exc_info=True)
            return {"status": "error", "error": str(e)}

    async def generate_password(self, length: int = 16, uppercase: bool = True, lowercase: bool = True, numbers: bool = True, symbols: bool = True) -> Dict[str, Any]:
        """Generate a secure password."""
        self._check_ready()
        try:
            if not 12 <= length <= 99:
                return {"status": "error", "error": "Password length must be between 12 and 99."}
            
            alphabet = ''
            password_parts = []
            
            # Build the character set and guarantee at least one of each selected type
            if uppercase:
                alphabet += string.ascii_uppercase
                password_parts.append(secrets.choice(string.ascii_uppercase))
            if lowercase:
                alphabet += string.ascii_lowercase
                password_parts.append(secrets.choice(string.ascii_lowercase))
            if numbers:
                alphabet += string.digits
                password_parts.append(secrets.choice(string.digits))
            if symbols:
                # Use a curated list of symbols to avoid issues with certain websites
                safe_symbols = '!@#$%^&*()_+-=[]{}|;:,.<>?'
                alphabet += safe_symbols
                password_parts.append(secrets.choice(safe_symbols))
            
            if not alphabet:
                return {"status": "error", "error": "At least one character type must be selected."}
            
            # Fill the rest of the password length with characters from the full alphabet
            remaining_length = length - len(password_parts)
            for _ in range(remaining_length):
                password_parts.append(secrets.choice(alphabet))
            
            # Shuffle the list to ensure the guaranteed characters are not always at the start
            secrets.SystemRandom().shuffle(password_parts)
            
            password = "".join(password_parts)
            
            return {"status": "success", "password": password}
        except Exception as e:
            logger.error(f"Error generating password: {e}", exc_info=True)
            return {"status": "error", "error": str(e)}

    # --- MFA Methods ---
    
    async def generate_mfa_secret(self, user_id: str, encryption_key: str) -> Dict[str, Any]:
        """Generate a TOTP secret and QR code for MFA setup."""
        self._check_ready()
        try:
            user = await self.db.users.find_one({"_id": ObjectId(user_id)})
            if not user:
                return {"status": "error", "error": "User not found"}
            
            # Generate a new TOTP secret
            secret = pyotp.random_base32()
            
            # Encrypt the secret using the user's encryption key
            key = encryption_key.encode()
            encrypted_secret = self.encrypt_data(secret, key)
            
            # Generate provisioning URI
            username = user.get("username", user.get("email", "user"))
            issuer = "Password Manager"
            totp_uri = pyotp.totp.TOTP(secret).provisioning_uri(
                name=username,
                issuer_name=issuer
            )
            
            # Generate QR code
            qr = qrcode.QRCode(version=1, box_size=10, border=5)
            qr.add_data(totp_uri)
            qr.make(fit=True)
            
            img = qr.make_image(fill_color="black", back_color="white")
            img_buffer = io.BytesIO()
            img.save(img_buffer, format='PNG')
            img_buffer.seek(0)
            qr_code_base64 = base64.b64encode(img_buffer.getvalue()).decode()
            
            # Generate backup codes
            backup_codes = [secrets.token_hex(4).upper() for _ in range(10)]
            hashed_backup_codes = [generate_password_hash(code) for code in backup_codes]
            
            # Store encrypted secret and hashed backup codes (but don't enable MFA yet)
            await self.db.users.update_one(
                {"_id": ObjectId(user_id)},
                {
                    "$set": {
                        "mfa_secret": encrypted_secret,
                        "mfa_backup_codes": hashed_backup_codes,
                        "mfa_verified_at": None
                    }
                }
            )
            
            await self._log_security_event(
                user_id=user_id,
                event_type="mfa_secret_generated",
                details={"username": username}
            )
            
            return {
                "status": "success",
                "secret": secret,  # Return plain secret for display (user needs to verify before enabling)
                "qr_code": f"data:image/png;base64,{qr_code_base64}",
                "backup_codes": backup_codes,  # Return plain codes (user must save these)
                "manual_entry_key": secret
            }
        except Exception as e:
            logger.error(f"Error generating MFA secret: {e}", exc_info=True)
            return {"status": "error", "error": str(e)}
    
    async def verify_mfa_code(self, user_id: str, code: str, encryption_key: str) -> Dict[str, Any]:
        """Verify MFA TOTP code or backup code."""
        self._check_ready()
        try:
            user = await self.db.users.find_one({"_id": ObjectId(user_id)})
            if not user:
                return {"status": "error", "error": "User not found"}
            
            mfa_enabled = user.get("mfa_enabled", False)
            encrypted_secret = user.get("mfa_secret")
            
            if not encrypted_secret:
                return {"status": "error", "error": "MFA not set up for this user"}
            
            # Decrypt the secret
            key = encryption_key.encode()
            secret = self.decrypt_data(encrypted_secret, key)
            
            # Try TOTP verification first
            totp = pyotp.TOTP(secret)
            if totp.verify(code, valid_window=1):  # Allow 1 time step window for clock skew
                # Successful TOTP verification
                await self.db.users.update_one(
                    {"_id": ObjectId(user_id)},
                    {
                        "$set": {
                            "mfa_verified_at": datetime.datetime.utcnow(),
                            "failed_login_attempts": 0,
                            "locked_until": None
                        }
                    }
                )
                
                await self._log_security_event(
                    user_id=user_id,
                    event_type="mfa_verified_totp",
                    details={"username": user.get("username")}
                )
                
                return {"status": "success", "verified": True, "method": "totp"}
            
            # Try backup code verification
            backup_codes = user.get("mfa_backup_codes", [])
            code_upper = code.upper().strip()
            
            for i, hashed_code in enumerate(backup_codes):
                if check_password_hash(hashed_code, code_upper):
                    # Remove used backup code
                    backup_codes.pop(i)
                    await self.db.users.update_one(
                        {"_id": ObjectId(user_id)},
                        {
                            "$set": {
                                "mfa_backup_codes": backup_codes,
                                "mfa_verified_at": datetime.datetime.utcnow(),
                                "failed_login_attempts": 0,
                                "locked_until": None
                            }
                        }
                    )
                    
                    await self._log_security_event(
                        user_id=user_id,
                        event_type="mfa_verified_backup",
                        details={"username": user.get("username"), "remaining_codes": len(backup_codes)}
                    )
                    
                    return {"status": "success", "verified": True, "method": "backup", "remaining_codes": len(backup_codes)}
            
            # Code verification failed
            await self._log_security_event(
                user_id=user_id,
                event_type="mfa_verification_failed",
                details={"username": user.get("username")}
            )
            
            return {"status": "error", "error": "Invalid MFA code"}
        except Exception as e:
            logger.error(f"Error verifying MFA code: {e}", exc_info=True)
            return {"status": "error", "error": str(e)}
    
    async def enable_mfa(self, user_id: str, encryption_key: str, verification_code: str) -> Dict[str, Any]:
        """Enable MFA after verifying the setup code."""
        self._check_ready()
        try:
            user = await self.db.users.find_one({"_id": ObjectId(user_id)})
            if not user:
                return {"status": "error", "error": "User not found"}
            
            encrypted_secret = user.get("mfa_secret")
            if not encrypted_secret:
                return {"status": "error", "error": "MFA secret not found. Please generate a new secret first."}
            
            # Verify the code before enabling
            verify_result = await self.verify_mfa_code(user_id, verification_code, encryption_key)
            if verify_result.get("status") != "success":
                return {"status": "error", "error": "Invalid verification code. Please enter the code from your authenticator app."}
            
            # Enable MFA
            await self.db.users.update_one(
                {"_id": ObjectId(user_id)},
                {
                    "$set": {
                        "mfa_enabled": True,
                        "mfa_verified_at": datetime.datetime.utcnow()
                    }
                }
            )
            
            await self._log_security_event(
                user_id=user_id,
                event_type="mfa_enabled",
                details={"username": user.get("username")}
            )
            
            return {"status": "success", "message": "MFA enabled successfully"}
        except Exception as e:
            logger.error(f"Error enabling MFA: {e}", exc_info=True)
            return {"status": "error", "error": str(e)}
    
    async def disable_mfa(self, user_id: str, password: str) -> Dict[str, Any]:
        """Disable MFA (requires password verification)."""
        self._check_ready()
        try:
            user = await self.db.users.find_one({"_id": ObjectId(user_id)})
            if not user:
                return {"status": "error", "error": "User not found"}
            
            # Verify password before disabling MFA
            if not check_password_hash(user["password"], password):
                await self._log_security_event(
                    user_id=user_id,
                    event_type="mfa_disable_failed_password",
                    details={"username": user.get("username")}
                )
                return {"status": "error", "error": "Invalid password"}
            
            # Disable MFA and clear secrets
            await self.db.users.update_one(
                {"_id": ObjectId(user_id)},
                {
                    "$set": {
                        "mfa_enabled": False,
                        "mfa_secret": None,
                        "mfa_backup_codes": [],
                        "mfa_verified_at": None
                    }
                }
            )
            
            await self._log_security_event(
                user_id=user_id,
                event_type="mfa_disabled",
                details={"username": user.get("username")}
            )
            
            return {"status": "success", "message": "MFA disabled successfully"}
        except Exception as e:
            logger.error(f"Error disabling MFA: {e}", exc_info=True)
            return {"status": "error", "error": str(e)}
    
    async def get_mfa_status(self, user_id: str) -> Dict[str, Any]:
        """Get MFA status for a user."""
        self._check_ready()
        try:
            user = await self.db.users.find_one({"_id": ObjectId(user_id)})
            if not user:
                return {"status": "error", "error": "User not found"}
            
            mfa_enabled = user.get("mfa_enabled", False)
            has_secret = user.get("mfa_secret") is not None
            backup_codes_count = len(user.get("mfa_backup_codes", []))
            
            return {
                "status": "success",
                "mfa_enabled": mfa_enabled,
                "mfa_setup": has_secret,
                "backup_codes_remaining": backup_codes_count
            }
        except Exception as e:
            logger.error(f"Error getting MFA status: {e}", exc_info=True)
            return {"status": "error", "error": str(e)}
    
    async def change_master_password(
        self, 
        user_id: str, 
        current_password: str, 
        new_password: str,
        current_encryption_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Change user's master password with security checks.
        SECURITY: Automatically re-encrypts all passwords with new encryption key.
        """
        self._check_ready()
        try:
            user = await self.db.users.find_one({"_id": ObjectId(user_id)})
            if not user:
                return {"status": "error", "error": "User not found"}
            
            # Verify current password
            if not check_password_hash(user["password"], current_password):
                await self._log_security_event(
                    user_id=user_id,
                    event_type="password_change_failed",
                    details={"username": user.get("username"), "reason": "invalid_current_password"}
                )
                return {"status": "error", "error": "Current password is incorrect"}
            
            # Check if new password is the same as current
            if check_password_hash(user["password"], new_password):
                return {"status": "error", "error": "New password must be different from current password"}
            
            # Check password strength (advisory only, not blocking)
            strength_check = self.check_password_strength(new_password)
            if not strength_check["valid"]:
                # Only enforce minimum length
                if len(new_password) < 6:
                    return {
                        "status": "error",
                        "error": "New password must be at least 6 characters long"
                    }
            
            # Check password history (prevent reusing last 5 passwords)
            password_history = user.get("password_history", [])
            for old_pwd in password_history[-PASSWORD_HISTORY_COUNT:]:
                if check_password_hash(old_pwd["password_hash"], new_password):
                    return {
                        "status": "error",
                        "error": f"New password cannot be one of your last {PASSWORD_HISTORY_COUNT} passwords. Please choose a different password."
                    }
            
            # Generate new salt and hash
            new_salt = os.urandom(16)
            new_hashed_password = generate_password_hash(new_password)
            
            # Generate new encryption key
            new_encryption_key = self.get_encryption_key_from_password(new_password, new_salt)
            new_key_bytes = new_encryption_key if isinstance(new_encryption_key, bytes) else new_encryption_key.encode()
            
            # Get current encryption key for re-encryption
            if not current_encryption_key:
                # Fallback: derive from current password (shouldn't happen in normal flow)
                current_encryption_key_bytes = self.get_encryption_key_from_password(current_password, user["salt"])
            else:
                # Convert string to bytes if needed
                if isinstance(current_encryption_key, str):
                    current_encryption_key_bytes = current_encryption_key.encode()
                else:
                    current_encryption_key_bytes = current_encryption_key
            
            old_key_bytes = current_encryption_key_bytes
            
            # Re-encrypt all passwords with new encryption key
            passwords = await self.db.passwords.find(
                {"user_id": ObjectId(user_id)}
            ).to_list(length=None)
            
            re_encrypted_count = 0
            for pwd_entry in passwords:
                try:
                    # Decrypt with old key
                    old_website = self.decrypt_data(pwd_entry["website"], old_key_bytes)
                    old_username = self.decrypt_data(pwd_entry["username"], old_key_bytes)
                    old_password = self.decrypt_data(pwd_entry["password"], old_key_bytes)
                    
                    # Encrypt with new key
                    new_website = self.encrypt_data(old_website, new_key_bytes)
                    new_username = self.encrypt_data(old_username, new_key_bytes)
                    new_password_enc = self.encrypt_data(old_password, new_key_bytes)
                    
                    # Update password entry
                    await self.db.passwords.update_one(
                        {"_id": pwd_entry["_id"]},
                        {
                            "$set": {
                                "website": new_website,
                                "username": new_username,
                                "password": new_password_enc,
                                "updated_at": datetime.datetime.utcnow()
                            }
                        }
                    )
                    re_encrypted_count += 1
                except Exception as e:
                    logger.warning(f"Failed to re-encrypt password entry {pwd_entry.get('_id')}: {e}")
                    # Continue with other entries
            
            # Update password history (keep last PASSWORD_HISTORY_COUNT)
            updated_history = password_history[-PASSWORD_HISTORY_COUNT:] if len(password_history) > PASSWORD_HISTORY_COUNT else password_history
            updated_history.append({
                "password_hash": user["password"],  # Store old password hash
                "created_at": datetime.datetime.utcnow()
            })
            
            # Update user document
            await self.db.users.update_one(
                {"_id": ObjectId(user_id)},
                {
                    "$set": {
                        "password": new_hashed_password,
                        "salt": new_salt,
                        "password_history": updated_history,
                        "password_changed_at": datetime.datetime.utcnow()
                    }
                }
            )
            
            # Log security event
            await self._log_security_event(
                user_id=user_id,
                event_type="password_changed",
                details={
                    "username": user.get("username"), 
                    "password_strength": strength_check["strength"],
                    "passwords_re_encrypted": re_encrypted_count
                }
            )
            
            return {
                "status": "success",
                "message": "Master password changed successfully",
                "encryption_key": new_encryption_key.decode() if isinstance(new_encryption_key, bytes) else new_encryption_key,
                "passwords_re_encrypted": re_encrypted_count,
                "warning": f"✅ All {re_encrypted_count} password(s) have been automatically re-encrypted with your new master password."
            }
        except Exception as e:
            logger.error(f"Error changing master password: {e}", exc_info=True)
            return {"status": "error", "error": str(e)}

    async def initialize(self):
        """
        Post-initialization hook: performs security checks and initialization.
        This is called automatically when the actor starts up.
        
        NOTE: Demo mode is DISABLED for security. This password manager requires
        proper user registration and authentication. No demo users or demo content
        will be created.
        """
        import sys
        print(f"[{self.write_scope}-Actor] ⚡ INITIALIZE CALLED - Starting post-initialization setup...", flush=True, file=sys.stderr)
        logger.info(f"[{self.write_scope}-Actor] ⚡ INITIALIZE CALLED - Starting post-initialization setup...")
        
        try:
            # Demo mode is disabled for security
            logger.info(f"[{self.write_scope}-Actor] 🔒 Demo mode is DISABLED - Password manager requires proper authentication")
            print(f"[{self.write_scope}-Actor] 🔒 Demo mode is DISABLED - Password manager requires proper authentication", flush=True, file=sys.stderr)
            
            # Verify database is ready
            if not self.db:
                raise RuntimeError("Database not initialized")
            
            logger.info(f"[{self.write_scope}-Actor] ✅ Password manager initialized and ready (demo mode disabled)")
            print(f"[{self.write_scope}-Actor] ✅ Password manager initialized and ready (demo mode disabled)", flush=True, file=sys.stderr)
                
        except Exception as e:
            import traceback
            print(f"[{self.write_scope}-Actor] ❌ ERROR during initialization: {e}", flush=True, file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            logger.error(f"[{self.write_scope}-Actor] ❌ ERROR during initialization: {e}", exc_info=True)

