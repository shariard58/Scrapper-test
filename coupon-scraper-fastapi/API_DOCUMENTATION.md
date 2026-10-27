# Coupon Scanner API Documentation

## Overview

The Coupon Scanner API is a FastAPI application designed for collecting and scanning coupons from various coupon websites. The API provides endpoints to collect links, scan coupons, and manage coupon data.

**Base URL:** `/api/v1`  
**API Version:** `1.0.0`

---

## Table of Contents

- [General Information](#general-information)
- [Response Format](#response-format)
- [Error Handling](#error-handling)
- [Endpoints](#endpoints)
  - [Collect Links](#1-collect-links)
  - [Coupon Scanner](#2-coupon-scanner)
  - [Collect Coupons](#3-collect-coupons)
- [Supported Websites](#supported-websites)

---

## General Information

### Base Endpoints

- **Root:** `GET /` - Returns API information
- **Health Check:** `GET /health` - Returns API health status

### Authentication

Currently, the API does not require authentication. All endpoints are publicly accessible.

---

## Response Format

### Success Response

```json
{
  "message": "Success message",
  "result": { /* response data */ }
}
```

### Error Response

```json
{
  "message": "Error message"
}
```

### HTTP Status Codes

- `200` - Success
- `400` - Bad Request (Client Error)
- `404` - Not Found
- `500` - Internal Server Error

---

## Error Handling

The API uses standard HTTP status codes and returns error messages in JSON format:

- **400 Bad Request:** Invalid parameters or missing required fields
- **404 Not Found:** Resource not found
- **500 Internal Server Error:** Server-side errors

---

## Endpoints

### 1. Collect Links

Collect links from various coupon websites based on domain, handle, query, and page number.

**Endpoint:** `GET /api/v1/collect-links`

**Description:**  
This endpoint scrapes coupon links from multiple coupon websites organized into 10 different page groups. Each page group contains different scrapers that search for coupons based on the provided domain, handle, and query parameters.

**Query Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `domain` | string | Yes | - | Domain name of the store |
| `handle` | string | Yes | - | Handle name for the store |
| `query` | string | Yes | - | Comma-separated query terms (e.g., "term1, term2, term3") |
| `page` | integer | No | 1 | Page number (1-10) |

**Page Groups:**

- **Page 1:** coupert, discounttime, karmanow, rakuten, hotdeals
- **Page 2:** slickdeals, capitalone, retailmenot, honey, rebates
- **Page 3:** coupons, dealcatcher, dontpayfull, offers, savings
- **Page 4:** dealspotr, promocodes, couponchief, goodshop, simplycodes
- **Page 5:** tenereteam, couponcause, goodsearch, valuecom
- **Page 6:** lovecoupons, knoji, swagbucks, joinsmarty
- **Page 7:** rebatesme, refermate, discountreactor, couponbox
- **Page 8:** dealdrop, dazzdeals, greenpromocodes
- **Page 9:** couponbind, deala, promopro
- **Page 10:** couponbirds

**Example Request:**

```bash
GET /api/v1/collect-links?domain=example.com&handle=example-store&query=discount,coupon&page=1
```

**Example Success Response:**

```json
{
  "message": "Collect links successfully",
  "result": {
    "coupert": ["https://example.com/coupon1", "https://example.com/coupon2"],
    "rakuten": ["https://example.com/coupon3"],
    "hotdeals": ["https://example.com/coupon4"]
  }
}
```

**Example Error Response:**

```json
{
  "message": "Domain, handle, query, and page are required"
}
```

```json
{
  "message": "Invalid page number: 11. Must be between 1 and 10"
}
```

**Notes:**

- The `query` parameter accepts comma-separated values that will be trimmed and processed as a list
- Each page group executes multiple scrapers in sequence
- The response contains a dictionary where keys are scraper names and values are lists of collected links
- Page 3 uses Algolia API for the `offers` scraper (requires special configuration)

---

### 2. Coupon Scanner

Get coupons from a specific store and website. This endpoint retrieves coupon scanner information from the database, fetches coupons from the specified website, and updates the database with the results.

**Endpoint:** `GET /api/v1/coupon-scanner`

**Description:**  
Fetches coupons from a specific coupon website for a given store. The endpoint first retrieves the coupon scanner link from the database, then uses the appropriate scraper function to extract coupons, removes duplicates, and updates the database with the results.

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `store` | string | Yes | Store name |
| `website` | string | Yes | Website name (must be one of the supported websites) |

**Example Request:**

```bash
GET /api/v1/coupon-scanner?store=amazon&website=coupert
```

**Example Success Response:**

```json
{
  "message": "Fetch coupons successfully",
  "result": [
    "SAVE20",
    "SUMMER2024",
    "NEWUSER10"
  ]
}
```

**Example Error Responses:**

```json
{
  "message": "Not found coupon scanner link"
}
```

```json
{
  "message": "invalid website"
}
```

```json
{
  "message": "No coupons found"
}
```

**Notes:**

- The endpoint requires that coupon scanner information exists in the database for the given store and website
- The website name is case-insensitive (converted to lowercase)
- Duplicate coupons are automatically removed
- The database is updated with the fetched coupons after a successful fetch
- If no coupons are found, an empty array is returned with a success message

---

### 3. Collect Coupons

Collect coupons from a website using provided links.

**Endpoint:** `GET /api/v1/collect-coupons`

**Description:**  
This endpoint accepts a website name and a comma-separated list of links, then fetches coupons from those links using the appropriate scraper function. Unlike the coupon-scanner endpoint, this endpoint does not interact with the database and works directly with provided links.

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `website` | string | Yes | Website name (must be one of the supported websites) |
| `links` | string | Yes | Comma-separated list of URLs to scrape |

**Example Request:**

```bash
GET /api/v1/collect-coupons?website=coupert&links=https://example.com/page1,https://example.com/page2
```

**Example Success Response:**

```json
{
  "message": "Fetch coupons successfully",
  "result": [
    "COUPON1",
    "COUPON2",
    "DISCOUNT20"
  ]
}
```

**Example Error Responses:**

```json
{
  "message": "invalid website"
}
```

```json
{
  "message": "No coupons found"
}
```

**Notes:**

- Links should be provided as a comma-separated string
- Empty links are automatically filtered out
- Duplicate coupons are automatically removed
- The website name is case-insensitive
- This endpoint does not update the database; it only returns the fetched coupons

---

## Supported Websites

The following websites are supported across the API endpoints:

| Website | Available in Endpoints |
|---------|----------------------|
| coupert | All endpoints |
| discountime | All endpoints |
| karma | coupon-scanner, collect-coupons |
| rakuten | All endpoints |
| hotdeals | All endpoints |
| slickdeals | All endpoints |
| capitalone | All endpoints |
| retailmenot | All endpoints |
| honey | All endpoints |
| rebates | All endpoints |
| coupons | All endpoints |
| dealcatcher | All endpoints |
| dontpayfull | All endpoints |
| couponbirds | All endpoints |
| offers | All endpoints |
| savings | All endpoints |
| dealspotr | All endpoints |
| promocodes | All endpoints |
| couponchief | All endpoints |
| goodshop | All endpoints |
| simplycodes | All endpoints |
| tenereteam | All endpoints |
| couponcause | All endpoints |
| goodsearch | All endpoints |
| valuecom | All endpoints |
| lovecoupons | All endpoints |
| knoji | All endpoints |
| swagbucks | All endpoints |
| joinsmarty | All endpoints |
| rebatesme | All endpoints |
| refermate | All endpoints |
| discountreactor | All endpoints |
| couponbox | All endpoints |
| dealdrop | All endpoints |
| revounts | coupon-scanner, collect-coupons |
| dazzdeals | All endpoints |
| greenpromocode | All endpoints |
| couponbind | All endpoints |
| deala | All endpoints |
| promopro | All endpoints |

**Note:** Some websites may have different naming conventions:
- `karmanow` (in collect-links) vs `karma` (in other endpoints)
- `greenpromocodes` (in collect-links) vs `greenpromocode` (in other endpoints)

---

## Example Usage

### Complete Workflow Example

1. **Collect Links:**
```bash
curl "http://localhost:8000/api/v1/collect-links?domain=amazon.com&handle=amazon&query=electronics,books&page=1"
```

2. **Collect Coupons from Links:**
```bash
curl "http://localhost:8000/api/v1/collect-coupons?website=coupert&links=https://coupert.com/amazon,https://coupert.com/amazon/deals"
```

3. **Get Coupons via Scanner:**
```bash
curl "http://localhost:8000/api/v1/coupon-scanner?store=amazon&website=coupert"
```

---

## Database Schema

The API interacts with the following database tables:

### `scanner_website`
- `id` - Primary key
- `name` - Website name

### `scanner_coupon`
- `id` - Primary key
- `scanner_website` - Foreign key to `scanner_website`
- `store` - Store name
- `coupons` - Array of coupon codes
- `website_urls` - Array of website URLs
- `no_of_leaked_codes` - Number of coupons
- `updated_at` - Last update timestamp

### `stores`
- `id` - Primary key
- `store_name` - Store name
- `store_domain` - Store domain
- `is_active` - Active status

---

## Rate Limiting

Currently, there are no rate limits implemented. However, it is recommended to:
- Implement appropriate delays between requests
- Cache results when possible
- Use pagination for large datasets

---

## Version History

- **v1.0.0** - Initial API release

---

## Support

For issues or questions, please refer to the project repository or contact the development team.
