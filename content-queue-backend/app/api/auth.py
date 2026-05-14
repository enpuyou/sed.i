"""
Authentication endpoints.

Handles registration, login (JWT), email verification, and password reset.
JWTs expire after ACCESS_TOKEN_EXPIRE_MINUTES (default 7 days). Registration
seeds onboarding content for new users.
"""

from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.database import get_db
from app.core.security import (
    verify_password,
    get_password_hash,
    create_access_token,
    create_refresh_token,
    hash_token,
    refresh_token_expires,
)
from app.core.deps import get_current_active_user
from app.models.user import User
from app.models.refresh_token import RefreshToken
from app.schemas.user import (
    UserCreate,
    UserResponse,
    Token,
    RefreshRequest,
    LogoutRequest,
    UserUpdate,
    DeleteAccountRequest,
)
from app.models.content import ContentItem
from app.models.highlight import Highlight
from app.models.vinyl import VinylRecord
from app.tasks.extraction import extract_metadata
from app.tasks.discogs import fetch_discogs_metadata

import secrets
from datetime import datetime, timezone
from app.models.token import VerificationToken
from app.tasks.email import send_verification_email_task, send_password_reset_email_task
from app.schemas.user import ForgotPasswordRequest, ResetPasswordRequest, GenericMessage
import posthog
import hashlib

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED
)
def register(user_data: UserCreate, db: Session = Depends(get_db)):
    """
    Register a new user.

    - Checks if email is already taken
    - Hashes the password
    - Saves user to database
    """
    # Check if user already exists
    existing_user = db.query(User).filter(User.email == user_data.email).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered"
        )

    if db.query(User).filter(User.username == user_data.username).first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered",
        )

    # Hash the password
    hashed_password = get_password_hash(user_data.password)

    # Create new user
    new_user = User(
        email=user_data.email,
        username=user_data.username,
        hashed_password=hashed_password,
        full_name=user_data.full_name,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)  # Get the auto-generated ID and timestamps

    # Generate Verification Token
    token_str = secrets.token_urlsafe(32)
    verify_token = VerificationToken(
        user_id=new_user.id,
        token=token_str,
        token_type="email_verification",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    db.add(verify_token)
    db.commit()

    # NOTE: email is sent after the token commit but before onboarding content
    # is created below. This is acceptable — verification works independently.
    send_verification_email_task.delay(new_user.email, token_str)

    # ---------------------------------------------------------
    # Create Default "User Guide" Article
    # ---------------------------------------------------------

    # Define the static HTML content
    guide_html = """
    <h1>Welcome to sed.i</h1>
    <p><b>sed.i</b> is your calm, personal space for reading and thinking. This guide demonstrates how to use your new content queue effectively.</p>

    <h2>1. Smart Highlighting</h2>
    <p>Reading is active. Select any text to highlight it. You can choose from multiple colors to categorize your thoughts as you see fit. All your highlights are saved automatically and can be reviewed in the Highlights Panel (press <b>H</b>).</p>
    <p>Try highlighting a sentence below to see it in action!</p>

    <h2>2. Organization with Lists</h2>
    <p>Don't let your reading pile up. Create <b>Lists</b> to organize content by topic, project, or mood. You can find your lists in the sidebar or via the "Lists" menu on mobile.</p>

    <h2>3. A Distraction-Free, Customizable Desktop</h2>
    <p>We strip away ads, popups, and clutter. You can customize your entire reading experience—including fonts, text sizing, theme, and layout spacing—using the Settings menu in the top right. This is <b>your</b> space.</p>

    <h2>4. Public Profiles</h2>
    <p>Share your favorite content with the world. Claim a username in your Settings to enable your public profile, then toggle any article or vinyl record to "Public" so others can see it!</p>

    <h3>Ready to start?</h3>
    <p>Add your first article by pasting a URL above, or install our <a href="https://chromewebstore.google.com/detail/sedi/doojneiapaegndmglponeacdbcgaojnm" target="_blank">Chrome extension</a> to save content with one click.</p>
    """

    # Create the ContentItem
    guide_content = ContentItem(
        user_id=new_user.id,
        original_url="https://sed.i/welcome",  # Virtual URL
        title="Getting Started with sed.i",
        description="A quick guide to highlighting, organizing, and enjoying your new reading queue.",
        content_type="article",
        full_text=guide_html,
        reading_time_minutes=2,
        word_count=len(guide_html.split()),
        processing_status="completed",  # No extraction needed
        submitted_via="system_welcome",
    )
    db.add(guide_content)
    db.commit()
    db.refresh(guide_content)

    # ---------------------------------------------------------
    # Programmatic Highlights (Demo)
    # ---------------------------------------------------------

    # Helper to find offsets (simple string search in the HTML)
    # Note: frontend highlighting currently works on the raw text content of nodes,
    # but for simplicity here we assume the backend stores HTML and frontend renders it.
    # The HighlightRenderer matches text content.
    # FOR NOW: Let's simpler create a highlight on a specific unique phrase.

    # We'll highlight "Select any text to highlight it"
    target_phrase_1 = "Select any text to highlight it"
    start_1 = guide_html.find(target_phrase_1)

    if start_1 != -1:
        hl_1 = Highlight(
            user_id=new_user.id,
            content_item_id=guide_content.id,
            text=target_phrase_1,
            start_offset=start_1,
            end_offset=start_1 + len(target_phrase_1),
            color="yellow",
            note="Welcome to your first highlight! You can add notes like this one.",
        )
        db.add(hl_1)

    # Highlight "Create Lists"
    target_phrase_2 = "Create Lists"
    start_2 = guide_html.find(target_phrase_2)

    if start_2 != -1:
        hl_2 = Highlight(
            user_id=new_user.id,
            content_item_id=guide_content.id,
            text=target_phrase_2,
            start_offset=start_2,
            end_offset=start_2 + len(target_phrase_2),
            color="green",
            note="Pro tip: Use lists to group related research.",
        )
        db.add(hl_2)

    db.commit()

    # ---------------------------------------------------------
    # Add Example Article: TextEdit and the Relief of Simple Software
    # ---------------------------------------------------------
    example_article = ContentItem(
        user_id=new_user.id,
        original_url="https://www.newyorker.com/culture/infinite-scroll/textedit-and-the-relief-of-simple-software",
        title="TextEdit and the Relief of Simple Software",
        description="A reflection on the virtues of minimalist software and focused tools.",
        content_type="article",
        processing_status="pending",  # Will be extracted by background task
        submitted_via="system_default",
    )
    db.add(example_article)
    db.commit()
    db.refresh(example_article)

    # Trigger background extraction for the example article
    extract_metadata.delay(str(example_article.id))

    # ---------------------------------------------------------
    # Add Example Article: Why I Finally Quit Spotify (New Yorker)
    # ---------------------------------------------------------
    spotify_article = ContentItem(
        user_id=new_user.id,
        original_url="https://www.newyorker.com/culture/infinite-scroll/why-i-finally-quit-spotify",
        title="Why I Finally Quit Spotify",
        description="The streaming service’s “smart shuffle” feature broke me.",
        content_type="article",
        processing_status="pending",
        submitted_via="system_default",
    )
    db.add(spotify_article)
    db.commit()
    db.refresh(spotify_article)

    # Trigger background extraction for the spotify article
    extract_metadata.delay(str(spotify_article.id))

    # ---------------------------------------------------------
    # Add Default Vinyl Record: Hiroshi Yoshimura – A·I·R
    # ---------------------------------------------------------
    default_vinyl = VinylRecord(
        user_id=new_user.id,
        discogs_url="https://www.discogs.com/release/33729033-Hiroshi-Yoshimura-AIR-Air-In-Resort",
        processing_status="pending",
    )
    db.add(default_vinyl)
    db.commit()
    db.refresh(default_vinyl)

    # Trigger background Discogs metadata fetch
    fetch_discogs_metadata.delay(str(default_vinyl.id))

    try:
        email_hash = hashlib.sha256(new_user.email.encode()).hexdigest()
        username_hash = hashlib.sha256(new_user.username.encode()).hexdigest()
        posthog.capture(
            str(new_user.id),
            "user_signed_up",
            properties={"email_hash": email_hash, "username_hash": username_hash},
        )
    except Exception:
        pass

    return new_user


@router.post("/login", response_model=Token)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)
):
    """
    Login and get a JWT token.

    - Verifies email and password
    - Returns access token
    """
    # Find user by email
    user = db.query(User).filter(User.email == form_data.username).first()

    # Verify password
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(
        data={"sub": user.email},
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    raw_refresh, token_hash = create_refresh_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=refresh_token_expires(),
        )
    )
    db.commit()

    try:
        posthog.capture(str(user.id), "user_logged_in")
    except Exception:
        pass

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "refresh_token": raw_refresh,
    }


