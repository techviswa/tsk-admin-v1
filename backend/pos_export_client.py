"""Wait for durable POS snapshots without treating HTTP 202 as an empty export."""
import time
from fastapi import HTTPException


def await_export(response, send, params, *, seconds=270, sleep=time.sleep, now=time.monotonic):
    deadline = now() + seconds
    identity = None
    while response.status_code == 202:
        payload = response.json()
        snapshot = payload.get('snapshot') or {}
        current = (payload.get('resource'), payload.get('business_id'), payload.get('tenant_id'), snapshot.get('id'))
        if not isinstance(current[3], str) or not current[3].startswith('export-') or len(current[3]) > 100 or not all(current[:3]):
            raise HTTPException(502, 'POS returned an invalid export job acknowledgement')
        if identity is not None and current != identity:
            raise HTTPException(502, 'POS changed export job scope while preparing a snapshot')
        identity = current
        if now() >= deadline:
            raise HTTPException(503, {'code': 'POS_EXPORT_PENDING', 'message': 'POS is still preparing the export. Its durable job remains recoverable; retry shortly.'})
        params.update(consistent='true', export_id=current[3])
        sleep(min(2, max(.1, float(snapshot.get('retry_after') or 1))))
        response = send(params)
    if identity and response.status_code == 200:
        payload = response.json()
        result_identity = (payload.get('resource'), payload.get('business_id'), payload.get('tenant_id'), (payload.get('meta', {}).get('snapshot') or {}).get('id'))
        if result_identity != identity:
            raise HTTPException(502, 'POS completed a different export job')
    return response
