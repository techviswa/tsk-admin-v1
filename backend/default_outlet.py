"""Idempotent automatic outlet creation using MongoDB's unique document ID."""
from pymongo.errors import DuplicateKeyError


async def create_default_outlet_once(collection, document):
    key = f"default-outlet:{document['business_id']}"
    created = False
    try:
        result = await collection.update_one(
            {"_id": key}, {"$setOnInsert": document}, upsert=True)
        created = result.upserted_id is not None
    except DuplicateKeyError:
        # A concurrent provisioning attempt won the same document identity.
        pass
    persisted = await collection.find_one({"_id": key}, {"_id": 0})
    if not persisted or persisted.get("business_id") != document["business_id"]:
        raise RuntimeError("Default outlet creation could not be verified")
    return persisted, created
