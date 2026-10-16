"""Linux deployment smoke test; only temporary synthetic credentials/data. No broker calls."""
import json, os, re, signal, socket, subprocess, sys, tempfile, time
from pathlib import Path
import pyotp, requests
from werkzeug.security import generate_password_hash
root=Path.cwd()
with tempfile.TemporaryDirectory() as temp:
    data=Path(temp)/'data'
    s=socket.socket();s.bind(('127.0.0.1',0));port=s.getsockname()[1];s.close()
    secret=pyotp.random_base32()
    env=dict(os.environ,RENDER='true',NSE_DATA_DIR=str(data),PORT=str(port),
        RENDER_EXTERNAL_HOSTNAME='localhost',APP_SECRET_KEY='test-only-'+'x'*48,
        APP_PASSWORD_HASH=generate_password_hash('test-only-password'),APP_TOTP_SECRET=secret)
    env.pop('UPSTOX_ANALYTICS_TOKEN',None)
    client=requests.Session();base=f'http://localhost:{port}'
    # Secure cookies cannot be sent by requests over loopback HTTP. Browser-level
    # production HTTPS still requires Render acceptance.
    headers={'X-Forwarded-Proto':'https'}
    with open(Path(temp)/'process.log','w+') as log:
        p=None
        try:
            for attempt in range(2):
                p=subprocess.Popen([sys.executable,'cloud_start.py'],env=env,stdout=log,stderr=log)
                for _ in range(100):
                    if p.poll() is not None: raise AssertionError('launcher exited')
                    try:
                        if client.get(base+'/healthz',timeout=1).status_code==200: break
                    except requests.RequestException: pass
                    time.sleep(.1)
                else: raise AssertionError('health timed out')
                assert client.get(base+'/login',timeout=2).status_code==400
                r=client.get(base+'/login',headers=headers,timeout=2)
                assert r.status_code==200
                csrf=re.search('name="csrf" value="([^"]+)"',r.text)[1]
                cookie=r.headers['Set-Cookie'].split(';')[0]
                if attempt == 0: login_code = pyotp.TOTP(secret).now()
                r=client.post(base+'/login',headers=dict(headers,Cookie=cookie),data={
                    'csrf':csrf,'password':'test-only-password','code':login_code},allow_redirects=False,timeout=3)
                if attempt==0:
                    assert r.status_code==302
                    auth_cookie=r.headers['Set-Cookie'].split(';')[0]
                else:
                    assert r.status_code==200 and 'Login नहीं हुआ' in r.text  # replay rejected after restart
                assert client.get(base+'/',headers=dict(headers,Cookie=auth_cookie),timeout=3).status_code==200
                for _ in range(100):
                    if (data/'runtime'/'readiness.json').exists(): break
                    time.sleep(.1)
                assert (data/'runtime'/'readiness.json').exists()
                status=json.loads((data/'runtime'/'readiness.json').read_text())
                assert status['historical_data']=='MISSING'
                if attempt==0:
                    p.terminate();assert p.wait(timeout=20)==0
                    (data/'runtime'/'restart_marker').write_text('persisted')
                else:
                    assert (data/'runtime'/'restart_marker').read_text()=='persisted'
                    children=json.loads((data/'runtime'/'cloud_health.json').read_text())['child_pids']
                    os.kill(int(children[0]),signal.SIGKILL)
                    assert p.wait(timeout=20)==1
                    assert not (data/'runtime'/'cloud_health.json').exists()
            print('PASS: real Gunicorn + worker startup, HTTP guards, login, auth persistence/replay after restart, offline readiness, child-failure shutdown')
        except Exception:
            log.flush();log.seek(0);print(log.read()[-3500:]);raise
        finally:
            if p and p.poll() is None:p.terminate();p.wait(timeout=20)
