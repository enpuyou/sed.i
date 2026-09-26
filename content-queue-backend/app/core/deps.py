from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session
from app.core.auth_cookies import get_token_from_cookie
from app.core.config import settings
from app.core.database import get_db
from app.models.user import User
from app.schemas.user import TokenData

# This tells FastAPI where to find the login endpoint. auto_error=False so a
# missing Authorization header doesn't itself 401 — the cookie fallback below
# needs to run first (the browser frontend authenticates via cookie only,
# never sends a Bearer header).
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)


def get_current_user(
    request: Request,
    bearer_token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """
    Verify JWT token and return the current user.
    Used as a dependency for protected routes.

    Checks the Authorization: Bearer header first (browser extension, MCP
    OAuth clients), then falls back to the httpOnly auth cookie (Next.js
    frontend — see app/core/auth_cookies.py). Both paths decode the same JWT
    the same way; only where the token comes from differs.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    token = bearer_token or get_token_from_cookie(request)
    if token is None:
        raise credentials_exception

    try:
        # Decode the token
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
        token_data = TokenData(email=email)
    except JWTError:
        raise credentials_exception

    # Find user in database
    user = db.query(User).filter(User.email == token_data.email).first()
    if user is None:
        raise credentials_exception

    return user


def get_current_active_user(current_user: User = Depends(get_current_user)) -> User:
    """
    Check if user is active.
    Can be used for routes that require active account.
    """
    if not current_user.is_active:
        raise HTTPException(status_code=403, detail="Inactive user")
    return current_user
