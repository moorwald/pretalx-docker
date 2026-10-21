"""Read-only integrity probe for held or running Pretalx, emitting no personal data."""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys

from qa_pretalx import fingerprints


def probe():
    data = Path('/data')
    media = Path('/public/media')
    files = {hashlib.sha256(str(path.relative_to(media)).encode()).hexdigest():
             hashlib.sha256(path.read_bytes()).hexdigest()
             for path in media.rglob('*') if path.is_file() and not path.name.startswith('mwc-maint-')}
    report = {'pretalx':importlib.metadata.version('pretalx'),
              'django':importlib.metadata.version('Django'),
              'python':sys.version.split()[0],
              'business':fingerprints(data/'db.sqlite3'),
              'media_files_by_hashed_path':files,
              'secret_sha256':hashlib.sha256((data/'.secret').read_bytes()).hexdigest()}
    print('PX_PROBE '+json.dumps(report,sort_keys=True))


if __name__ == '__main__':
    probe()
