"""Configuration loading for ezenciel-coding.
Last edited: 2026-02-23 (resolve db_path/projects_dir/workspaces_dir relative to REPO_ROOT)
"""
from typing import Dict, Any, Optional
import os
import sys
import secrets
import yaml
from pathlib import Path
from dotenv import dotenv_values
from pydantic_settings import BaseSettings
from loguru import logger

logger.remove()
logger.add(sys.stderr, format="{time:YYYY-MM-DDTHH:mm:ss}Z | {level:<7} | {message}", level="INFO")

REPO_ROOT = Path(__file__).resolve().parents[1]

class Settings(BaseSettings):
    api_key: Optional[str] = None
    db_path: str = "sqlite:///data/worker.db"
    poll_interval_seconds: int = 5
    workspaces_dir: str = "workspaces"
    projects_dir: str = "projects"
    git_user_name: str = "Dev Worker"
    git_user_email: str = "devworker@ezenciel.ai"
    job_max_retries: int = 3
    job_retry_delay_minutes: int = 15
    job_timeout_minutes: int = 120
    
    class Config:
        env_file = str(REPO_ROOT / ".env")

def _resolve_dir(path: str) -> str:
    """Resolve path relative to REPO_ROOT if not absolute."""
    p = Path(path)
    if not p.is_absolute():
        p = REPO_ROOT / p
    return str(p)


def _resolve_db_path(db_path: str) -> str:
    """Resolve sqlite relative path to REPO_ROOT if not absolute."""
    if not db_path.startswith("sqlite:///"):
        return db_path
    path_part = db_path[len("sqlite:///"):]
    p = Path(path_part)
    if not p.is_absolute():
        p = REPO_ROOT / p
    return f"sqlite:///{p}"


settings = Settings()
settings.db_path = _resolve_db_path(settings.db_path)
if not settings.api_key:
    settings.api_key = secrets.token_urlsafe(32)
    logger.warning("API_KEY not set — generated for this session: {}", settings.api_key)


def load_project_configs() -> Dict[str, Any]:
    """
    Load project configurations from the projects_dir.
    Each project should be a directory containing:
    - config.yaml (repository_url, stack, test_command, install_command)
    - .env (optional, environment variables for the project runtime)
    - system.md (optional, per-project coder instructions)
    """
    projects = {}
    projects_dir = _resolve_dir(settings.projects_dir)
    if not os.path.exists(projects_dir):
        os.makedirs(projects_dir, exist_ok=True)
        return projects

    for entry in os.listdir(projects_dir):
        project_path = os.path.join(projects_dir, entry)
        if os.path.isdir(project_path):
            project_id = entry
            config_file = os.path.join(project_path, "config.yaml")
            env_file = os.path.join(project_path, ".env")
            system_file = os.path.join(project_path, "system.md")
            
            if os.path.exists(config_file):
                try:
                    with open(config_file, "r") as f:
                        config_data = yaml.safe_load(f) or {}
                    
                    # Load .env if it exists and merge it into env_vars
                    env_vars = config_data.get("env_vars", {})
                    if os.path.exists(env_file):
                        parsed_env = dotenv_values(env_file)
                        env_vars.update(parsed_env)
                    
                    config_data["env_vars"] = env_vars

                    # Load optional per-project system instructions.
                    system_instructions = ""
                    if os.path.exists(system_file):
                        with open(system_file, "r", encoding="utf-8") as system_handle:
                            system_instructions = system_handle.read().strip()
                    if system_instructions:
                        config_data["system_instructions"] = system_instructions

                    projects[project_id] = config_data
                except Exception as e:
                    logger.error("Error loading project {}: {}", project_id, e)
                
    return projects
