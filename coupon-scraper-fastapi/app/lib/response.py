from fastapi.responses import JSONResponse
from typing import Any, Optional

def success_response(message: str, result: Any, status_code: int = 200):
    """Return a success response"""
    return JSONResponse(
        status_code=status_code,
        content={
            "message": message,
            "result": result,
        }
    )

def error_response(message: str, status_code: int = 400):
    """Return an error response"""
    return JSONResponse(
        status_code=status_code,
        content={
            "message": message,
        }
    )

def client_error_response(message: str):
    """Return a 400 client error response"""
    return error_response(message, 400)

def not_found_response(message: str):
    """Return a 404 not found response"""
    return error_response(message, 404)

def internal_server_error_response(message: str):
    """Return a 500 internal server error response"""
    return error_response(message, 500)

def unauthorized_response(message: str):
    """Return a 401 unauthorized response"""
    return error_response(message, 401)
