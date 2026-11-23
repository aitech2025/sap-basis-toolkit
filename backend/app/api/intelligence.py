"""Intelligent operations analytics API."""
from fastapi import APIRouter, Depends, Query

from ..auth import authorize
from ..services import intelligence, root_cause
from ..services.cache import ttl_cached

router = APIRouter(prefix="/api/v1/intelligence", dependencies=[Depends(authorize)])


@router.get("/availability")
def observed_availability(hours: int = Query(24, ge=1, le=24 * 30),
                           system_id: int | None = Query(None, ge=1),
                           landscape: str | None = Query(None, min_length=1, max_length=80)):
    return ttl_cached(intelligence.availability)(hours, system_id, landscape)


@router.get("/anomalies")
def metric_anomalies(hours: int = Query(24, ge=1, le=24),
                     system_id: int | None = Query(None, ge=1),
                     landscape: str | None = Query(None, min_length=1, max_length=80)):
    return ttl_cached(intelligence.anomalies)(hours, system_id, landscape)


@router.get("/capacity-forecasts")
def capacity_forecasts(horizon_days: int = Query(30, ge=1, le=365),
                       threshold_pct: float = Query(85, gt=0, le=100),
                       system_id: int | None = Query(None, ge=1),
                       landscape: str | None = Query(None, min_length=1, max_length=80)):
    return ttl_cached(intelligence.capacity_forecasts)(horizon_days, threshold_pct, system_id, landscape)


@router.get("/incident-correlations")
def incident_correlations(hours: int = Query(24, ge=1, le=24 * 30),
                          system_id: int | None = Query(None, ge=1),
                          landscape: str | None = Query(None, min_length=1, max_length=80)):
    return ttl_cached(intelligence.correlations)(hours, system_id, landscape)



@router.get("/root-cause/{incident_id}")
def root_cause_analysis(incident_id: int):
    """Return ranked, explainable diagnostic hypotheses for one incident."""
    try:
        return root_cause.analyze(incident_id)
    except LookupError as exc:
        from fastapi import HTTPException
        raise HTTPException(404, str(exc))
