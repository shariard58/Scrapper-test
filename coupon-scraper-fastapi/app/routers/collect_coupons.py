from fastapi import APIRouter, Query
from app.lib.response import (
    success_response,
    client_error_response,
    internal_server_error_response
)
from app.lib.logger import get_logger
from app.lib.fetch_functions import FETCH_FUNCTIONS, ASYNC_FETCH_FUNCTIONS

router = APIRouter()
logger = get_logger(__name__)


# Collect coupons from a website using provided links
@router.get("/collect-coupons")
async def collect_coupons_from_website(
    website: str = Query(..., description="Website name"),
    links: str = Query(..., description="Comma-separated list of links")
):
    """
    Collect coupons from a website using provided links
    """
    try:
        logger.info(f'links: {links}')
        
        # Convert links string to list
        links_list = [link.strip() for link in links.split(',') if link.strip()]
        logger.info(f'links_list: {links_list}')
        
        # Check if website is valid
        website_key = website.lower()
        if website_key not in FETCH_FUNCTIONS:
            return client_error_response("invalid website")
        
        # Fetch coupons
        fetch_func = FETCH_FUNCTIONS[website_key]
        if website_key in ASYNC_FETCH_FUNCTIONS:
            # These are async functions - use await since we're in an async route handler
            coupons = await fetch_func(links_list)
        else:
            coupons = fetch_func(links_list)
        
        # Remove duplicates
        coupons = list(set(coupons))
        
        # Check if coupons is not empty
        if not coupons:
            return success_response("No coupons found", [])
        
        # Return success response
        return success_response("Fetch coupons successfully", coupons)
        
    except Exception as e:
        logger.error(f"API error: {e}")
        return internal_server_error_response(f"error: {e}")