@router.post("/refresh", response_model=Token)
def refresh_token_endpoint(body: RefreshRequest, db: Session = Depends(get_db)):
    """
    Exchange a valid refresh token for a new access token + rotated refresh token.

    The old refresh token is immediately revoked. If the token is already revoked
    (possible theft replay), all refresh tokens for that user are revoked.
    """
    token_hash = hash_token(body.refresh_token)
    record = (
        db.query(RefreshToken).filter(RefreshToken.token_hash == token_hash).first()
    )

    if not record:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
        )

    # Detect theft: token was already rotated — revoke all user tokens
    if record.revoked_at is not None:
        db.query(RefreshToken).filter(
            RefreshToken.user_id == record.user_id,
            RefreshToken.revoked_at.is_(None),
        ).update({"revoked_at": datetime.now(timezone.utc)})
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token already used",
        )

    if datetime.now(timezone.utc) > record.expires_at.astimezone(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token expired"
        )

    user = db.query(User).filter(User.id == record.user_id).first()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
        )

    # Rotate: revoke old, issue new pair
    record.revoked_at = datetime.now(timezone.utc)
    new_access = create_access_token(
        data={"sub": user.email},
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    raw_refresh, new_hash = create_refresh_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=new_hash,
            expires_at=refresh_token_expires(),
        )
    )
    db.commit()

    return {
        "access_token": new_access,
        "token_type": "bearer",
        "refresh_token": raw_refresh,
    }


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(body: LogoutRequest, db: Session = Depends(get_db)):
    """Revoke a refresh token server-side. No-op if token is absent or already revoked."""
    if not body.refresh_token:
        return
    token_hash = hash_token(body.refresh_token)
    record = (
        db.query(RefreshToken).filter(RefreshToken.token_hash == token_hash).first()
    )
    if record and record.revoked_at is None:
        record.revoked_at = datetime.now(timezone.utc)
        db.commit()


