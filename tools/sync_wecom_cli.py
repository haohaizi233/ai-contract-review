"""Bounded, read-only WeCom CLI pull -> local RAG. Never deactivates missing files."""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from contract_kb import config  # noqa: E402
from contract_kb.ingest import import_template_file, security_check_file  # noqa: E402
from contract_kb.storage import Storage, create_storage  # noqa: E402
from tools.sync_wecom_wedrive import parse_pdf_document  # noqa: E402

TARGET = '法律事务部（含商务合同等文件）/商务合同模板/深圳市世纪恒程知识产权代理事务所'


def atomic_json(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def scoped_files(rows, prefix):
    base = PurePosixPath(prefix).parts
    result = []
    for row in rows:
        raw = row.get('path', '').replace('\\', '/')
        parts = PurePosixPath(raw).parts
        if (row.get('type') == 'file' and '..' not in parts
                and len(parts) > len(base) and parts[:len(base)] == base):
            result.append(row)
    return result


def fetch_inventory(call, space, keywords=()):
    rows, cursor, seen = [], '', set()
    for _ in range(1000):
        args = ['disk', 'files', 'search', '--search-type', 'file',
                '--space-keywords', space, '--limit', '100']
        if cursor:
            args += ['--cursor', cursor]
        if keywords:
            args += ['--keywords', *keywords]
        data = call(args)
        rows.extend(data.get('files', []))
        cursor = data.get('next_cursor') or ''
        if data.get('has_more') and not cursor:
            raise RuntimeError('pagination_missing_cursor')
        if not cursor:
            return rows
        if cursor in seen:
            raise RuntimeError('pagination_repeated_cursor')
        seen.add(cursor)
    raise RuntimeError('pagination_limit_exceeded')


class CliClient:
    def __init__(self, executable, space):
        self.executable, self.space = executable, space

    def call(self, args, cwd=None):
        p = subprocess.run([self.executable, *args], capture_output=True,
                           encoding='utf-8', errors='replace', timeout=180,
                           cwd=cwd,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            data = json.loads(p.stdout)
        except ValueError:
            raise RuntimeError('cli_invalid_response') from None
        if p.returncode or 'error' in data or data.get('errcode', 0):
            error = data.get('error', {})
            code = error.get('code') if isinstance(error, dict) else None
            # Do not persist raw API responses, tokens, URLs or identity context.
            raise RuntimeError(f'cli_error:{code or data.get("errcode", "unknown")}')
        return data

    def inventory(self):
        # Observed server behavior: an unfiltered space search returns only a subset,
        # even with no cursor. Supplement with bounded explicit keyword queries.
        # Each query is fully paginated; exact scope is enforced by scoped_files.
        merged = {}
        for keywords in ((), ('事务所',), ('世纪恒程',)):
            for row in fetch_inventory(self.call, self.space, keywords):
                previous = merged.get(row['id'])
                if previous is None or row.get('update_time', '') >= previous.get('update_time', ''):
                    merged[row['id']] = row
        return list(merged.values())

    def download(self, row, target):
        data = self.call(['disk', 'files', 'download', '--file-id', row['id'],
                          '--output-dir', str(target.parent)], cwd=target.parent)
        returned = data.get('file_path', '')
        if returned:
            if returned.startswith('\\\\?\\UNC\\'):
                returned = '\\\\' + returned[8:]
            elif returned.startswith('\\\\?\\'):
                returned = returned[4:]
            p = Path(returned)
            if p.is_file() and p.resolve().is_relative_to(target.parent.resolve()):
                if p.resolve() != target.resolve():
                    shutil.copyfile(p, target)
            else:
                raise RuntimeError('download_path_outside_cache')
        if not target.is_file():
            raise RuntimeError('download_missing_file')


def convert_doc(source, target):
    proc = subprocess.run([
        'powershell.exe', '-NoProfile', '-NonInteractive', '-File',
        str(ROOT / 'tools/deploy/convert_legacy_doc.ps1'),
        '-Source', str(source), '-Destination', str(target)],
        capture_output=True, timeout=120,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if proc.returncode or not target.is_file():
        raise RuntimeError('legacy_doc_conversion_failed')


class CliSync:
    def __init__(self, cache, prefix, client, db_path=None, trusted=False, mirror=None):
        self.mirror = Path(mirror).resolve() if mirror else None
        self.trusted = trusted
        self.cache, self.prefix, self.client = Path(cache), prefix, client
        self.cache.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.cache / 'manifest.json'
        self.manifest = (json.loads(self.manifest_path.read_text(encoding='utf-8'))
                         if self.manifest_path.exists() else {})
        self.storage = Storage(db_path) if db_path else create_storage()

    def run(self):
        health_path = self.cache / 'health.json'
        previous = json.loads(health_path.read_text(encoding='utf-8')) if health_path.exists() else {}
        result = {'timestamp': datetime.now().astimezone().isoformat(),
                  'source': self.prefix, 'status': 'OK', 'changed': 0, 'skipped': 0,
                  'failed': 0, 'unsupported': 0, 'errors': [],
                  'last_success': previous.get('last_success'),
                  'authorization_expiry': 'UNKNOWN_REQUIRES_USER_RENEWAL',
                  'trusted_source': self.trusted,
                  'local_directory': str(self.mirror) if self.mirror else None,
                  'db_path': str(config.DB_PATH), 'deletion_enabled': False}
        try:
            rows = scoped_files(self.client.inventory(), self.prefix)
        except Exception as e:
            result.update(status='SOURCE_UNAVAILABLE', failed=1,
                          errors=[{'stage': 'inventory', 'error': self.safe_error(e)}])
            atomic_json(health_path, result)
            return result
        result['discovered'] = len(rows)
        for row in rows:
            try:
                self.sync_file(row, result)
            except Exception as e:
                result['failed'] += 1
                result['errors'].append({'path': row.get('path'), 'error': self.safe_error(e)})
        if result['failed']:
            result['status'] = 'PARTIAL_FAILURE'
        elif result['unsupported']:
            result['status'] = 'UNSUPPORTED_FILES'
        elif not rows:
            result['status'] = 'EMPTY_SOURCE_CHECK_REQUIRED'
        else:
            result['last_success'] = result['timestamp']
        atomic_json(health_path, result)
        return result

    @staticmethod
    def safe_error(error):
        if isinstance(error, RuntimeError):
            return str(error)[:160]
        return type(error).__name__

    def sync_file(self, row, result):
        key = row['id']
        suffix = PurePosixPath(row['path']).suffix.lower()
        if suffix not in ('.doc', '.docx', '.pdf'):
            result['unsupported'] += 1
            return
        size = int(row['file_size'])
        if size <= 0 or size > 20 * 1024 * 1024:
            raise RuntimeError('invalid_or_oversize_file')
        old = self.manifest.get(key, {})
        stable_id = 'WDCLI_' + hashlib.sha256(key.encode()).hexdigest()[:24]
        directory = self.cache / 'files' / stable_id
        directory.mkdir(parents=True, exist_ok=True)
        incoming = directory / ('incoming' + suffix)
        mirror_target = self.mirror_target(row)
        if mirror_target and mirror_target.is_file() and old.get('mirror_path') == str(mirror_target):
            actual = hashlib.sha256(mirror_target.read_bytes()).hexdigest()
            if actual != old.get('original_sha256'):
                raise RuntimeError('local_edit_conflict_preserved')
        unchanged = all(old.get(k) == row.get(k) for k in ('path', 'file_size', 'update_time'))
        if unchanged and old.get('document_id'):
            doc = self.storage.get_document(old['document_id'], tenant_id=config.TENANT_ID)
            cache_valid = incoming.is_file() and hashlib.sha256(incoming.read_bytes()).hexdigest() == old.get('original_sha256')
            if doc and doc['status'] in ('ACTIVE', 'PENDING_REVIEW') and (not self.mirror or cache_valid):
                self.publish(incoming, mirror_target, stable_id)
                if self.trusted and doc['status'] == 'PENDING_REVIEW':
                    self.approve(old['document_id'])
                if mirror_target:
                    old['mirror_path'] = str(mirror_target)
                    atomic_json(self.manifest_path, self.manifest)
                result['skipped'] += 1
                return
        self.client.download(row, incoming)
        if incoming.stat().st_size != size:
            raise RuntimeError('download_size_mismatch')
        raw_hash = hashlib.sha256(incoming.read_bytes()).hexdigest()
        parsed_path = incoming
        if suffix == '.doc':
            parsed_path = directory / 'converted.docx'
            if parsed_path.exists():
                parsed_path.unlink()
            convert_doc(incoming, parsed_path)
        if suffix == '.pdf':
            rec, error = parse_pdf_document(parsed_path)
        else:
            sec = security_check_file(parsed_path)
            if not sec.passed:
                raise RuntimeError('document_security_check_failed')
            rec, error = import_template_file(parsed_path)
        if rec is None or not rec.clauses:
            raise RuntimeError('parse_failed_or_no_clauses')
        doc = rec.to_dict()
        # Stable template identity, unique content-version fingerprint including remote revision.
        # Including revision permits reverting to historical bytes without reactivating stale IDs.
        doc.update(template_id=stable_id, source_file=row['path'], status='PENDING_REVIEW',
                   sha256=hashlib.sha256((key + raw_hash + str(row['update_time'])).encode()).hexdigest())
        did, _ = self.storage.upsert_document(doc, tenant=config.TENANT_ID)
        if self.trusted:
            self.approve(did)
        self.publish(incoming, mirror_target, stable_id)
        self.manifest[key] = {k: row.get(k) for k in ('path', 'file_size', 'update_time')}
        self.manifest[key].update(document_id=did, original_sha256=raw_hash,
                                  clause_count=len(rec.clauses))
        if mirror_target:
            self.manifest[key]['mirror_path'] = str(mirror_target)
        atomic_json(self.manifest_path, self.manifest)
        result['changed'] += 1

    def mirror_target(self, row):
        if not self.mirror:
            return None
        parts = PurePosixPath(row['path'].replace('\\', '/')).parts
        base = PurePosixPath(self.prefix).parts
        relative = parts[len(base):]
        if parts[:len(base)] != base or not relative or any(p in ('.', '..') or ':' in p for p in relative):
            raise RuntimeError('invalid_mirror_path')
        target = self.mirror.joinpath(*relative).resolve()
        if not target.is_relative_to(self.mirror.resolve()):
            raise RuntimeError('mirror_path_outside_root')
        return target

    def publish(self, incoming, target, stable_id):
        if target is None:
            return
        data = incoming.read_bytes()
        if target.is_file():
            previous = target.read_bytes()
            if previous == data:
                return
            backup = self.cache / 'local_backups' / stable_id
            backup.mkdir(parents=True, exist_ok=True)
            saved = backup / (hashlib.sha256(previous).hexdigest() + target.suffix)
            if not saved.exists():
                saved.write_bytes(previous)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name + '.wecom-sync.tmp')
        temp.write_bytes(data)
        temp.replace(target)

    def approve(self, document_id):
        with self.storage._lock:
            try:
                self.storage.approve_knowledge_version(
                    tenant_id=config.TENANT_ID, document_id=document_id,
                    approver_id='wecom-cli-sync:authorized-trusted-source',
                    approval_action='TRUSTED_SOURCE_SYNC',
                    approval_reason='User-authorized automatic activation from bounded source: ' + self.prefix)
            except Exception:
                self.storage._rollback()
                raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, default=ROOT / 'data/wecom_cli_sync')
    parser.add_argument('--prefix', default=TARGET)
    parser.add_argument('--local-dir', type=Path, default=ROOT / '参考',
                        help='Local original-file mirror; cloud-relative subfolders retained')
    parser.add_argument('--trusted-source', action='store_true',
                        help='User-authorized template source; approve each version with audit')
    parser.add_argument('--cli', default=shutil.which('wecom-cli.cmd') or shutil.which('wecom-cli'))
    args = parser.parse_args()
    if not args.cli:
        parser.error('wecom-cli is not installed')
    args.cache.mkdir(parents=True, exist_ok=True)
    # OS releases the byte-range lock when a crashed process exits.
    import msvcrt
    with (args.cache / 'sync.lock').open('a+b') as lock:
        lock.seek(0)
        if not lock.read(1):
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            print('sync_already_running')
            return 0
        sync = None
        try:
            sync = CliSync(args.cache, args.prefix, CliClient(args.cli, args.prefix.split('/')[0]),
                           trusted=args.trusted_source, mirror=args.local_dir)
            result = sync.run()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result['status'] == 'OK' else 1
        except Exception as error:
            atomic_json(args.cache / 'health.json', {
                'timestamp': datetime.now().astimezone().isoformat(),
                'status': 'STARTUP_OR_RUNTIME_FAILURE', 'error': CliSync.safe_error(error),
                'last_success': None, 'deletion_enabled': False})
            print('sync_failed_see_health_json')
            return 1
        finally:
            if sync is not None:
                sync.storage.close()


if __name__ == '__main__':
    sys.exit(main())
