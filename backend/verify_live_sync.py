"""Production export checks using the configured admin login.

Run from the repository root. Does not print credentials or exported records.
Successful reads do not prove write propagation or outage recovery.
--sync explicitly imports the seven selected resources into AdminCore.
"""
import json
import argparse
import time
from pathlib import Path

import requests
from dotenv import dotenv_values


BASE_URL = "https://tsk-admin-v1.onrender.com"
RESOURCES = ["products", "bills", "payments", "customers", "inventory", "reports", "staff-shifts"]


def report(**values):
    print(json.dumps(values), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stored-only", action="store_true", help="Read AdminCore counts without requesting POS exports")
    parser.add_argument("--sync", action="store_true", help="Import selected resources into AdminCore, then check stored counts")
    args = parser.parse_args()
    config = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    if not config.get("ADMIN_EMAIL") or not config.get("ADMIN_PASSWORD"):
        report(stage="login", error="Missing local admin credentials")
        return 1
    with requests.Session() as session:
        response = session.post(f"{BASE_URL}/api/auth/login", json={
            "email": config["ADMIN_EMAIL"], "password": config["ADMIN_PASSWORD"],
        }, timeout=30, allow_redirects=False)
        report(stage="login", http=response.status_code)
        if response.status_code != 200:
            return 1
        token = response.json().get("access_token")
        if token:
            session.headers["Authorization"] = f"Bearer {token}"
        configuration = session.get(f"{BASE_URL}/api/pos-bridge/config", timeout=30)
        if configuration.status_code == 200:
            bridge = configuration.json()
            report(stage="bridge", base_url=bridge.get("base_url"), api_key_configured=bridge.get("api_key_configured"))
        response = session.get(f"{BASE_URL}/api/businesses", timeout=30)
        report(stage="businesses", http=response.status_code)
        if response.status_code != 200:
            return 1
        businesses = response.json()
        if not isinstance(businesses, list):
            report(stage="businesses", error="Unexpected response format")
            return 1
        linked = [row for row in businesses if row.get("pos_external_id") and row.get("pos_tenant_id")]
        report(stage="businesses", total=len(businesses), linked=len(linked))
        failures = int(len(linked) < 2)
        for business in linked[:2]:
            business_id = business["id"]
            for resource in ([] if args.stored_only else RESOURCES):
                try:
                    endpoint = "sync" if args.sync else "proxy"
                    response = session.request("POST" if args.sync else "GET", f"{BASE_URL}/api/pos-bridge/{endpoint}/{resource}",
                                               params={"business_id": business_id}, timeout=55)
                    result = {"business_id": business_id, "resource": resource, "http": response.status_code}
                    if response.status_code == 200 and args.sync:
                        payload = response.json()
                        result.update(status=payload.get("status"), imported=payload.get("count"), errors=payload.get("error_count"))
                        failures += int(payload.get("status") != "success" or bool(payload.get("error_count")))
                        if payload.get("errors"):
                            result["first_error"] = str(payload["errors"][0].get("reason", "Import failed"))[:200]
                    elif response.status_code == 200:
                        rows = response.json().get("rows")
                        if not isinstance(rows, list):
                            raise ValueError("Export omitted rows")
                        mismatches = sum(
                            str(row.get("business_id") or row.get("businessId") or row.get("pos_business_id") or "") != str(business["pos_external_id"])
                            or str(row.get("tenant_id") or row.get("tenantId") or row.get("pos_tenant_id") or "") != str(business["pos_tenant_id"])
                            for row in rows
                        )
                        result.update(rows=len(rows), scope_mismatches=mismatches)
                        failures += int(bool(mismatches))
                    else:
                        failures += 1
                        try:
                            detail = response.json().get("detail", {})
                            if isinstance(detail, dict):
                                result.update({key: detail[key] for key in ["code", "retry_after_seconds", "resource", "endpoint"] if key in detail})
                        except ValueError:
                            pass
                    report(**result)
                    if response.status_code == 429:
                        report(stage="stopped", reason="Rate limited; no further requests sent")
                        return 1
                except (requests.RequestException, ValueError) as exc:
                    failures += 1
                    report(business_id=business_id, resource=resource, error=type(exc).__name__)
                time.sleep(1)
            response = session.get(f"{BASE_URL}/api/pos-bridge/resources", params={"business_id": business_id}, timeout=50)
            if response.status_code != 200:
                report(stage="stored_counts", business_id=business_id, http=response.status_code)
                failures += 1
            else:
                for row in response.json():
                    if row.get("key") in RESOURCES:
                        report(stage="stored_counts", business_id=business_id, resource=row["key"],
                               local_count=row.get("local_count"), last_sync_status=(row.get("last_sync") or {}).get("status"))
        report(stage="finished", failures=failures, checked_businesses=min(2, len(linked)))
        return int(bool(failures))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (requests.RequestException, ValueError) as exc:
        report(stage="failed", error=type(exc).__name__)
        raise SystemExit(1)
