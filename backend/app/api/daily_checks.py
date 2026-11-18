"""Basis daily checks API: fleet matrix, CSV export and review sign-off."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..auth import authorize, require
from ..services import daily_checks

router = APIRouter(prefix="/api/v1/daily-checks", dependencies=[Depends(authorize)])


class ReviewIn(BaseModel):
    landscape: str | None = Field(default=None, max_length=80)
    comment: str | None = Field(default=None, max_length=2000)


@router.get("")
def daily_check_matrix(landscape: str | None = Query(None, max_length=80), environment: str | None = Query(None, max_length=10)):
    return daily_checks.matrix(landscape, environment)


@router.get("/export.csv")
def daily_check_csv(landscape: str | None = Query(None, max_length=80), environment: str | None = Query(None, max_length=10)):
    data = daily_checks.matrix(landscape, environment)
    name = f"basis-daily-checks-{data['generated_at'][:10]}.csv"
    return Response(daily_checks.to_csv(data), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/reviews", status_code=201, dependencies=[Depends(require("operator"))])
def daily_check_review(payload: ReviewIn, request: Request):
    """Sign off today's checks: stores the matrix as reviewed, with the reviewer and a comment."""
    return daily_checks.review(request.state.principal.username, payload.landscape, payload.comment)


@router.get("/reviews")
def daily_check_reviews(days: int = Query(30, ge=1, le=366)):
    return daily_checks.reviews(days)


@router.get("/reviews/{review_id}")
def daily_check_review_detail(review_id: int):
    try:
        return daily_checks.get_review(review_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
