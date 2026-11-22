"""Publish immutable scientific pair results after their GridFS payloads exist."""
from hashlib import sha256
from io import BytesIO
import json

import gridfs
import numpy as np


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def digest(value):
    return sha256(canonical(value).encode()).hexdigest()


def encode_arrays(arrays):
    buffer = BytesIO()
    np.savez_compressed(buffer, **arrays)
    return buffer.getvalue()


class ResearchStore:
    def __init__(self, db):
        self.db = db
        self.fs = gridfs.GridFS(db, collection="sparse_oflow")

    def initialize(self, run_id, manifest):
        # Fail on authorization or incompatible identity before writing arrays.
        self.db.command("listCollections", nameOnly=True)
        self.db.pairs.create_index(
            [("run_id", 1), ("storm_id", 1), ("valid_time", 1)], unique=True
        )
        self.db.events.create_index([("run_id", 1), ("storm_id", 1)], unique=True)
        existing = self.db.runs.find_one({"_id": run_id})
        if existing and existing["manifest_sha256"] != digest(manifest):
            raise ValueError("Run identity conflicts with configuration or input snapshot")
        self.db.runs.update_one({"_id": run_id}, {"$setOnInsert": {
            "manifest": manifest, "manifest_sha256": digest(manifest),
            "status": "running",
        }}, upsert=True)

    def read(self, key):
        record = self.db.pairs.find_one(key)
        if record is None or record["outcome"] == "failed":
            return None
        if record.get("payload_id") is not None:
            raw = self.fs.get(record["payload_id"]).read()
            if sha256(raw).hexdigest() != record["payload_sha256"]:
                raise ValueError("Completed pair payload checksum mismatch")
            with np.load(BytesIO(raw), allow_pickle=False) as arrays:
                if sorted(arrays.files) != record["array_names"]:
                    raise ValueError("Completed pair array inventory mismatch")
        return record

    def publish(self, key, record, arrays):
        if self.db.runs.find_one({"_id": key["run_id"]})["status"] == "complete":
            raise ValueError("A complete run is immutable")
        if self.read(key) is not None:
            raise ValueError("Completed pair is immutable; resume it or use a new run")
        document = {**key, **record}
        if arrays is not None:
            raw = encode_arrays(arrays)
            checksum = sha256(raw).hexdigest()
            # Upload first: interrupted uploads can be orphaned, never published.
            payload_id = self.fs.put(raw, metadata={**key, "sha256": checksum,
                "schema_version": 1, "stage": "tracked_pre_decluster_pre_interpolation"})
            document.update(payload_id=payload_id, payload_sha256=checksum,
                            array_names=sorted(arrays))
        self.db.pairs.replace_one(key, document, upsert=True)

    def orphan_count(self, run_id):
        referenced = {r["payload_id"] for r in self.db.pairs.find(
            {"run_id": run_id, "payload_id": {"$exists": True}}, {"payload_id": 1})}
        return sum(r["_id"] not in referenced for r in self.db["sparse_oflow.files"].find(
            {"metadata.run_id": run_id}, {"_id": 1}))
