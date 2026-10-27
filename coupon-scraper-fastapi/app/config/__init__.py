import os
from app.lib.logger import get_logger
from pydantic_settings import BaseSettings
from typing import Literal

logger = get_logger(__name__)

class Settings(BaseSettings):
    # Environment
    ENV: Literal["dev", "prod"] = "dev"
    
    # Dev Database Configuration
    DEV_DB_HOST: str
    DEV_DB_PORT: int = 5432
    DEV_DB_NAME: str
    DEV_DB_USER: str
    DEV_DB_PASSWORD: str
    
    # Prod Database Configuration
    PROD_DB_HOST: str
    PROD_DB_PORT: int = 5432
    PROD_DB_NAME: str
    PROD_DB_USER: str
    PROD_DB_PASSWORD: str
    
    # Logging
    LOG_LEVEL: str = "INFO"
    
    # ZenRows API Configuration
    ZENROWS_API_KEY: str
    
    class Config:
        env_file = ".env"
        case_sensitive = False

settings = Settings()

def get_db_config(env: str = None):
    """Get database configuration based on environment"""
    if env is None:
        env = settings.ENV
    
    if env == "dev":
        return {
            "host": settings.DEV_DB_HOST,
            "port": settings.DEV_DB_PORT,
            "dbname": settings.DEV_DB_NAME,
            "user": settings.DEV_DB_USER,
            "password": settings.DEV_DB_PASSWORD
        }
    else:
        return {
            "host": settings.PROD_DB_HOST,
            "port": settings.PROD_DB_PORT,
            "dbname": settings.PROD_DB_NAME,
            "user": settings.PROD_DB_USER,
            "password": settings.PROD_DB_PASSWORD
        }
