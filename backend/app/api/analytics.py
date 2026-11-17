"""Historical telemetry analytics API."""
from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import authorize
from ..services import analytics

router = APIRouter(prefix="/api/v1/analytics", dependencies=[Depends(authorize)])


@router.get("/metrics")
def metric_catalogue():
    return analytics.catalogue()


@router.get("/summary")
def analytics_summary(hours: int = Query(24, ge=1, le=24 * 30),
                      landscape: str | None = Query(None, min_length=1, max_length=80)):
    return analytics.summary(hours, landscape)


@router.get("/series")
def analytics_series(metric: str = Query(..., min_length=1, max_length=80),
                     hours: int = Query(24, ge=1, le=24 * 30),
                     bucket_seconds: int = Query(300, ge=60, le=86400),
                     aggregation: str = Query("avg", pattern="^(avg|min|max|last)$"),
                     system_id: int | None = Query(None, ge=1),
                     landscape: str | None = Query(None, min_length=1, max_length=80)):
    try:
        return analytics.series(metric, hours, bucket_seconds, aggregation, system_id, landscape)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
