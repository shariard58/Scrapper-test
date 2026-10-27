import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor
from app.config import get_db_config, settings
from app.lib.logger import get_logger
from typing import Optional, List, Dict, Any

logger = get_logger(__name__)

# Connection pools for dev and prod
connection_pools: Dict[str, pool.SimpleConnectionPool] = {}

def _is_valid_db_config(config: dict) -> bool:
    """Check if database configuration is valid (not a placeholder)"""
    placeholder_keywords = ["your_", "placeholder", "example.com"]
    host = str(config.get("host", "")).lower()
    
    # Check if host contains placeholder keywords
    for keyword in placeholder_keywords:
        if keyword in host:
            return False
    
    # Check if essential fields are not empty
    if not config.get("host") or not config.get("dbname") or not config.get("user"):
        return False
    
    return True

def init_db():
    """Initialize database connection pool for the environment specified in ENV variable"""
    # Get the current environment from settings
    current_env = settings.ENV
    logger.info(f"Initializing database connection for environment: {current_env}")
    
    try:
        # Get configuration for the current environment only
        db_config = get_db_config(current_env)
        
        if not _is_valid_db_config(db_config):
            logger.error(f"{current_env.upper()} database configuration appears to be a placeholder or invalid.")
            raise ValueError(f"Invalid database configuration for {current_env} environment. Please check your .env file.")
        
        # Initialize pool for the current environment only
        connection_pools[current_env] = pool.SimpleConnectionPool(
            1, 20,
            host=db_config["host"],
            port=db_config["port"],
            database=db_config["dbname"],
            user=db_config["user"],
            password=db_config["password"]
        )
        logger.info(f"{current_env.upper()} database connection pool initialized successfully")
        
    except Exception as e:
        logger.error(f"Error initializing {current_env} database pool: {e}")
        raise RuntimeError(f"Failed to initialize database connection for {current_env} environment. Check your .env file for valid database credentials.")

def close_db():
    """Close all database connection pools"""
    for env, pool_obj in connection_pools.items():
        if pool_obj:
            pool_obj.closeall()
            logger.info(f"{env} database connection pool closed")

def get_connection(env: str = None):
    """Get a connection from the pool"""
    if env is None:
        env = settings.ENV
    if env not in connection_pools:
        raise ValueError(f"Database pool for environment '{env}' not initialized")
    return connection_pools[env].getconn()

def return_connection(conn, env: str = None):
    """Return a connection to the pool"""
    if env is None:
        env = settings.ENV
    if env in connection_pools:
        connection_pools[env].putconn(conn)

def execute_query(
    query: str,
    params: Optional[tuple] = None,
    fetchone: bool = False,
    fetchall: bool = False,
) -> Optional[Any]:
    """Execute a database query"""
    conn = None
    try:
        env = settings.ENV
        conn = get_connection(env)
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query, params)
            conn.commit()
            
            if fetchall:
                return cursor.fetchall()
            elif fetchone:
                return cursor.fetchone()
            else:
                return cursor.rowcount
    except Exception as e:
        logger.error(f"Database error: {e}")
        if conn:
            conn.rollback()
        return None
    finally:
        if conn:
            return_connection(conn, env)
