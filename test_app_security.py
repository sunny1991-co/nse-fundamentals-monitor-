import tempfile
import unittest
from pathlib import Path
from werkzeug.security import generate_password_hash
from research_monitor.auth import AuthStore
from app import create_app

# Test-only values. No account credentials are used.
SECRET='JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP'
PASSWORD='test-only-password-not-for-deployment'
HASH=generate_password_hash(PASSWORD,method='scrypt')

class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=AuthStore(Path(self.tmp.name)/'test.sqlite',HASH,SECRET)
    def tearDown(self):
        self.tmp.cleanup()
    def test_password_alone_cannot_login(self):
        self.assertIsNone(self.store.login(PASSWORD,'',now=3000))
    def test_mfa_success_and_replay_block(self):
        code=self.store.totp.at(3000)
        token=self.store.login(PASSWORD,code,now=3000)
        self.assertTrue(self.store.valid(token,now=3001))
        self.assertIsNone(self.store.login(PASSWORD,code,now=3002))
    def test_wrong_password_with_right_code(self):
        self.assertIsNone(self.store.login('wrong',self.store.totp.at(3000),now=3000))
    def test_account_lockout(self):
        for _ in range(5):
            self.store.login('wrong','000000',now=3000)
        self.assertIsNone(self.store.login(PASSWORD,self.store.totp.at(3030),now=3030))
        self.assertIsNotNone(self.store.login(PASSWORD,self.store.totp.at(3930),now=3930))
    def test_expiry_and_logout(self):
        token=self.store.login(PASSWORD,self.store.totp.at(3000),now=3000)
        self.assertFalse(self.store.valid(token,now=3900))
        token=self.store.login(PASSWORD,self.store.totp.at(3930),now=3930)
        self.store.logout(token)
        self.assertFalse(self.store.valid(token,now=3931))
    def test_recovery_single_use_and_password_required(self):
        code='recovery-test-one-time-code'
        self.store.replace_recovery_codes([code])
        self.assertIsNone(self.store.login('wrong',code,now=3000))
        self.assertIsNotNone(self.store.login(PASSWORD,code,now=3001))
        self.assertIsNone(self.store.login(PASSWORD,code,now=3002))
    def app(self):
        return create_app(dict(APP_SECRET_KEY='test-only-cookie-secret-32-characters-long',APP_PASSWORD_HASH=HASH,
            APP_TOTP_SECRET=SECRET,APP_AUTH_DB=str(Path(self.tmp.name)/'web.sqlite'),APP_HOST='localhost'))
    def test_http_csrf_and_private_dashboard(self):
        client=self.app().test_client()
        self.assertEqual(client.get('/').status_code,400)
        self.assertEqual(client.get('/',base_url='https://localhost').status_code,302)
        self.assertEqual(client.post('/login',base_url='https://localhost',data={'password':PASSWORD}).status_code,403)
        r=client.get('/login',base_url='https://localhost')
        self.assertEqual(r.status_code,200)
        cookie=r.headers['Set-Cookie']
        for flag in ['Secure','HttpOnly','SameSite=Strict']:
            self.assertIn(flag,cookie)
        self.assertIn("frame-ancestors 'none'",r.headers['Content-Security-Policy'])
    def test_loopback_http_requires_explicit_local_mode_and_loopback_client(self):
        app=create_app(dict(APP_SECRET_KEY='test-only-cookie-secret-32-characters-long',
            APP_PASSWORD_HASH=HASH,APP_TOTP_SECRET=SECRET,
            APP_AUTH_DB=str(Path(self.tmp.name)/'local.sqlite'),APP_HOST='localhost',
            LOCAL_LOOPBACK_ONLY='1'))
        client=app.test_client()
        r=client.get('/login',base_url='http://localhost')
        self.assertEqual(r.status_code,200)
        self.assertNotIn('Secure',r.headers['Set-Cookie'])
        self.assertNotIn('Strict-Transport-Security',r.headers)
        external=client.get('/login',base_url='http://localhost',
                            environ_base={'REMOTE_ADDR':'192.0.2.10'})
        self.assertEqual(external.status_code,400)

    def test_loopback_mode_refuses_non_loopback_host(self):
        with self.assertRaises(RuntimeError):
            create_app(dict(APP_SECRET_KEY='test-only-cookie-secret-32-characters-long',
                APP_PASSWORD_HASH=HASH,APP_TOTP_SECRET=SECRET,
                APP_AUTH_DB=str(Path(self.tmp.name)/'bad.sqlite'),APP_HOST='0.0.0.0',
                LOCAL_LOOPBACK_ONLY='1'))

    def test_startup_without_security_refused(self):
        with self.assertRaises(RuntimeError):
            create_app({})

if __name__=='__main__': unittest.main()
