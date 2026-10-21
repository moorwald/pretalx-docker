"""Encrypted snapshots of the actual Pretalx SQLite/data/public volumes.

Uses the existing Mautic maintenance receiver and Coolify task primitives via
PYTHONPATH. Never exports a private key or plaintext over HTTP. No SSH or manual
production Docker access. A final snapshot must be taken with app writers held.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shlex
import urllib.request
from datetime import datetime, timezone

from coolify_task import Task
from export_backup import checked, decrypt, init, upload_certificate

APP = 'vwg0cs0oc8wgkcw0o8csko0o'
ORIGIN = 'https://cfp.mautic.org'


def export(folder):
    os.umask(0o077)
    receiver = json.loads((folder / 'receiver.json').read_text())
    nonce = receiver['session'].rsplit('-', 1)[1]
    assert len(nonce) == 10 and all(c in '0123456789abcdef' for c in nonce)
    work = '/data/.m' + nonce
    public = '/public/media/mwc-maint-' + secrets.token_hex(16) + '.cms'
    target = folder / 'pretalx.cms'
    if target.exists():
        raise RuntimeError('Use a new receiver directory for each immutable snapshot')
    with Task(APP, 'pretalx', 'Pretalx encrypted SQLite maintenance backup') as task:
        task.run(checked(f'umask 077; mkdir -m 700 {work}'))
        upload_certificate(task, work, folder / 'receiver.crt')
        code = f'import sqlite3;s=sqlite3.connect("file:/data/db.sqlite3?mode=ro",uri=True);d=sqlite3.connect("{work}/db.sqlite3");s.backup(d);d.close();s.close()'
        task.run(checked('python -c ' + shlex.quote(code)))
        code = f'import sqlite3;c=sqlite3.connect("{work}/db.sqlite3");print(c.execute("pragma integrity_check").fetchone()[0])'
        assert task.run(checked('python -c ' + shlex.quote(code))).strip() == 'ok'
        command = f'tar --exclude="data/.m??????????" --exclude=data/logs --exclude="data/db.sqlite3*" --exclude="public/media/mwc-maint-*.cms" -czf {work}/data.tgz -C / data public'
        task.run(checked(command))
        task.run(checked(f'tar -cf {work}/bundle.tar -C {work} db.sqlite3 data.tgz'))
        command = f'cd {work}; umask 077; openssl cms -encrypt -binary -aes-256-gcm -outform DER -in bundle.tar -out export.cms -recip cert.pem -keyopt rsa_padding_mode:oaep -keyopt rsa_oaep_md:sha256'
        task.run(checked(command))
        wanted = task.run(checked(f'sha256sum {work}/export.cms')).split()[0]
        published = False
        try:
            task.run(checked(f'mv {work}/export.cms {public} && chmod 644 {public}'))
            published = True
            url = ORIGIN + '/media/' + public.rsplit('/', 1)[1]
            request = urllib.request.Request(url, headers={'User-Agent': 'Mautic-encrypted-maintenance'})
            partial = target.with_suffix('.cms.partial')
            try:
                with urllib.request.urlopen(request, timeout=120) as response, partial.open('wb') as output:
                    while chunk := response.read(1024 * 1024):
                        output.write(chunk)
                assert hashlib.sha256(partial.read_bytes()).hexdigest() == wanted, 'Ciphertext hash mismatch'
                partial.replace(target)
            finally:
                partial.unlink(missing_ok=True)
        finally:
            if published:
                task.run(checked(f'rm -- {public}'))
        report = {'application': APP, 'created_at': datetime.now(timezone.utc).isoformat(),
                  'database_backend': 'sqlite3', 'ciphertext_sha256': wanted,
                  'ciphertext_bytes': target.stat().st_size, 'remote_workdir': work,
                  'public_ciphertext_removed': True, 'sqlite_integrity_check': 'ok'}
        (folder / 'pretalx-backup.json').write_text(json.dumps(report, indent=2) + '\n')
    decrypt(folder, 'pretalx')
    print('Pretalx encrypted snapshot transferred, hash checked, public copy removed and decrypted in private local storage.')
    print('Backup directory:', folder)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, help='Existing dedicated receiver directory; defaults to a new one')
    args = parser.parse_args()
    folder = args.directory.resolve() if args.directory else init()
    export(folder)
