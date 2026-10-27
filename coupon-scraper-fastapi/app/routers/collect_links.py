from functools import partial

from fastapi import APIRouter, Query

import app.lib.scrapeurl as su
from app.lib.logger import get_logger
from app.lib.response import (client_error_response,
                              internal_server_error_response, success_response)

router = APIRouter()
logger = get_logger(__name__)


def validate_inputs(domain: str, handle: str, query: str, page: int) -> bool:
    """Validate that all required inputs are provided and not empty."""
    return bool(domain and handle and query and page)


def process_query_string(query: str) -> list[str]:
    """Process query string into a list of trimmed query terms."""
    return [q.strip() for q in query.split(",")]


def create_store_config(handle: str, query_list: list[str]) -> dict:
    """Create store configuration dictionary."""
    return {
        'handle': handle,
        'query': query_list
    }


def get_algolia_config() -> dict:
    """Get Algolia API configuration."""
    return {
        'offers': {
            'app-id': 'ZC0GPM38IX',
            'api-key': '80e8b1435748e719ae1373e1a8d067c8'
        }
    }


def get_scraper_groups(algolia_config: dict) -> dict:
    """Get scraper groups mapping for different pages."""
    return {
        1: [
            su.coupert,
            su.discounttime,
            su.karmanow,
            su.rakuten,
            su.hotdeals
        ],
        2: [
            su.slickdeals,
            su.capitalone,
            su.retailmenot,
            su.honey,
            su.rebates
        ],
        3: [
            su.coupons,
            su.dealcatcher,
            su.dontpayfull,
            partial(su.offers, algolia=algolia_config),
            su.savings
        ],
        4: [
            su.dealspotr,
            su.promocodes,
            su.couponchief,
            su.goodshop,
            su.simplycodes
        ],
        5: [
            su.tenereteam,
            su.couponcause,
            su.goodsearch,
            su.valuecom
        ],
        6: [
            su.lovecoupons,
            su.knoji,
            su.swagbucks,
            su.joinsmarty,
        ],
        7: [
            su.rebatesme,
            su.refermate,
            su.discountreactor,
            su.couponbox
        ],
        8: [
            su.dealdrop,
            # su.revounts,
            su.dazzdeals,
            su.greenpromocodes,
        ],
        9: [
            su.couponbind,
            su.deala,
            su.promopro
        ],
        10: [
            # su.couponbirds,
            su.troupon,
            su.worthepenny,                              
        ],
        11:[
            su.savingheist,
            su.coupongrouphy,
            su.joincheckmate,
            su.faircoupons,
          
        ], 
        
        12:[
              su.gocashback,
              su.trustdeals,
              su.couponupto,
              su.promosreview
        ],
         13:[
              su.couponsgo,
              su.getscoupon,
              su.rediscount,
              su.couponterra
        ],
        14:[
             su.couponkiss,
             su.shipthedeal,
             su.couponpromo,
             su.couponblender
        ],
        15:[
             su.xyzcodes,
             su.kotag,
             su.zensavings,
             su.coponcodes,
             su.dealam
        ],
        
        16:[
            su.latestdeals
        ]
         
    }


def execute_scrapers(
    page: int,
    domain: str,
    store_config: dict,
    scraper_groups: dict
) -> dict:
    """Execute scrapers for the specified page and return handles."""
    if page not in scraper_groups:
        return None
    
    handles = {}
    logger.info(f"Processing page: {page} for domain: {domain}, handle: {store_config['handle']}, query: {store_config['query']}")
    
    for scraper in scraper_groups[page]:
        handles = scraper(handles, domain, store_config)
    
    return handles


@router.get("/collect-links")
async def collect_links(
    domain: str = Query(..., description="Domain name"),
    handle: str = Query(..., description="Handle name"),
    query: str = Query(..., description="Query name"),
    page: int = Query(1, description="Page number")
):
    try:
        # Validate inputs
        if not validate_inputs(domain, handle, query, page):
            return client_error_response("Domain, handle, query, and page are required")
        
        # Process query string
        query_list = process_query_string(query)
        
        # Create store configuration
        store_config = create_store_config(handle, query_list)
        
        # Get Algolia configuration
        algolia_config = get_algolia_config()
        
        # Get scraper groups
        scraper_groups = get_scraper_groups(algolia_config)
        
        # Execute scrapers
        handles = execute_scrapers(page, domain, store_config, scraper_groups)
        
        if handles is None:
            return client_error_response(f"Invalid page number: {page}. Must be between 1 and 10")
        
        return success_response("Collect links successfully", handles)
    except Exception as e:
        logger.error(f"API error: {e}")
        return internal_server_error_response(f"error: {e}")