from fastapi import APIRouter, Query
from app.models import get_coupon_scanner_by_store, update_coupon_scanner_by_id
from app.lib.response import (
    success_response,
    client_error_response,
    internal_server_error_response
)
from app.lib.logger import get_logger
from app.lib.fetch_functions import FETCH_FUNCTIONS, ASYNC_FETCH_FUNCTIONS

router = APIRouter()
logger = get_logger(__name__)


async def fetch_and_process_coupons(store: str, website: str, coupon_scanner_info: dict):
    """
    Fetch coupons from the specified website and process them.
    
    Args:
        store: Store name
        website: Website name
        coupon_scanner_info: Dictionary containing coupon scanner information
        
    Returns:
        tuple: (success: bool, response_data: dict or response object, coupons: list)
    """
    # Extract website_urls and coupon_scanner_id
    website_urls = coupon_scanner_info.get('website_urls', [])
    coupon_scanner_id = coupon_scanner_info.get('id')
    
    # Validate website_urls
    if not website_urls:
        return False, client_error_response("Not found coupon scanner link"), []
    
    # Validate website
    website_key = website.lower()
    if website_key not in FETCH_FUNCTIONS:
        return False, client_error_response("invalid website"), []
    
    # Fetch coupons
    fetch_func = FETCH_FUNCTIONS[website_key]
    if website_key in ASYNC_FETCH_FUNCTIONS:
        # These are async functions - use await since we're in an async route handler
        coupons = await fetch_func(website_urls)
    else:
        coupons = fetch_func(website_urls)
    
    # Remove duplicates
    coupons = list(set(coupons))
    
    # Log coupon information
    logger.info(f' store: {store}, website: {website}, coupons length: {len(coupons)}')
    logger.info(f' coupons: {coupons}')
    
    # Check if coupons is not empty
    if not coupons:
        return True, success_response("No coupons found", []), []
    
    # Update coupon scanner
    update_coupon_scanner_by_id(coupon_scanner_id, coupons)
    
    # Return success response
    return True, success_response("Fetch coupons successfully", coupons), coupons

@router.get("/coupon-scanner")
async def get_coupon_scanner(
    store: str = Query(..., description="Store name"),
    website: str = Query(..., description="Website name"),
):
    """
    Get coupons from a specific store and website
    """
    try:
        # Get coupon scanner info
        coupon_scanner_info = get_coupon_scanner_by_store(store, website)
        
        if not coupon_scanner_info:
            return client_error_response("Not found coupon scanner link")
        
        # Fetch and process coupons
        success, response, coupons = await fetch_and_process_coupons(
            store, website, coupon_scanner_info
        )
        
        return response
        
    except Exception as e:
        logger.error(f"API error: {e}")
        return internal_server_error_response(f"error: {e}")
