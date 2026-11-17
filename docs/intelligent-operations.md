# Intelligent operations analytics

Sentinel adds an explainable operations-intelligence layer over observed telemetry. It is deliberately transparent and does not claim autonomous root-cause analysis or predictive certainty.

## Endpoints

All endpoints require the existing API authentication.

- `GET /api/v1/intelligence/availability?hours=24&landscape=Finance`
- `GET /api/v1/intelligence/anomalies?hours=24&landscape=Finance`
- `GET /api/v1/intelligence/capacity-forecasts?horizon_days=30&threshold_pct=85&landscape=Finance`
- `GET /api/v1/intelligence/incident-correlations?hours=24&landscape=Finance`

## Availability measurement

Availability is computed as:

`up_samples / (up_samples + down_samples) * 100`

Only recorded `availability.system_up` samples are counted. Unobserved time is not treated as uptime or downtime. Maintenance windows overlapping the requested period are reported separately and are not subtracted. This is an observed-sample ratio, not a wall-clock SLA. A contractual SLA requires a defined probe cadence, missing-data policy, maintenance policy and measurement source.

## Anomaly detection

- Baseline: preceding seven days, excluding the recent analysis window.
- Recent window: at most 24 hours.
- Minimum baseline: 12 samples per system and metric.
- Robust score: median absolute deviation (MAD), scaled by 1.4826; standard deviation fallback is used only when MAD is zero.
- Signal threshold: absolute robust score >= 3.5; >= 6 is labelled critical.
- Results include baseline median, latest value, direction, sample counts and method.

Signals are investigation leads, not confirmed incidents. Detection is limited by collector sampling frequency and retention. A metric with insufficient baseline history is omitted rather than assigned a synthetic score.

## Capacity forecasting

Disk and memory forecasts use the last 90 days of `capacity_snapshots` and a least-squares linear trend. At least three snapshots spanning time are required. The API returns daily change, projected utilisation at the selected horizon and estimated time to a configurable utilisation threshold. Flat/declining trends do not invent an exhaustion date. This is a directional trend projection, not a physical capacity simulation or guaranteed date.

## Incident correlation

For incidents opened within the selected time window, Sentinel compares metric observations in the 30 minutes before and after opening. It returns per-metric sample counts, before/after averages and change. Results are ordered by absolute observed change.

Temporal association is not proof of causation. The output does not assert a root cause; it provides evidence for an operator to investigate. Incidents without an associated SAP system or without nearby samples have no metric evidence.

## Chart overlays

Monitoring Analytics overlays incident markers on a metric series when the incident's system and metric match. Markers represent incident opening timestamps; they do not indicate that the incident caused the metric movement.

## Operational guardrails

- All endpoints are authenticated.
- Query windows and row counts are bounded.
- No telemetry is generated, interpolated or filled in.
- Maintenance periods are disclosed, not silently removed.
- Forecast and anomaly calculations are deterministic and inspectable.
- For high-volume environments, consider rollups/continuous aggregates and configurable retention after measuring real workload.

## Root cause analysis

`GET /api/v1/intelligence/root-cause/{incident_id}` returns a read-only diagnostic report for an incident.

The initial engine correlates the incident's registered SAP system telemetry within ±30 minutes of incident opening. It groups threshold breaches and worsening metric movements into HANA, ABAP, Interfaces, Availability and Connectivity hypotheses. Evidence includes the observed value, investigation threshold, before/after averages, sample counts, timestamp and whether a metric was the incident's original trigger.

Hypotheses receive a deterministic evidence score capped at 10. A primary incident metric is discounted so the triggering alert is not incorrectly counted as independent corroboration. Confidence labels describe evidence strength and signal coverage; they are not probabilities.

The report also includes monitor snapshot metadata, collection coverage, data truncation indicators, limitations and operator investigation steps. Raw monitor payloads, usernames, credentials and other sensitive details are not exposed by this endpoint.

### Current limits

- This is a rules-and-evidence ranking engine, not causal inference or an ML model.
- It correlates only the incident's assigned SAP system. Shared HANA, network, storage and middleware dependencies are not inferred.
- OS CPU, network latency, RFC response-time and application dependency telemetry are not yet first-class collected signals.
- Snapshot evidence is summary-only and metrics without observations remain unknown.
- No remediation action is executed. Operators remain responsible for diagnosis and change approval.
