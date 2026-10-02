"""
Crash Signature Generation and In-App Frame Analysis.

Extracts top in-app stack frames, computes deterministic 8-char SHA1 signatures,
and enforces a 200-signature cardinality ceiling for Prometheus label safety.
"""

import hashlib
import os
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Tuple


@dataclass
class CrashRecord:
    signature_id: str
    exception_type: str
    location: str
    endpoint: str
    message: str
    count: int
    first_seen: str
    last_seen: str
    sample_traceback: str


class CrashSignatureEngine:
    """
    Computes crash signatures and maintains an in-memory ring buffer of recent crashes.
    """

    MAX_SIGNATURES = 200

    def __init__(self):
        self._signatures: Dict[str, CrashRecord] = {}
        self._active_prometheus_signatures: Set[str] = set()

    def get_top_in_app_frame(self, exc: BaseException) -> Tuple[str, str, int]:
        """
        Walks the exception traceback and returns (relative_file_path, func_name, line_no)
        for the topmost frame residing inside the application/workers source code,
        skipping stdlib, site-packages, and test harnesses.
        """
        tb = exc.__traceback__
        frames = traceback.extract_tb(tb) if tb else []

        chosen_frame = None

        # Look from bottom up (most recent frame first)
        for frame in reversed(frames):
            filename = frame.filename.replace("\\", "/")
            if "site-packages" not in filename and ("/app/" in filename or "/workers/" in filename):
                chosen_frame = frame
                break

        # Fallback to the last frame if no in-app frame was matched
        if not chosen_frame and frames:
            chosen_frame = frames[-1]

        if chosen_frame:
            # Normalize to relative path
            fn = chosen_frame.filename.replace("\\", "/")
            app_idx = fn.find("/app/")
            if app_idx != -1:
                rel_path = fn[app_idx + 1 :]
            else:
                workers_idx = fn.find("/workers/")
                if workers_idx != -1:
                    rel_path = fn[workers_idx + 1 :]
                else:
                    rel_path = os.path.basename(fn)

            return rel_path, chosen_frame.name, chosen_frame.lineno

        return "unknown", "unknown", 0

    def compute_signature(
        self,
        exc: BaseException,
        endpoint: str = "unknown",
    ) -> Tuple[str, str, str]:
        """
        Generates deterministic (signature_id, location, prometheus_sig_id).

        Returns:
            signature_id: 8-char hex SHA1 hash
            location: 'path/file.py:function_name:lineno'
            prometheus_sig_id: signature_id if under cardinality cap (200), else 'other'
        """
        exc_type = type(exc).__name__
        rel_file, func_name, lineno = self.get_top_in_app_frame(exc)
        location = f"{rel_file}:{func_name}:{lineno}"

        raw_key = f"{exc_type}|{location}"
        signature_id = hashlib.sha256(raw_key.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]

        # Enforce cardinality ceiling for Prometheus labels
        if signature_id in self._active_prometheus_signatures:
            prom_sig = signature_id
        elif len(self._active_prometheus_signatures) < self.MAX_SIGNATURES:
            self._active_prometheus_signatures.add(signature_id)
            prom_sig = signature_id
        else:
            prom_sig = "other"

        return signature_id, location, prom_sig

    def record_crash(
        self,
        exc: BaseException,
        endpoint: str = "unknown",
        scrubbed_traceback: str = "",
    ) -> CrashRecord:
        """
        Records the crash in the in-memory ring buffer.
        """
        signature_id, location, _ = self.compute_signature(exc, endpoint)
        now_iso = datetime.now(timezone.utc).isoformat()
        exc_type = type(exc).__name__
        msg = str(exc)

        if signature_id in self._signatures:
            record = self._signatures[signature_id]
            record.count += 1
            record.last_seen = now_iso
            record.message = msg
            if scrubbed_traceback:
                record.sample_traceback = scrubbed_traceback
            return record

        record = CrashRecord(
            signature_id=signature_id,
            exception_type=exc_type,
            location=location,
            endpoint=endpoint,
            message=msg,
            count=1,
            first_seen=now_iso,
            last_seen=now_iso,
            sample_traceback=scrubbed_traceback or "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        )

        # Evict oldest if exceeding 200 items in memory
        if len(self._signatures) >= self.MAX_SIGNATURES:
            oldest_key = min(self._signatures.keys(), key=lambda k: self._signatures[k].last_seen)
            del self._signatures[oldest_key]

        self._signatures[signature_id] = record
        return record

    def get_all_records(self) -> List[Dict]:
        """Returns all recorded crash signatures as a list of dicts sorted by count descending."""
        records = list(self._signatures.values())
        records.sort(key=lambda r: r.count, reverse=True)
        return [
            {
                "signature_id": r.signature_id,
                "exception_type": r.exception_type,
                "location": r.location,
                "endpoint": r.endpoint,
                "message": r.message,
                "count": r.count,
                "first_seen": r.first_seen,
                "last_seen": r.last_seen,
                "sample_traceback": r.sample_traceback,
            }
            for r in records
        ]

    def clear(self) -> None:
        """Clears in-memory buffer (useful for unit tests)."""
        self._signatures.clear()
        self._active_prometheus_signatures.clear()


crash_engine = CrashSignatureEngine()
