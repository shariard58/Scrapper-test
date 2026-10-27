# Coupon Scanner

A FastAPI-based REST API for coupon scanning and collection, migrated from AWS Lambda functions. This application provides three main endpoints for fetching coupons, collecting coupons from links, and discovering store links from various coupon websites.

## Features

- **Three Main Endpoints:**
  - `/api/v1/coupon-scanner` - Get coupons from a specific store and website
  - `/api/v1/collect-coupons` - Collect coupons from websites using provided links
  - `/api/v1/collect-links` - Collect store links from various coupon websites

- **Dynamic Environment Support:**
  - Development (dev) and Production (prod) database configurations
  - Environment-based database connection pooling
  - Configurable via environment variables

- **PostgreSQL Integration:**
  - Connection pooling for efficient database access
  - Support for multiple environments
  - Automatic connection management

## Project Structure

```
coupon-scanner-fastapi/
├── main.py                 # FastAPI application entry point
├── requirements.txt        # Python dependencies
├── setup.sh               # Automated setup script
├── docker-base.dev.sh     # Docker setup for development
├── docker-base.prod.sh    # Docker setup for production
├── .gitignore             # Git ignore file
├── .dockerignore          # Docker ignore file
├── README.md              # This file
└── app/
    ├── __init__.py
    ├── database.py        # Database connection and query functions
    ├── models.py          # Database models and queries
    ├── config/            # Configuration module
    │   ├── __init__.py    # Settings and database configuration
    │   ├── headers.py     # HTTP headers configuration
    │   └── coupon_scraper.py  # Coupon scraping functions
    ├── lib/               # Utility libraries
    │   ├── __init__.py
    │   ├── logger.py      # Logging utilities
    │   ├── response.py    # Response helpers
    │   ├── helpers.py     # Helper functions (ZenRows integration, HTTP utilities)
    │   └── scrapeurl.py   # Scraper functions for collecting links from coupon websites
    └── routers/           # API route handlers
        ├── __init__.py
        ├── coupon_scanner.py    # Coupon scanner endpoint
        ├── collect_coupons.py  # Collect coupons endpoint
        └── collect_links.py    # Collect links endpoint
```

## Prerequisites

- Python 3.8 or higher
- PostgreSQL database (for both dev and prod environments)
- pip (Python package manager)

## Installation

### Quick Setup (Automated)

Use the provided setup script for automated installation:

```bash
chmod +x setup.sh
./setup.sh
```

