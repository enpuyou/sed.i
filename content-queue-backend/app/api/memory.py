"""
Memory endpoints.

GET  /memory/profile  — return the current user's consolidated memory profile
POST /memory/consolidate — trigger consolidation for the current user (async via Celery)
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_active_user
from app.models.memory import UserProfile
from app.models.user import User

router = APIRouter(prefix="/memory", tags=["memory"])


class ProfileResponse(BaseModel):
    current_focus: str | None
    reading_velocity: str | None
    memory_text: str | None
    last_consolidated: str | None

    model_config = {"from_attributes": True}


@router.get("/profile", response_model=ProfileResponse)
def get_profile(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    profile = (
        db.query(UserProfile).filter(UserProfile.user_id == current_user.id).first()
    )
    if profile is None:
        raise HTTPException(
            status_code=404, detail="No memory profile yet — consolidation has not run."
        )
    return ProfileResponse(
        current_focus=profile.current_focus,
        reading_velocity=(
            profile.reading_velocity.value if profile.reading_velocity else None
        ),
        memory_text=profile.memory_text,
        last_consolidated=(
            profile.last_consolidated.isoformat() if profile.last_consolidated else None
        ),
    )


@router.post("/consolidate", status_code=202)
def trigger_consolidation(
    current_user: User = Depends(get_current_active_user),
):
    from app.tasks.memory import consolidate_memory_task

    consolidate_memory_task.delay(str(current_user.id))
    return {"status": "queued", "user_id": str(current_user.id)}
