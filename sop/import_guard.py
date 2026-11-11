"""Bind Excel conflict decisions to the file, operator and reviewed database state."""
import hashlib
import json
import uuid

from .store import TZ, ValidationError


def snapshot(store):
    # Only used during import/confirmation, not the periodic status poll. Include
    # catalog and rules so a rename, deletion or changed parent cannot be approved
    # using an earlier preview. Revisions also detect a value changed and restored.
    data = {table: [dict(row) for row in store.db.execute(f'SELECT * FROM {table} ORDER BY 1, 2')]
            for table in ('plans', 'product_catalog')}
    data['rules'] = store._generation_rules()
    data['settings'] = store.settings()
    data['month'] = store.clock().astimezone(TZ).strftime('%Y-%m')
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


class ImportGuard:
    def __init__(self, store, content, actor, token):
        self.store = store
        self.fingerprint = hashlib.sha256(content).hexdigest()
        self.actor = actor or 'Excel导入'
        self.snapshot = snapshot(store)
        row = store.db.execute('SELECT * FROM plan_import_confirmations WHERE token=?',
                               (token if isinstance(token, str) else '',)).fetchone()
        self.confirmed = bool(row and row['fingerprint'] == self.fingerprint
                              and row['actor'] == self.actor and row['snapshot'] == self.snapshot)
        self.result = (json.loads(row['result']) if row and row['fingerprint'] == self.fingerprint
                       and row['actor'] == self.actor and row['result'] else None)

    def check(self):
        # Called AFTER BEGIN IMMEDIATE: another connection cannot slip a write
        # between this check and the atomic catalog/plan commit.
        if snapshot(self.store) != self.snapshot:
            raise ValidationError('核对期间计划或车型规则已有变化，请重新导入并核对当前值。')

    def preview(self, result):
        token = uuid.uuid4().hex
        self.store.db.execute('INSERT INTO plan_import_confirmations(token,fingerprint,actor,snapshot) VALUES (?,?,?,?)',
                              (token, self.fingerprint, self.actor, self.snapshot))
        return {**result, 'confirmation_token': token}
