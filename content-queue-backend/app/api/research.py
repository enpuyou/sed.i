"""
Research brief endpoints.

GET /research/{run_id} — poll the status and result of a research run
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_active_user
from app.models.research import ResearchRun
from app.models.user import User

router = APIRouter(prefix="/research", tags=["research"])


@router.get("/{run_id}")
def get_research_run(
    run_id: uuid.UUID,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    run = (
        db.query(ResearchRun)
        .filter(ResearchRun.id == run_id, ResearchRun.user_id == current_user.id)
        .first()
    )
    if not run:
        raise HTTPException(status_code=404, detail="Research run not found.")
    return {
        "status": run.status,
        "result": run.result,
        "cost": run.cost,
        "error": run.error,
        "progress": {
            "iteration": run.iteration_count,
            "sub_questions": run.sub_questions or [],
            "searches_run_count": len(run.searches_run or []),
        },
    }
