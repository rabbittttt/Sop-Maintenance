"""Persistent, versioned reconciliation rounds on the SOP database."""
from __future__ import annotations

import base64
import binascii
from collections import defaultdict
from contextlib import contextmanager
from datetime import date, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
from uuid import uuid4

from .paths import PLAN_WORKBOOK_NAME, project_path, source_directory
from .reconcile_baseline import capture_baseline
from .reconcile_engine import METRICS, reconcile, validate_record
from .source_rules import normalize_generation
from .store import ConflictError, ValidationError, month_range, stamp, text_field as store_text_field, validate_month

MAX_FILE_BYTES = 15 * 1024 * 1024
MAX_IMPORT_BYTES = 26 * 1024 * 1024
MAX_RECORDS = 10000
SHEET_TYPES = {".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
               ".xls": "application/vnd.ms-excel", ".csv": "text/csv; charset=utf-8"}
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
RECORD_FIELDS = {"target_id": ("关联车型", 160), "model": ("原文车型", 200), "variant": ("原文版本", 200),
                 "period": ("期间", 40), "granularity": ("时间粒度", 10), "metric": ("指标", 60),
                 "location": ("库区", 200), "batch": ("批次", 200), "scope": ("范围", 16)}


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return sha256(_encode(value).encode("utf-8")).hexdigest()


def text_field(value, label, max_length):
    text = store_text_field(value, label, max_length)
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        raise ValidationError(f"{label}包含无效字符。") from None
    return text


def _owner(body):
    value = text_field(body.get("owner"), "维护人", 100)
    if not value:
        raise ValidationError("请先填写维护人。")
    return value