The script will:
- Create a virtual environment
- Install all dependencies
- Create a `.env` file template (if it doesn't exist)
- Guide you through next steps

### Manual Setup

#### Step 1: Clone or Navigate to Project Directory

```bash
git clone https://github.com/lookseeapp/coupon-scraper-fastapi.git
cd coupon-scanner-fastapi
```

#### Step 2: Create Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

#### Step 3: Install Dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

#### Step 4: Configure Environment Variables

Create a `.env` file in the project root with your database configurations:

```env
# Environment Configuration
ENV=dev

# Development Database Configuration
DEV_DB_HOST=localhost
DEV_DB_PORT=5432
DEV_DB_NAME=coupon_scanner_dev
DEV_DB_USER=postgres
DEV_DB_PASSWORD=your_dev_password

# Production Database Configuration
PROD_DB_HOST=your_prod_host
PROD_DB_PORT=5432
PROD_DB_NAME=coupon_scanner_prod
PROD_DB_USER=your_prod_user
PROD_DB_PASSWORD=your_prod_password

# ZenRows API Configuration (required for web scraping)
ZENROWS_API_KEY=your_zenrows_api_key

# Logging
LOG_LEVEL=INFO
```

## Running the Application

### Development Mode

```bash
# Activate virtual environment
source venv/bin/activate

# Run with auto-reload
python main.py

# Or using uvicorn directly
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

The API will be available at: `http://localhost:8000`

### Production Mode

```bash
# Set environment to prod in .env file
ENV=prod

# Run with uvicorn
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

## API Endpoints

### 1. Root Endpoint

```bash
GET http://localhost:8000/
```

Response:
```json
{
  "message": "Coupon Scanner API",
  "version": "1.0.0",
  "status": "running"
}
```

### 2. Health Check

```bash
GET http://localhost:8000/health
```

Response:
```json
{
  "status": "healthy"
}
```

### 3. Coupon Scanner

Get coupons from a specific store and website.

**Endpoint:** `GET /api/v1/coupon-scanner`

**Query Parameters:**
- `store` (required): Store name
- `website` (required): Website name (e.g., 'coupert', 'rakuten', 'honey')
- `env` (optional): Environment - 'dev' or 'prod' (default: 'dev')

**Example Request:**
```bash
curl "http://localhost:8000/api/v1/coupon-scanner?store=amazon&website=coupert&env=dev"
```

**Example Response:**
```json
{
  "message": "Fetch coupons successfully",
  "result": ["SAVE20", "WELCOME10", "DEAL15"]
}
```

### 4. Collect Coupons

Collect coupons from websites using provided links.

**Endpoint:** `GET /api/v1/collect-coupons`

**Query Parameters:**
- `website` (required): Website name
- `links` (required): Comma-separated list of URLs

**Example Request:**
```bash
curl "http://localhost:8000/api/v1/collect-coupons?website=coupert&links=https://www.coupert.com/store/amazon,https://www.coupert.com/store/ebay"
```

**Example Response:**
```json
{
  "message": "Fetch coupons successfully",
  "result": ["SAVE20", "WELCOME10"]
}
```

### 5. Collect Links

Collect store links from various coupon websites. This endpoint searches multiple coupon websites to find store-specific URLs.

**Endpoint:** `GET /api/v1/collect-links`

**Query Parameters:**
- `domain` (required): Store domain name (e.g., 'amazon.com')
- `handle` (required): Store handle name
- `query` (required): Comma-separated search query terms
- `page` (optional): Page number (1-10, default: 1). Different pages query different groups of coupon websites.

**Example Request:**
```bash
curl "http://localhost:8000/api/v1/collect-links?domain=amazon.com&handle=amazon&query=amazon,amazon.com&page=1"
```

**Example Response:**
```json
{
  "message": "Collect links successfully",
  "result": {
    "amazon.com": {
      "coupert": ["https://www.coupert.com/store/amazon"],
      "rakuten": ["https://www.rakuten.com/amazon"]
    }
  }
}
```

## API Documentation

FastAPI automatically generates interactive API documentation:

- **Swagger UI:** `http://localhost:8000/docs`
- **ReDoc:** `http://localhost:8000/redoc`

## Environment Configuration

The application supports dynamic environment switching:

- **Development (dev):** Uses `DEV_DB_*` environment variables
- **Production (prod):** Uses `PROD_DB_*` environment variables

The environment can be set in two ways:
1. Set `ENV` variable in `.env` file (default for the entire application)
2. Pass `env` query parameter to endpoints (overrides default for that request)

## Database Setup

Ensure your PostgreSQL databases are set up with the required tables:

- `public.stores` - Store information
- `public.scanner_website` - Scanner website information
- `public.scanner_coupon` - Coupon scanner data

## Supported Websites

The following websites are supported for coupon fetching:

- coupert, discountime, karma, rakuten, hotdeals, slickdeals
- capitalone, retailmenot, honey, rebates, coupons
- dealcatcher, dontpayfull, couponbirds, offers, savings
- dealspotr, promocodes, couponchief, goodshop, simplycodes
- tenereteam, couponcause, goodsearch, valuecom, lovecoupons
- knoji, swagbucks, joinsmarty, rebatesme, refermate
- discountreactor, couponbox, dealdrop, revounts, dazzdeals
- greenpromocode, couponbind, deala, promopro

## Logging

Logging is configured via the `LOG_LEVEL` environment variable. Supported levels:
- DEBUG
- INFO (default)
- WARNING
- ERROR
- CRITICAL

## Docker Support

The project includes Docker setup scripts for both development and production environments:

### Development

```bash
chmod +x docker-base.dev.sh
./docker-base.dev.sh
```

### Production

```bash
chmod +x docker-base.prod.sh
./docker-base.prod.sh
```

## Troubleshooting

### Database Connection Issues

1. Verify database credentials in `.env` file
2. Ensure PostgreSQL is running and accessible
3. Check firewall rules if connecting to remote database
4. Verify database name, user, and password are correct
5. Ensure the `ENV` variable matches the environment you're trying to connect to (dev/prod)

### Import Errors

If you encounter import errors, ensure:
1. Virtual environment is activated
2. All dependencies are installed: `pip install -r requirements.txt`
3. You're running from the project root directory
4. Python version is 3.8 or higher: `python3 --version`

### Missing Environment Variables

If you get errors about missing environment variables:
1. Ensure `.env` file exists in the project root
2. Verify all required variables are set (especially `ZENROWS_API_KEY`)
3. Check that variable names match exactly (case-sensitive)
4. Restart the application after updating `.env` file

### Port Already in Use

If port 8000 is already in use:
```bash
# Use a different port
uvicorn main:app --host 0.0.0.0 --port 8001
```

## Development

### Running Tests

```bash
# Install test dependencies
pip install pytest pytest-asyncio httpx

# Run tests
pytest
```

### Code Formatting

```bash
# Install formatting tools
pip install black isort

# Format code
black .
isort .
```

## Deployment

For production deployment:

1. Set `ENV=prod` in your production `.env` file
2. Use a production WSGI server like Gunicorn:
   ```bash
   pip install gunicorn
   gunicorn main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
   ```
3. Use a reverse proxy (nginx) for SSL termination
4. Set up proper logging and monitoring
5. Configure environment variables securely (use secrets management)

## License

This project is part of the Coupon Scanner system.

## Support

For issues or questions, please refer to the main project documentation or contact the development team.
