from app.database import execute_query
from app.lib.logger import get_logger
from typing import Optional, Tuple, Any

logger = get_logger(__name__)

def get_store_info(store_domain: str) -> Optional[dict]:
    """Get store information by domain"""
    query = """
        SELECT id, store_name, store_domain, is_active 
        FROM public.stores 
        WHERE store_domain = %s
    """
    return execute_query(query, (store_domain,), fetchone=True)

def get_coupon_scanner_by_store(store: str, domain_name: str) -> Optional[Tuple]:
    """Get coupon scanner information by store and domain"""
    try:
        query = """
            SELECT sc.id, sc.coupons, sc.website_urls, sc.no_of_leaked_codes, sc.updated_at
            FROM public.scanner_website as sw
            INNER JOIN public.scanner_coupon as sc ON sc.scanner_website = sw.id 
            WHERE LOWER(sw.name) = %s and LOWER(sc.store) = %s
        """
        result = execute_query(query, (domain_name, store), fetchone=True, fetchall=False)
        return result
    except Exception as e:
        logger.error(f"Error {domain_name}, store: {store} get_coupon_scanner_by_store: {e}")
        return None

def update_coupon_scanner_by_id(id: int, coupons: list) -> Optional[int]:
    """Update coupon scanner with new coupons"""
    try:
        query = """
            UPDATE public.scanner_coupon 
            SET coupons = %s, no_of_leaked_codes = %s, updated_at = NOW()
            WHERE id = %s
        """
        return execute_query(query, (coupons, len(coupons), id), fetchone=False, fetchall=False)
    except Exception as e:
        logger.error(f"Database error `update_coupon_scanner`: {e}")
        return None
