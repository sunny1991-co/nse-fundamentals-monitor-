"""Single-owner password+TOTP authentication with persistent replay/lockout state."""
import hashlib
import hmac
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
import pyotp
from werkzeug.security import check_password_hash

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

class AuthStore:
    def __init__(self,path,password_hash,totp_secret):
        if not password_hash.startswith('scrypt:') or len(totp_secret)<32:
            raise ValueError('Strong password hash and TOTP enrollment required')
        self.path=str(path);self.password_hash=password_hash;self.totp=pyotp.TOTP(totp_secret)
        self.totp.now() # Invalid enrollment must stop startup.
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        with self.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS auth_state
                (id INTEGER PRIMARY KEY CHECK(id=1), failures INTEGER, locked_until REAL, last_counter INTEGER);
                INSERT OR IGNORE INTO auth_state VALUES(1,0,0,-1);
                CREATE TABLE IF NOT EXISTS sessions (hash TEXT PRIMARY KEY, created REAL, seen REAL);
                CREATE TABLE IF NOT EXISTS recovery (hash TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS audit (at REAL, event TEXT);''')
        Path(path).chmod(0o600)

    @contextmanager
    def db(self):
        connection=sqlite3.connect(self.path,timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def login(self,password,code,now=None):
        now=time.time() if now is None else now
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            failures,locked,last=db.execute('SELECT failures,locked_until,last_counter FROM auth_state WHERE id=1').fetchone()
            if now<locked:
                return None
            if locked and now>=locked:
                failures=0
            matched=None
            valid_password=check_password_hash(self.password_hash,password)
            if valid_password and len(code)==6 and code.isascii() and code.isdigit():
                for counter in range(int(now//30)-1,int(now//30)+2):
                    if counter>last and hmac.compare_digest(self.totp.at(counter*30),code):
                        matched=counter
                        break
            recovery=False
            if valid_password and matched is None and code.startswith('recovery-'):
                recovery=db.execute('DELETE FROM recovery WHERE hash=?',(digest(code),)).rowcount==1
            if matched is None and not recovery:
                failures+=1
                db.execute('UPDATE auth_state SET failures=?,locked_until=? WHERE id=1',(failures,now+900 if failures>=5 else 0))
                db.execute('INSERT INTO audit VALUES(?,?)',(now,'LOGIN_FAILED'))
                return None
            db.execute('UPDATE auth_state SET failures=0,locked_until=0,last_counter=? WHERE id=1',(matched if matched is not None else last,))
            if recovery:
                db.execute('DELETE FROM sessions')
            token=secrets.token_urlsafe(32)
            db.execute('DELETE FROM sessions WHERE created<? OR seen<?',(now-28800,now-900))
            db.execute('INSERT INTO sessions VALUES(?,?,?)',(digest(token),now,now))
            db.execute('INSERT INTO audit VALUES(?,?)',(now,'LOGIN_RECOVERY' if recovery else 'LOGIN_MFA'))
            return token

    def valid(self,token,now=None):
        if not token:
            return False
        now=time.time() if now is None else now
        with self.db() as db:
            row=db.execute('SELECT created,seen FROM sessions WHERE hash=?',(digest(token),)).fetchone()
            if not row or not 0<=now-row[0]<28800 or not 0<=now-row[1]<900:
                db.execute('DELETE FROM sessions WHERE hash=?',(digest(token),))
                return False
            db.execute('UPDATE sessions SET seen=? WHERE hash=?',(now,digest(token)))
            return True

    def logout(self,token):
        with self.db() as db:
            db.execute('DELETE FROM sessions WHERE hash=?',(digest(token),))
            db.execute('INSERT INTO audit VALUES(?,?)',(time.time(),'LOGOUT'))

    def replace_recovery_codes(self,codes):
        # Server-admin provisioning only: never exposed as a public HTTP route.
        with self.db() as db:
            db.execute('DELETE FROM recovery');db.execute('DELETE FROM sessions')
            db.executemany('INSERT INTO recovery VALUES(?)',[(digest(c),) for c in codes])
