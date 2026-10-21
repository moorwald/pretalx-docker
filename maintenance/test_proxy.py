"""Synthetic proxy/maintenance tests in a private Docker network, no public ports."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
BACKEND = '''from http.server import BaseHTTPRequestHandler,HTTPServer
class H(BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(200);self.end_headers();self.wfile.write(self.headers.get('X-Forwarded-Proto','').encode())
 def log_message(self,*a):pass
HTTPServer(('0.0.0.0',80),H).serve_forever()
'''
CLIENT = '''import json,urllib.request,urllib.error
r=urllib.request.Request('http://proxy'+__import__('sys').argv[1],headers={'X-Forwarded-Proto':'https'})
try:s=urllib.request.urlopen(r,timeout=3)
except urllib.error.HTTPError as e:s=e
print(json.dumps({'status':s.status,'headers':dict(s.headers),'body':s.read().decode(errors='replace')}))
'''


def docker(*args):
    r=subprocess.run(['docker',*args],capture_output=True,text=True,timeout=60)
    if r.returncode: raise RuntimeError('Synthetic Docker operation failed: '+r.stderr[:500])
    return r.stdout


def run():
    suffix=uuid.uuid4().hex[:10]
    network='pretalx-proxy-qa-'+suffix
    backend=network+'-backend'
    proxy=network+'-proxy'
    created=[]
    docker('network','create','--internal',network)
    try:
        with tempfile.TemporaryDirectory(prefix='pretalx-synthetic-media-') as temp:
            public=Path(temp);public.chmod(0o755)
            media=public/'media';media.mkdir()
            for name,payload in [('unsafe.html',b'<script>synthetic</script>'),('unsafe.svg',b'<svg/>'),('photo.jpg',b'\xff\xd8\xff\xdb'),('document.pdf',b'%PDF-synthetic'),('mwc-maint-'+('a'*32)+'.cms',b'synthetic ciphertext fixture')]:
                (media/name).write_bytes(payload)
            docker('run','-d','--name',backend,'--network',network,'--network-alias','pretalx',
                   '--entrypoint','python','mwc-pretalx:20261021','-c',BACKEND)
            created.append(backend)
            checks={}
            for hold in [False,True]:
                args=['run','-d','--name',proxy,'--network',network,'--network-alias','proxy',
                      '-v',str(public)+':/public:ro']
                if hold:args+=['-v',str(ROOT/'maintenance/nginx-hold.conf')+':/etc/nginx/conf.d/default.conf:ro']
                docker(*args,'mwc-pretalx-nginx:20261021');created.append(proxy)
                for attempt in range(40):
                    try:
                        result=json.loads(docker('exec',backend,'python','-c',CLIENT,'/'))
                        break
                    except RuntimeError:
                        time.sleep(0.1)
                else:raise RuntimeError('Synthetic proxy did not become ready')
                if hold:
                    assert result['status']==503
                    denied=json.loads(docker('exec',backend,'python','-c',CLIENT,'/media/photo.jpg'))
                    assert denied['status']==503
                    allowed=json.loads(docker('exec',backend,'python','-c',CLIENT,'/media/mwc-maint-'+('a'*32)+'.cms'))
                    assert allowed['status']==200 and allowed['headers']['Cache-Control']=='no-store'
                    checks['maintenance']={'root':503,'ordinary_media':503,'encrypted_backup_fixture':200}
                else:
                    assert result['status']==200 and result['body']=='https'
                    for file,kind in [('unsafe.html','text/plain'),('unsafe.svg','text/plain'),('photo.jpg','image/jpeg'),('document.pdf','application/pdf')]:
                        response=json.loads(docker('exec',backend,'python','-c',CLIENT,'/media/'+file))
                        assert response['status']==200
                        assert response['headers']['Content-Type']==kind
                        assert response['headers']['X-Content-Type-Options']=='nosniff'
                        assert response['headers']['Content-Disposition']=='attachment'
                    checks['normal']={'https_forwarding':True,'unsafe_media_nonexecutable':True,'images_and_pdfs_typed':True,'attachment_and_nosniff':True}
                docker('rm','-fv',proxy);created.remove(proxy)
            print(json.dumps(checks,indent=2))
    finally:
        for name in reversed(created):docker('rm','-fv',name)
        docker('network','rm',network)


if __name__=='__main__':run()