class Reconciliation:
    def __init__(self, store, root):
        self.store, self.root = store, Path(root)
        self.db = store.db
        with self.store.lock:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS reconcile_rounds (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, start_month TEXT NOT NULL, end_month TEXT NOT NULL,
                    revision INTEGER NOT NULL, baseline_version INTEGER NOT NULL, baseline_at TEXT NOT NULL,
                    created_at TEXT NOT NULL, owner TEXT NOT NULL, snapshot_id INTEGER
                );
                CREATE TABLE IF NOT EXISTS reconcile_baselines (
                    round_id TEXT NOT NULL, version INTEGER NOT NULL, payload TEXT NOT NULL,
                    snapshot_id INTEGER, created_at TEXT NOT NULL, owner TEXT NOT NULL,
                    PRIMARY KEY(round_id, version)
                );
                CREATE TABLE IF NOT EXISTS reconcile_sources (
                    id TEXT PRIMARY KEY, round_id TEXT NOT NULL, name TEXT NOT NULL, type TEXT NOT NULL,
                    created_at TEXT NOT NULL, as_of TEXT NOT NULL, owner TEXT NOT NULL, previous_id TEXT,
                    fingerprint TEXT NOT NULL, raw_text TEXT NOT NULL, content BLOB, media_type TEXT,
                    UNIQUE(round_id, fingerprint)
                );
                CREATE TABLE IF NOT EXISTS reconcile_records (
                    id TEXT PRIMARY KEY, round_id TEXT NOT NULL, source_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    payload TEXT NOT NULL, UNIQUE(source_id, fingerprint)
                );
                CREATE INDEX IF NOT EXISTS reconcile_records_round ON reconcile_records(round_id);
                CREATE TABLE IF NOT EXISTS reconcile_audit (
                    round_id TEXT NOT NULL, revision INTEGER NOT NULL, action TEXT NOT NULL,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL, owner TEXT NOT NULL,
                    PRIMARY KEY(round_id, revision)
                );
            """)

    @contextmanager
    def _transaction(self):
        with self.store.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def index(self):
        with self.store.lock:
            rounds = [dict(row) for row in self.db.execute("SELECT * FROM reconcile_rounds ORDER BY created_at DESC, rowid DESC")]
            snapshots = [dict(row) for row in self.db.execute(
                "SELECT id,label,cutoff_at AS cutoff FROM snapshots ORDER BY cutoff_at DESC, id DESC")]
        return {"rounds": rounds, "snapshots": snapshots, "current_month": self.store.clock().strftime("%Y-%m"),
                "metrics": METRICS}

    def _round(self, round_id, body=None):
        row = self.db.execute("SELECT * FROM reconcile_rounds WHERE id=?", (round_id,)).fetchone()
        if not row:
            raise ValidationError("对账轮次不存在。")
        if body is not None and (type(body.get("revision")) is not int or body["revision"] != row["revision"]):
            raise ConflictError([{"round_id": round_id, "revision": row["revision"], "reason": "对账轮次已更新，请重新读取。"}])
        return dict(row)

    def _baseline(self, row):
        return json.loads(self.db.execute("SELECT payload FROM reconcile_baselines WHERE round_id=? AND version=?",
                                         (row["id"], row["baseline_version"])).fetchone()[0])

    def _records(self, round_id):
        return [json.loads(row[0]) for row in self.db.execute(
            "SELECT payload FROM reconcile_records WHERE round_id=? ORDER BY rowid", (round_id,))]

    def detail(self, round_id, *, include_baseline=False):
        with self.store.lock:
            row = self._round(round_id)
            baseline = self._baseline(row)
            records = self._records(round_id)
            sources = [dict(item) for item in self.db.execute("""
                SELECT id,name,type,created_at,as_of,owner,previous_id,content IS NOT NULL AS has_file
                FROM reconcile_sources WHERE round_id=? ORDER BY rowid DESC
            """, (round_id,))]
        labels = {t["id"]: t["label"] for t in baseline["targets"]}
        candidates = [{**r, "model": labels.get(r["target_id"], r["model"])} for r in records]
        result = reconcile(baseline["records"] + candidates)
        issue_record_ids = {rid for issue in result["issues"] for rid in issue["record_ids"]}
        return {**row, "sources": sources, "records": (baseline["records"] + records if include_baseline else records),
                "targets": [{key: t[key] for key in ("id", "label", "level")} for t in baseline["targets"]],
                "result": result, "baseline_count": len(baseline["records"]), "metrics": METRICS,
                "baseline_records": [r for r in baseline["records"] if r["id"] in issue_record_ids]}

    @staticmethod
    def _snapshot_id(value):
        if value in (None, ""):
            return None
        if isinstance(value, str) and value.isascii() and value.isdecimal() and len(value) <= 12:
            value = int(value)
        if type(value) is not int or not 1 <= value <= 10**12:
            raise ValidationError("基准快照编号无效。")
        return value

    def _write_baseline(self, round_id, version, baseline, snapshot_id, now, owner):
        self.db.execute("INSERT INTO reconcile_baselines VALUES (?,?,?,?,?,?)",
                        (round_id, version, _encode(baseline), snapshot_id, now, owner))

    def _audit(self, round_id, revision, action, details, now, owner):
        self.db.execute("INSERT INTO reconcile_audit VALUES (?,?,?,?,?,?)",
                        (round_id, revision, action, _encode(details), now, owner))

    def create(self, body):
        name, owner = text_field(body.get("name"), "轮次名称", 120), _owner(body)
        start, end = validate_month(body.get("start_month")), validate_month(body.get("end_month"))
        if not name or not 1 <= len(month_range(start, end)) <= 60:
            raise ValidationError("请填写轮次名称，并选择有效的月份范围（最长 60 个月）。")
        snapshot_id = self._snapshot_id(body.get("snapshot_id"))
        round_id = uuid4().hex
        with self.store.lock:
            baseline = capture_baseline(self.store, start, end, snapshot_id)
            now = stamp(self.store.clock())
            with self._transaction():
                self.db.execute("INSERT INTO reconcile_rounds VALUES (?,?,?,?,?,?,?,?,?,?)", (
                    round_id, name, start, end, 1, 1, baseline["captured_at"], now, owner, snapshot_id))
                self._write_baseline(round_id, 1, baseline, snapshot_id, now, owner)
                self._audit(round_id, 1, "create", {"baseline_version": 1}, now, owner)
        return {"id": round_id}

    def update_baseline(self, round_id, body):
        owner, snapshot_id = _owner(body), self._snapshot_id(body.get("snapshot_id"))
        with self.store.lock:
            row = self._round(round_id, body)
            previous = self._baseline(row)
            baseline = capture_baseline(self.store, row["start_month"], row["end_month"], snapshot_id)
            now = stamp(self.store.clock())
            version, revision = row["baseline_version"] + 1, row["revision"] + 1
            # Keep reviewed catalog links resolvable after a vehicle is retired or renamed.
            current_ids = {t["id"] for t in baseline["targets"]}
            linked_ids = {r["target_id"] for r in self._records(round_id)}
            baseline["targets"].extend(t for t in previous["targets"] if t["id"] in linked_ids - current_ids)
            with self._transaction():
                self._round(round_id, body)
                self._write_baseline(round_id, version, baseline, snapshot_id, now, owner)
                self.db.execute("""UPDATE reconcile_rounds SET baseline_at=?, snapshot_id=?, revision=?, baseline_version=?
                                  WHERE id=?""", (baseline["captured_at"], snapshot_id, revision, version, round_id))
                self._audit(round_id, revision, "baseline", {"baseline_version": version}, now, owner)
        return self.detail(round_id)

    def _shared_files(self):
        orders = source_directory(self.root).resolve()
        configured = os.environ.get("SOP_RECONCILE_DIR")
        roots = [orders]
        if configured:
            roots.insert(0, project_path(self.root, configured).resolve())
        elif orders.name == "订单汇总":
            roots.insert(0, (orders.parent / "对账资料").resolve())
        result = {}
        for root in dict.fromkeys(roots):
            if not root.is_dir():
                continue
            for path in sorted(root.iterdir()):
                name = path.name
                if (name.startswith(("~$", ".")) or name.casefold() == PLAN_WORKBOOK_NAME.casefold()
                        or name.lower().endswith(".pending.xlsx") or path.suffix.lower() not in SHEET_TYPES
                        or path.is_symlink() or not path.is_file() or path.resolve().parent != root):
                    continue
                try:
                    info = path.stat()
                except FileNotFoundError:
                    continue
                if 0 < info.st_size <= MAX_FILE_BYTES:
                    signature = (info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino)
                    result[_digest([str(path), signature])] = (path, signature)
        return result

    def shared(self):
        return {"files": [{"id": key, "name": path.name} for key, (path, _) in self._shared_files().items()]}

    def read_shared(self, file_id):
        entry = self._shared_files().get(file_id) if isinstance(file_id, str) else None
        if not entry:
            raise ValidationError("共享文件不存在或已更新，请重新选择。")
        path, signature = entry
        try:
            with path.open("rb") as stream:
                content = stream.read(MAX_FILE_BYTES + 1)
                info = os.fstat(stream.fileno())
                named_info = path.stat()
        except OSError:
            raise ValidationError("共享文件暂时无法读取，请稍后重试。") from None
        # Windows reports different ctime semantics on an open file handle.
        named = (named_info.st_size, named_info.st_mtime_ns, named_info.st_ctime_ns, named_info.st_ino)
        if (len(content) != signature[0] or signature != named
                or (info.st_size, info.st_mtime_ns, info.st_ino) != (signature[0], signature[1], signature[3])):
            raise ValidationError("共享文件正在更新，请重新选择。")
        return path.name, SHEET_TYPES[path.suffix.lower()], content

    def _source_input(self, value):
        if not isinstance(value, dict) or value.get("type") not in ("excel", "text", "image"):
            raise ValidationError("请选择 Excel、消息或截图资料。")
        kind = value["type"]
        name, as_of = text_field(value.get("name"), "资料名称", 240), text_field(value.get("as_of"), "资料日期", 10)
        raw_text = text_field(value.get("raw_text"), "资料原文", 200000)
        if as_of:
            try:
                if date.fromisoformat(as_of).isoformat() != as_of:
                    raise ValueError
            except ValueError:
                raise ValidationError("资料日期须为 YYYY-MM-DD。") from None
        content, media = None, None
        if value.get("shared_id"):
            if kind != "excel" or value.get("content_base64"):
                raise ValidationError("共享资料只能使用选定的表格文件。")
            name, media, content = self.read_shared(value["shared_id"])
        elif value.get("content_base64") is not None:
            encoded = value["content_base64"]
            if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_FILE_BYTES + 2) // 3):
                raise ValidationError("资料文件不能超过 15 MB。")
            try:
                content = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError):
                raise ValidationError("资料文件编码无效。") from None
            media = (SHEET_TYPES if kind == "excel" else IMAGE_TYPES if kind == "image" else {}).get(Path(name).suffix.lower())
            if not media or not 0 < len(content) <= MAX_FILE_BYTES:
                raise ValidationError("资料文件类型无效、为空或超过 15 MB。")
        if not name or any(ord(c) < 32 or c in "/\\" for c in name):
            raise ValidationError("资料名称不能为空或包含路径字符。")
        if kind == "text" and not raw_text:
            raise ValidationError("消息原文不能为空。")
        fingerprint = _digest([kind, as_of, raw_text, sha256(content).hexdigest() if content else None,
                               name.casefold() if content is None and not raw_text else None])
        return {"name": name, "type": kind, "as_of": as_of, "raw_text": raw_text,
                "content": content, "media_type": media, "fingerprint": fingerprint}

    @staticmethod
    def _record_fields(value):
        fields = {key: text_field(value.get(key), label, limit) for key, (label, limit) in RECORD_FIELDS.items()}
        amount = value.get("quantity")
        if amount is not None and (type(amount) is not int or abs(amount) > 10**12):
            raise ValidationError("数量须为整数，无法识别的数量请留空。")
        return {**fields, "quantity": amount}

    @staticmethod
    def _aliases(baseline):
        targets = {target["id"]: target for target in baseline["targets"]}
        aliases = defaultdict(set)
        for target in targets.values():
            for name in target["aliases"]:
                if name:
                    aliases[normalize_generation(name).casefold()].add(target["id"])
        return targets, aliases

    def _candidate(self, value, source_id, targets, aliases):
        if not isinstance(value, dict):
            raise ValidationError("候选记录格式无效。")
        fields = self._record_fields(value)
        if not fields["target_id"]:
            label = " ".join(filter(None, (fields["model"], fields["variant"])))
            matches = aliases[normalize_generation(label).casefold()]
            if len(matches) == 1:
                fields["target_id"] = next(iter(matches))
        elif fields["target_id"] not in targets:
            raise ValidationError("所选车型不在本轮基准目录中，请重新选择。")
        warnings = value.get("warnings", [])
        if not isinstance(warnings, list) or len(warnings) > 30:
            raise ValidationError("候选记录提示格式无效。")
        source_ref = text_field(value.get("source_ref"), "来源位置", 500)
        if not source_ref:
            raise ValidationError("候选记录缺少来源位置。")
        return {**fields, "source_id": source_id, "source_ref": source_ref,
                "original_text": text_field(value.get("original_text"), "记录原文", 10000),
                "warnings": [text_field(item, "记录提示", 500) for item in warnings], "status": "pending"}

    def import_sources(self, round_id, body, *, restore=False):
        owner = _owner(body)
        imports = body.get("imports") if restore else [body]
        if not isinstance(imports, list) or not 1 <= len(imports) <= 100:
            raise ValidationError("请选择要导入的资料。")
        total = 0
        prepared = []
        for item in imports:
            if not isinstance(item, dict) or not isinstance(item.get("records"), list):
                raise ValidationError("候选记录列表格式无效。")
            total += len(item["records"])
            if total > MAX_RECORDS or (not restore and not total):
                raise ValidationError("单次请导入 1 至 10,000 条候选记录。")
            prepared.append((self._source_input(item.get("source")), item["records"]))
        now = stamp(self.store.clock())
        with self._transaction():
            row = self._round(round_id, body)
            targets, aliases = self._aliases(self._baseline(row))
            added, imported_ids, record_ids = 0, [], []
            for source, values in prepared:
                existing = self.db.execute("SELECT id FROM reconcile_sources WHERE round_id=? AND fingerprint=?",
                                           (round_id, source["fingerprint"])).fetchone()
                source_id = existing["id"] if existing else uuid4().hex
                if not existing:
                    previous = self.db.execute("""SELECT id FROM reconcile_sources WHERE round_id=? AND type=? AND name=?
                                                  ORDER BY rowid DESC LIMIT 1""", (round_id, source["type"], source["name"])).fetchone()
                    self.db.execute("INSERT INTO reconcile_sources VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
                        source_id, round_id, source["name"], source["type"], now, source["as_of"], owner,
                        previous["id"] if previous else None, source["fingerprint"], source["raw_text"],
                        source["content"], source["media_type"]))
                    imported_ids.append(source_id)
                for value in values:
                    candidate = self._candidate(value, source_id, targets, aliases)
                    fingerprint = _digest({key: val for key, val in candidate.items() if key not in {"warnings", "target_id"}})
                    candidate.update(id=uuid4().hex, updated_at=now, updated_by=owner)
                    cursor = self.db.execute("INSERT OR IGNORE INTO reconcile_records VALUES (?,?,?,?,?)",
                                             (candidate["id"], round_id, source_id, fingerprint, _encode(candidate)))
                    if cursor.rowcount:
                        record_ids.append(candidate["id"])
                        added += 1
            if self.db.execute("SELECT COUNT(*) FROM reconcile_records WHERE round_id=?", (round_id,)).fetchone()[0] > 2 * MAX_RECORDS:
                raise ValidationError("本轮记录已超过 20,000 条，请另建一轮。")
            if imported_ids or added:
                revision = row["revision"] + 1
                self.db.execute("UPDATE reconcile_rounds SET revision=? WHERE id=?", (revision, round_id))
                self._audit(round_id, revision, "restore" if restore else "import",
                            {"sources": imported_ids, "records": record_ids}, now, owner)
        return {**self.detail(round_id), "added_records": added}

    @staticmethod
    def _period_in_scope(record, row):
        period = record["period"]
        if not row["start_month"] <= period[:7] <= row["end_month"]:
            return False
        if record["granularity"] == "week":
            # Whole weeks must be contained in the chosen round.
            end = (date.fromisoformat(period) + timedelta(days=6)).isoformat()
        else:
            end = period
        return end[:7] <= row["end_month"]

    def update_records(self, round_id, body):
        owner, changes = _owner(body), body.get("changes")
        if not isinstance(changes, list) or not 1 <= len(changes) <= MAX_RECORDS:
            raise ValidationError("请选择要更新的记录。")
        now = stamp(self.store.clock())
        with self._transaction():
            row = self._round(round_id, body)
            baseline = self._baseline(row)
            targets = {target["id"]: target for target in baseline["targets"]}
            edits = []
            seen = set()
            for change in changes:
                if not isinstance(change, dict) or not isinstance(change.get("id"), str) or change["id"] in seen:
                    raise ValidationError("待更新记录无效或重复。")
                seen.add(change["id"])
                stored = self.db.execute("SELECT payload FROM reconcile_records WHERE round_id=? AND id=?",
                                         (round_id, change["id"])).fetchone()
                if not stored:
                    raise ValidationError("待更新记录不存在。")
                before = json.loads(stored["payload"])
                merged = {**before, **change}
                record = {**before, **self._record_fields(merged), "status": merged["status"]}
                if record["status"] not in ("pending", "confirmed", "excluded"):
                    raise ValidationError("确认状态无效。")
                target = targets.get(record["target_id"])
                if record["target_id"] and target is None:
                    raise ValidationError("所选车型不在本轮基准目录中。")
                if record["status"] == "confirmed":
                    errors = validate_record({**record, "model": target["label"] if target else record["model"]})
                    if errors:
                        raise ValidationError("；".join(errors))
                    if not self._period_in_scope(record, row):
                        raise ValidationError("记录期间须完整落在本轮月份范围内。")
                if record != before:
                    record.update(updated_at=now, updated_by=owner)
                    self.db.execute("UPDATE reconcile_records SET payload=? WHERE id=?", (_encode(record), record["id"]))
                    edits.append({"before": before, "after": record})
            if edits:
                revision = row["revision"] + 1
                self.db.execute("UPDATE reconcile_rounds SET revision=? WHERE id=?", (revision, round_id))
                self._audit(round_id, revision, "records", edits, now, owner)
        return self.detail(round_id)

    def source(self, round_id, source_id, *, download=False):
        with self.store.lock:
            fields = ("name,media_type,content" if download else
                      "id,name,type,created_at,as_of,owner,previous_id,raw_text,content IS NOT NULL AS has_file")
            row = self.db.execute(f"SELECT {fields} FROM reconcile_sources WHERE round_id=? AND id=?",
                                  (round_id, source_id)).fetchone()
        if not row or (download and row["content"] is None):
            raise ValidationError("资料或原始文件不存在。")
        return (row["name"], row["media_type"], row["content"]) if download else dict(row)
