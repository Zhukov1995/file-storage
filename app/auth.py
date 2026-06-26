import hmac
from fastapi import Header, HTTPException, status
from typing import Optional
from app.config import get_settings


def require_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    expected = get_settings().api_key
    # Reject missing key before compare_digest (which requires non-None str args)
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )
    # Use constant-time comparison to prevent timing attacks
    if not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )
