"""Disposable, offline SQLite restore, Pretalx upgrade and file/database rollback.

No published ports, external mail, production writes or workers. Reports contain
only status codes, counts and hashes, never proposal text, user data or secrets.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tarfile
import time
import uuid

BASELINE = 'pretalx/standalone:v2025.2.2'
TARGET = 'pretalx/standalone:v2026.2.1'
FIELDS = {
    'event_event': ['id', 'slug', 'name', 'custom_css', 'is_public', 'organiser_id'],
    'person_user': ['id', 'email', 'password', 'name', 'code', 'is_active', 'is_administrator'],
    'person_speakerprofile': ['id', 'event_id', 'user_id', 'biography'],
    'submission_submission': ['id', 'code', 'event_id', 'title', 'state', 'abstract', 'description'],
    'submission_answer': ['id', 'question_id', 'submission_id', 'person_id', 'answer', 'answer_file'],
    'schedule_schedule': ['id', 'event_id', 'version', 'published'],
}

CHECK = r'''
import json, logging
logging.disable(logging.CRITICAL)
from django.conf import settings
from django.test import Client
from django.contrib.auth import get_user_model
from pretalx.event.models import Event
from pretalx.submission.models import Submission
checks = {}
client = Client(raise_request_exception=True, HTTP_HOST='cfp.mautic.org')
for name, path in [('health','/healthcheck/'),('event','/mwc26/'),('login','/orga/login/'),('public_api','/api/events/mwc26/')]:
    response = client.get(path, secure=True, follow=True)
    checks[name] = response.status_code
    assert response.status_code == 200, name + ' status ' + str(response.status_code)
print('PX_STAGE public', flush=True)
user = get_user_model().objects.filter(is_administrator=True, is_active=True).first()
assert user is not None, 'No administrator for isolated existing-user regression'
client.force_login(user, backend='django.contrib.auth.backends.ModelBackend')
for name, path in [('organiser','/orga/event/mwc26/'),('submissions','/orga/event/mwc26/submissions/'),('speakers','/orga/event/mwc26/speakers/')]:
    response = client.get(path, secure=True, follow=True)
    checks[name] = response.status_code
    assert response.status_code == 200, name + ' status ' + str(response.status_code)
print('PX_STAGE private', flush=True)
event = Event.objects.get(slug='mwc26')
submission = Submission.objects.filter(event=event).first()
assert submission is not None, 'No existing proposal for regression'
response = client.get('/orga/event/mwc26/submissions/'+submission.code+'/', secure=True, follow=True)
checks['existing_proposal'] = response.status_code
assert response.status_code == 200, 'Existing proposal status '+str(response.status_code)
checks['cache_backend'] = settings.CACHES['default']['BACKEND']
checks['secret_file_preserved'] = (settings.DATA_DIR / '.secret').is_file()
print('PX_QA '+json.dumps(checks), flush=True)
'''


def docker(*args, timeout=180):
    r = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode:
        classes = re.findall(r'^([A-Za-z_.]*(?:Error|Exception)):', r.stderr, re.M)
        statuses = re.findall(r'^AssertionError: ([A-Za-z_ ]+status [0-9]+)', r.stderr, re.M)
        frames = re.findall(r'File "([^"]+)", line ([0-9]+)', r.stderr)
        raise RuntimeError(f'Local Docker QA failed ({r.returncode}), classes={classes[-2:]}, statuses={statuses}, frames={frames[-8:]}; raw response withheld')
    return r.stdout


def extract(folder, mode):
    clear = folder / 'pretalx-clear'
    clear.mkdir(mode=0o700, exist_ok=True)
    if not (clear / 'db.sqlite3').exists():
        with tarfile.open(folder / 'pretalx.tar') as archive:
            assert set(archive.getnames()) == {'db.sqlite3', 'data.tgz'}
            archive.extractall(clear, filter='data')
    destination = folder / ('pretalx-' + mode)
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(mode=0o700)
    with tarfile.open(clear / 'data.tgz') as archive:
        archive.extractall(destination, filter='data')
    shutil.copy2(clear / 'db.sqlite3', destination / 'data/db.sqlite3')
    assert (destination / 'data/.secret').is_file()
    return destination


def fingerprints(database):
    result = {}
    with sqlite3.connect('file:' + str(database) + '?mode=ro', uri=True) as connection:
        assert connection.execute('pragma integrity_check').fetchone()[0] == 'ok'
        for table, columns in FIELDS.items():
            available = {row[1] for row in connection.execute('pragma table_info(' + table + ')')}
            if table == 'submission_answer' and 'speaker_id' in available and 'person_id' not in available:
                # Upstream moves speaker answers from User to event-specific SpeakerProfile.
                # Compare the same original user relation, not the new profile primary key.
                assert set(columns) - {'person_id'} <= available
                selected = ['profile.user_id' if field == 'person_id' else 'answer.' + field for field in columns]
                rows = connection.execute('select '+','.join(selected)+' from submission_answer answer left join person_speakerprofile profile on profile.id=answer.speaker_id order by answer.id').fetchall()
            else:
                assert set(columns) <= available, 'Required business fields changed in ' + table
                rows = connection.execute('select '+','.join(columns)+' from '+table+' order by id').fetchall()
            result[table] = {'count':len(rows),'sha256':hashlib.sha256(json.dumps(rows,default=str,separators=(',',':')).encode()).hexdigest()}
    return result


def media_hashes(folder):
    base = folder / 'public/media'
    return {str(path.relative_to(base)):hashlib.sha256(path.read_bytes()).hexdigest()
            for path in base.rglob('*') if path.is_file()}


def run(folder):
    os.umask(0o077)
    suffix = uuid.uuid4().hex[:10]
    network = 'pretalx-upgrade-qa-' + suffix
    redis = network + '-redis'
    docker('network','create','--internal',network)
    report = {'network_internal':True,'published_ports':False,'workers_started':False,'baseline_image':BASELINE,'target_image':TARGET,'comparison_normalization':{'submission_answer':'Map upgraded speaker_id through SpeakerProfile.user_id to compare the original person_id relation'}}
    try:
        docker('run','-d','--name',redis,'--network',network,'--network-alias','redis','redis:7-alpine')
        for _ in range(30):
            r = subprocess.run(['docker','exec',redis,'redis-cli','ping'],capture_output=True,text=True)
            if r.returncode == 0 and r.stdout.strip() == 'PONG': break
            time.sleep(1)
        else: raise TimeoutError('Isolated Redis did not start')
        baseline_media = None
        for index, (mode,image) in enumerate([('baseline',BASELINE),('upgrade',TARGET),('rollback',BASELINE)]):
            local = extract(folder,mode)
            original_media = media_hashes(local)
            original_secret = hashlib.sha256((local/'data/.secret').read_bytes()).hexdigest()
            original = fingerprints(local/'data/db.sqlite3')
            if mode == 'baseline':
                report['original'] = original
                baseline_media = original_media
            if mode == 'rollback':
                # A fresh restore of the matching original DB AND files, after a real upgraded copy existed.
                assert original == report['original']
            args=['run','--rm','--network',network,'--user','999:999',
                  '-v',f'{local / "data"}:/data','-v',f'{local / "public"}:/public',
                  '-e','PRETALX_DATA_DIR=/data','-e','PRETALX_DB_TYPE=sqlite3','-e','PRETALX_DB_NAME=/data/db.sqlite3',
                  '-e','PRETALX_SITE_URL=https://cfp.mautic.org','-e','PRETALX_DEBUG=False','-e','PRETALX_HTTPS=True',
                  '-e','PRETALX_FILESYSTEM_MEDIA=/public/media','-e','PRETALX_FILESYSTEM_STATIC=/public/static',
                  '-e','PRETALX_REDIS=redis://redis:6379/'+str(index*3),
                  '-e','PRETALX_REDIS_SESSIONS=false',
                  '-e','PRETALX_CELERY_BROKER=redis://redis:6379/'+str(index*3+1),
                  '-e','PRETALX_CELERY_BACKEND=redis://redis:6379/'+str(index*3+2),
                  '-e','PRETALX_MAIL_HOST=127.0.0.1','-e','PRETALX_MAIL_FROM=isolated-test@example.invalid',
                  '--entrypoint','python',image,'-m','pretalx']
            # The vendor's /pretalx directory is mode 0700, owned by UID 999.
            # Match its real runtime UID, while keeping the host parent mode 0700.
            ownership=['run','--rm','--network','none','--user','0:0',
                       '-v',f'{local / "data"}:/data','-v',f'{local / "public"}:/public',
                       '--entrypoint','chown',TARGET,'-R']
            docker(*ownership,'999:999','/data','/public')
            try:
                if mode == 'upgrade':
                    out=docker(*args,'migrate','--noinput',timeout=600)
                    report['applied_migrations']=[line.strip() for line in out.splitlines() if line.strip().startswith('Applying ')]
                    docker(*args,'rebuild',timeout=600)
                    out=docker(*args,'check','--deploy',timeout=120)
                    report['deploy_check_passed']=True
                out=docker(*args,'shell','--unsafe-disable-scopes','-c',CHECK,timeout=180)
            finally:
                docker(*ownership,f'{os.getuid()}:{os.getgid()}','/data','/public')
            lines=[line for line in out.splitlines() if line.startswith('PX_QA ')]
            assert len(lines)==1, 'Missing sanitized QA result'
            checked=json.loads(lines[0].removeprefix('PX_QA '))
            after=fingerprints(local/'data/db.sqlite3')
            assert after==report['original'], 'Business/authentication fields changed unexpectedly'
            after_media=media_hashes(local)
            assert all(after_media.get(path)==digest for path,digest in baseline_media.items()), 'An existing media file changed'
            assert hashlib.sha256((local/'data/.secret').read_bytes()).hexdigest()==original_secret, 'Application secret changed'
            report[mode]={'checks':checked,'business_fields_preserved':True,'original_media_files_preserved':len(original_media),'secret_preserved':True}
            print(mode+': passed scoped public/private HTTP, SQLite integrity, business/authentication and media checks',flush=True)
        report['database_and_file_rollback_proven']=True
        (folder/'pretalx-restore-upgrade-qa.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2))
    finally:
        r=subprocess.run(['docker','rm','-fv',redis],capture_output=True,text=True)
        if r.returncode and 'No such container' not in r.stderr: raise RuntimeError('Failed to remove isolated Redis')
        docker('network','rm',network)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('directory',type=Path)
    run(parser.parse_args().directory.resolve())