@router.get("/verify-email", response_model=GenericMessage)
def verify_email(token: str, db: Session = Depends(get_db)):
    """Verifies a user's email with the token sent to them."""
    verification_token = (
        db.query(VerificationToken)
        .filter(
            VerificationToken.token == token,
            VerificationToken.token_type == "email_verification",
        )
        .first()
    )

    if not verification_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid token"
        )

    if verification_token.is_used:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Token already used"
        )

    if datetime.now(timezone.utc) > verification_token.expires_at.astimezone(
        timezone.utc
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Token expired"
        )

    user = verification_token.user
    user.is_verified = True
    verification_token.is_used = True

    db.add(user)
    db.add(verification_token)
    db.commit()

    return {"message": "Email successfully verified!"}


@router.post("/forgot-password", response_model=GenericMessage)
def forgot_password(request: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """Sends a password reset email if the user exists."""
    user = db.query(User).filter(User.email == request.email).first()

    if user:
        # Invalidate any existing unused reset tokens for this user
        db.query(VerificationToken).filter(
            VerificationToken.user_id == user.id,
            VerificationToken.token_type == "password_reset",
            VerificationToken.is_used == False,  # noqa: E712
        ).update({"is_used": True})

        token_str = secrets.token_urlsafe(32)
        reset_token = VerificationToken(
            user_id=user.id,
            token=token_str,
            token_type="password_reset",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        db.add(reset_token)
        db.commit()
        send_password_reset_email_task.delay(user.email, token_str)

    return {
        "message": "If an account with that email exists, a password reset link has been sent."
    }


@router.post("/reset-password", response_model=GenericMessage)
def reset_password(request: ResetPasswordRequest, db: Session = Depends(get_db)):
    """Resets user's password using the token."""
    reset_token = (
        db.query(VerificationToken)
        .filter(
            VerificationToken.token == request.token,
            VerificationToken.token_type == "password_reset",
        )
        .first()
    )

    if not reset_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid token"
        )

    if reset_token.is_used:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Token already used"
        )

    if datetime.now(timezone.utc) > reset_token.expires_at.astimezone(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Token expired"
        )

    user = reset_token.user
    user.hashed_password = get_password_hash(request.new_password)
    reset_token.is_used = True

    db.add(user)
    db.add(reset_token)
    db.commit()

    return {"message": "Password successfully reset."}


@router.get("/me", response_model=UserResponse)
def get_current_user_info(current_user: User = Depends(get_current_active_user)):
    """
    Get current logged-in user's information.

    This route is PROTECTED - requires valid JWT token.
    """
    return current_user


@router.put("/me", response_model=UserResponse)
def update_current_user_info(
    user_update: UserUpdate,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    Update current logged-in user's profile information.
    """
    # Check if someone is trying to take an existing username
    if (
        user_update.username is not None
        and user_update.username != current_user.username
    ):
        existing = db.query(User).filter(User.username == user_update.username).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username is already taken",
            )
        current_user.username = user_update.username

    if user_update.full_name is not None:
        current_user.full_name = user_update.full_name

    if user_update.is_public is not None:
        current_user.is_public = user_update.is_public

    if user_update.is_queue_public is not None:
        current_user.is_queue_public = user_update.is_queue_public

    if user_update.is_crates_public is not None:
        current_user.is_crates_public = user_update.is_crates_public

    db.add(current_user)
    db.commit()
    db.refresh(current_user)

    return current_user


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
def delete_current_user(
    body: DeleteAccountRequest,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """
    Permanently delete the authenticated user's account and all associated data.
    Requires password confirmation.
    """
    if not verify_password(body.password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incorrect password.",
        )
    try:
        posthog.capture(str(current_user.id), "account_deleted")
    except Exception:
        pass
    db.delete(current_user)
    db.commit()
