"""Publish sales only after the actual source files remain stable for a minute."""
from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timezone
import importlib
import logging
import os
from pathlib import Path
import threading
import time
import uuid

from .paths import PROJECT_ROOT, source_directory, runtime_data_directory
from .store import ValidationError

LOGGER = logging.getLogger("sop")


SOURCE_FILES = ("鸿蒙智行锁单选配比例分析.xlsx",)


class SalesRefresh:
    def __init__(self, store, workbook_sync=None, *, root=None, reader=None,
                 interval=300, stable_seconds=60, clock=time.monotonic):
        self.store, self.workbook_sync = store, workbook_sync
        self.root = Path(root or PROJECT_ROOT)
        self.reader = reader or (lambda **kwargs: importlib.import_module("sop.data").refresh(**kwargs))
        self.interval, self.stable_seconds, self.clock = interval, stable_seconds, clock
        self.lock = threading.RLock()
        self.observed = self.applied = None
        self.observed_at = None
        self.next_check = 0
        self._status = {"status": "watching", "message": "自动检查原始文件；保存稳定后读取", "last_refresh": None}

    def status(self):
        with self.lock:
            return dict(self._status)

    def fingerprint(self):
        source = source_directory(self.root)
        paths = [source / name for name in SOURCE_FILES]
        paths.append(runtime_data_directory(self.root) / "source_settings.json")
        result = []
        for path in paths:
            try:
                stat = path.stat()
                result.append((str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
            except FileNotFoundError:
                result.append((str(path), None, None, None))
        return tuple(result)

    def refresh(self):
        # Older installations may still use file maintenance. The current
        # frontend-only mode never reads or writes that workbook.
        active_sync = self.workbook_sync if self.workbook_sync and not getattr(self.workbook_sync, "frontend_only", False) else None
        with self.lock, (active_sync.lock if active_sync else nullcontext()):
            if active_sync:
                sync = active_sync.sync()
                if sync["status"] in {"error", "conflict"} or not active_sync._input_checked:
                    raise ValidationError("先处理计划表同步问题后再更新销量：" + sync["message"])
            before = self.fingerprint()
            with self.store.lock:
                self.store.ensure_daily_snapshots()
                pending = self.store.sales_path.with_name(f".sales-{uuid.uuid4().hex}.pending.json")
                previous = self.store.sales_path.read_bytes() if self.store.sales_path.exists() else None
                published = False
                try:
                    candidate = self.reader(output_path=pending)
                    if self.fingerprint() != before:
                        raise ValidationError("原始文件正在更新，稍后将自动重试。")
                    self.store.validate_source_data(candidate)
                    os.replace(pending, self.store.sales_path)
                    published = True
                    data = self.store.record_sales()
                except Exception:
                    if published and previous is not None:
                        pending.write_bytes(previous)
                        os.replace(pending, self.store.sales_path)
                    raise
                finally:
                    pending.unlink(missing_ok=True)
            self.applied = self.observed = before
            self.observed_at = self.clock()
            self._status = {"status": "synced", "message": "原始文件自动读取已开启", "last_refresh": datetime.now(timezone.utc).isoformat()}
            result = {"ok": True, "meta": data.get("meta", {}), "product_count": len(data["products"]), "sales_count": len(data["sales"])}
            if active_sync:
                result["workbook_sync"] = active_sync.sync()
            return result

    def poll(self, *, force=False):
        with self.lock:
            now = self.clock()
            if not force and now < self.next_check:
                return None
            try:
                current = self.fingerprint()
                if current == self.applied:
                    self.observed = current
                    self.observed_at = None
                    self.next_check = now + self.interval
                    if self._status["status"] in {"waiting", "error"}:
                        self._status = {**self._status, "status": "synced", "message": "来源文件没有新修改。"}
                    return {"ok": True, "status": "unchanged", "message": "来源文件没有新修改。"}
                if current != self.observed:
                    self.observed = current
                    self.observed_at = now
                    self.next_check = now + self.stable_seconds
                    self._status = {**self._status, "status": "waiting", "message": "检测到来源文件变化，等待稳定1分钟后更新。"}
                    return {"ok": False, "status": "waiting", "message": self._status["message"]}
                if self.observed_at is None or now - self.observed_at < self.stable_seconds:
                    self.next_check = (self.observed_at if self.observed_at is not None else now) + self.stable_seconds
                    return {"ok": False, "status": "waiting", "message": self._status["message"]}
                result = self.refresh()
                self.next_check = self.clock() + self.interval
                return {**result, "status": "synced"}
            except Exception as error:
                self._status.update(status="error", message="原始文件尚未更新，保留上次数据；将自动重试。" + str(error))
                self.next_check = self.clock() + self.stable_seconds
                LOGGER.warning("Sales refresh deferred: %s", error)
                if force:
                    raise
                return {"ok": False, "status": "error", "message": self._status["message"]}
