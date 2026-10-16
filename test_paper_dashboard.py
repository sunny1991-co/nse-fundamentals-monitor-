import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from werkzeug.security import generate_password_hash
import app as dashboard
from research_monitor.paper_approval import PaperApprovals


class DashboardPaperTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.rootpatch=patch.object(dashboard,'ROOT',self.root)
        self.rootpatch.start();self.addCleanup(self.rootpatch.stop)
        self.app=dashboard.create_app(dict(APP_SECRET_KEY='test-secret-key-at-least-32-characters',
            APP_PASSWORD_HASH=generate_password_hash('testing'),
            APP_TOTP_SECRET='JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP',
            APP_AUTH_DB=str(self.root/'auth.sqlite'), APP_HOST='localhost'))
        self.client=self.app.test_client()
        self.data=dict(symbol='TEST',strategy='rehearsal-v1',broker='GROWW',
            capital='100000',entry_min='100',entry_max='101',stop='95',target='113',
            fee_fraction='.003',csrf='test-csrf')

    def authenticate(self):
        mocked=patch.object(self.app.extensions['auth_store'],'valid',return_value=True)
        mocked.start();self.addCleanup(mocked.stop)
        with self.client.session_transaction() as session:
            session['csrf']='test-csrf';session['auth']='test-only-session'

    def test_mfa_session_required(self):
        self.assertEqual(self.client.get('/paper',base_url='https://localhost').status_code,302)
        self.assertFalse((self.root/'runtime'/'paper_approvals.sqlite3').exists())

    def test_csrf_blocks_create(self):
        self.authenticate()
        self.assertEqual(self.client.post('/paper/create',data={**self.data,'csrf':'bad'},base_url='https://localhost').status_code,403)
        self.assertFalse((self.root/'runtime'/'paper_approvals.sqlite3').exists())

    def test_review_then_yes_paper_only_and_duplicate_blocked(self):
        self.authenticate()
        response=self.client.post('/paper/create',data=self.data,base_url='https://localhost')
        self.assertEqual(response.status_code,302)
        pid=response.location.rsplit('/',1)[1]
        book=PaperApprovals(self.root/'runtime'/'paper_approvals.sqlite3')
        row=book.get(pid)
        self.assertEqual(row['state'],'PENDING')
        review=self.client.get(response.location,base_url='https://localhost')
        self.assertIn('Yes — paper only'.encode(),review.data)
        body=dict(csrf='test-csrf',digest=row['digest'],answer='yes')
        url='/paper/'+pid+'/decide'
        self.assertEqual(self.client.post(url,data=body,base_url='https://localhost').status_code,302)
        self.assertEqual(book.get(pid)['state'],'APPROVED_PAPER')
        self.assertFalse(book.get(pid)['plan']['live_eligible'])
        self.assertEqual(self.client.post(url,data=body,base_url='https://localhost').status_code,409)

    def test_invalid_capital_and_html_symbol_rejected(self):
        self.authenticate()
        for change in [dict(capital='nan'),dict(symbol='<script>bad</script>')]:
            r=self.client.post('/paper/create',data={**self.data,**change},base_url='https://localhost')
            self.assertEqual(r.status_code,400)
            self.assertNotIn(b'<script>',r.data)

    def test_readonly_status_does_not_create_ledger(self):
        self.authenticate()
        r=self.client.get('/paper',base_url='https://localhost')
        self.assertEqual(r.status_code,200)
        self.assertIn(b'Not enrolled',r.data)
        self.assertFalse((self.root/'runtime'/'paper_approvals.sqlite3').exists())

if __name__=='__main__':
    unittest.main()
