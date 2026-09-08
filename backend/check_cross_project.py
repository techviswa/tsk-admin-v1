"""Run with the POS cross-project test harness and an isolated local MongoDB."""
import asyncio
import os
import secrets

import httpx
import server


async def main():
    assert os.environ["DB_NAME"].startswith("admincore_cross_test_")
    assert os.environ["MONGO_URL"].startswith("mongodb://127.0.0.1:")
    prefix = os.environ["CROSS_PROJECT_PREFIX"]
    actor = {"id": "test-platform-admin", "name": "Test Admin", "email": "admin@example.test", "role": "platform_admin", "status": "active", "business_ids": []}
    await server.db.users.insert_one(actor)
    await server.sync_system_modules()
    headers = {"Authorization": "Bearer " + server.create_access_token(actor["id"], actor["email"])}
    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://admin.test", headers=headers, timeout=60) as admin:
        businesses = []
        for suffix in ["a", "b"]:
            password = secrets.token_urlsafe(18)
            email = f"{prefix}-{suffix}@example.test"
            response = await admin.post("/api/businesses", json={"name": f"{prefix}-{suffix}", "type": "restaurant", "plan": "starter", "owner_name": "Test Owner", "owner_email": email, "owner_password": password})
            assert response.status_code == 200, response.text
            business = response.json()
            assert business["operational_ready"] is False
            blocked = await admin.get(f"/api/outlets/business/{business['id']}")
            assert blocked.status_code == 409, blocked.text
            job = await server.db.pos_provisioning_jobs.find_one({"id": business["pos_provisioning_job"]["id"]})
            await server.run_pos_provisioning_job(job)
            ready = await server.db.businesses.find_one({"id": business["id"]})
            assert ready["pos_provisioning_status"] == "synced", ready.get("pos_provisioning_error")
            assert ready.get("pos_owner_id") and ready.get("pos_default_outlet_id")
            async with httpx.AsyncClient(base_url=server.POS_CORE_API_BASE_URL, timeout=30) as pos:
                login = await pos.post("/api/auth/login", json={"email": email, "password": password})
                assert login.status_code == 200, login.text
                owner = login.json()["data"]["user"]
                assert owner["business_id"] == ready["pos_external_id"], owner
                product = await pos.post("/api/products", json={"name": f"Exclusive {suffix}", "price": 30, "stock": 7, "category": "test"})
                assert product.status_code == 201, product.text
                sale = await pos.post("/api/billing", json={
                    "customer_name": f"Customer {suffix}",
                    "customer_phone": "9000000001" if suffix == "a" else "9000000002",
                    "outlet_id": ready["pos_default_outlet_id"],
                    "items": [{"product_id": product.json()["data"]["id"], "name": f"Exclusive {suffix}", "quantity": 1, "price": 30}],
                    "payment_type": "Cash",
                })
                assert sale.status_code == 201, sale.text
                sale_id = sale.json()["data"]["id"]
                local_owner_id = business["created_owner"]["id"]
                profile = await admin.put(f"/api/users/{local_owner_id}", json={"name": "Updated Owner"})
                assert profile.status_code == 200, profile.text
                changed_email = f"changed-{email}"
                changed_password = secrets.token_urlsafe(18)
                credentials = await admin.put(f"/api/users/{local_owner_id}", json={"email": changed_email, "password": changed_password})
                assert credentials.status_code == 200, credentials.text
                expired = await pos.get("/api/auth/session")
                assert expired.status_code == 200 and expired.json()["data"]["authenticated"] is False, expired.text
                denied = await pos.get("/api/products")
                assert denied.status_code == 401, denied.text
                changed_login = await pos.post("/api/auth/login", json={"email": changed_email, "password": changed_password})
                assert changed_login.status_code == 200, changed_login.text
                assert changed_login.json()["data"]["user"]["id"] == owner["id"]
                old_login = await pos.post("/api/auth/login", json={"email": email, "password": password})
                assert old_login.status_code == 401, old_login.text
            synced = await server.process_pos_change("products", business["id"])
            assert synced["status"] == "success", synced
            products = await admin.get("/api/products", params={"business_id": business["id"]})
            assert products.status_code == 200, products.text
            rows = products.json()
            assert [row["name"] for row in rows] == [f"Exclusive {suffix}"], rows
            sale_sync = await server.process_pos_change("bills", business["id"])
            assert sale_sync["status"] == "success", sale_sync
            bills = await server.db.pos_bills.find({"business_id": business["id"]}).to_list(100)
            assert len(bills) == 1 and bills[0]["pos_external_id"] == sale_id, bills
            for collection in ["pos_payments", "pos_customers", "pos_reports_analytics"]:
                mirrored = await server.db[collection].find({"business_id": business["id"]}).to_list(100)
                assert mirrored, collection
                assert all(row["pos_business_id"] == ready["pos_external_id"] for row in mirrored), mirrored
            businesses.append(business["id"])
        first = await server.db.products.find({"business_id": businesses[0]}).to_list(100)
        second = await server.db.products.find({"business_id": businesses[1]}).to_list(100)
        assert {row["pos_external_id"] for row in first}.isdisjoint(row["pos_external_id"] for row in second)
        print("Cross-project provisioning, credentials, session revocation, product isolation and bill/dependent sync passed")


async def run():
    try:
        await main()
    finally:
        if os.environ.get("DB_NAME", "").startswith("admincore_cross_test_") and os.environ.get("MONGO_URL", "").startswith("mongodb://127.0.0.1:"):
            await server.client.drop_database(os.environ["DB_NAME"])
        server.client.close()


if __name__ == "__main__":
    asyncio.run(run())
