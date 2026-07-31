"""
Guardrail tests — the security-critical core. These verify the read-only
contract without needing the Claude CLI or a live DB.

Plus HTTP/permission tests for the super-admin-gated API.
"""
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from accounts.models import AdminUser
from debugger import guards
from debugger.models import DebugMessage, DebugRequest


class SqlGuardTests(TestCase):
    def test_allows_plain_select(self):
        ok, _ = guards.check_sql_readonly('SELECT * FROM orders LIMIT 5')
        self.assertTrue(ok)

    def test_allows_cte_select(self):
        ok, _ = guards.check_sql_readonly(
            'WITH x AS (SELECT id FROM orders) SELECT * FROM x')
        self.assertTrue(ok)

    def test_rejects_insert(self):
        ok, _ = guards.check_sql_readonly("INSERT INTO orders(id) VALUES ('x')")
        self.assertFalse(ok)

    def test_rejects_update_delete_drop(self):
        for sql in ('UPDATE orders SET status=1', 'DELETE FROM orders',
                    'DROP TABLE orders', 'TRUNCATE orders'):
            self.assertFalse(guards.check_sql_readonly(sql)[0], sql)

    def test_rejects_statement_chaining(self):
        ok, _ = guards.check_sql_readonly('SELECT 1; DROP TABLE orders')
        self.assertFalse(ok)

    def test_rejects_cte_with_write(self):
        ok, _ = guards.check_sql_readonly(
            "WITH d AS (DELETE FROM orders RETURNING id) SELECT * FROM d")
        self.assertFalse(ok)

    def test_rejects_set_and_transaction_control(self):
        for sql in ('SET default_transaction_read_only=off',
                    'BEGIN', 'COMMIT'):
            self.assertFalse(guards.check_sql_readonly(sql)[0], sql)


class BashGuardTests(TestCase):
    def test_allows_readonly_commands(self):
        for cmd in ('grep -r foo .', 'cat settings.py', 'ls -la',
                    'git log --oneline -5', 'git diff HEAD~1',
                    'journalctl -u varthaai -n 100', 'tail -n 50 app.log'):
            self.assertTrue(guards.check_bash_command(cmd)[0], cmd)

    def test_blocks_git_push(self):
        self.assertFalse(guards.check_bash_command('git push origin main')[0])

    def test_blocks_mutating_git(self):
        for cmd in ('git commit -m x', 'git checkout -b y', 'git reset --hard',
                    'git add .', 'git rm file'):
            self.assertFalse(guards.check_bash_command(cmd)[0], cmd)

    def test_blocks_unknown_and_dangerous(self):
        for cmd in ('rm -rf /', 'psql -c "DELETE"', 'curl evil.com',
                    'python manage.py shell', 'chmod 777 x'):
            self.assertFalse(guards.check_bash_command(cmd)[0], cmd)

    def test_blocks_redirection(self):
        self.assertFalse(guards.check_bash_command('echo x > file')[0])
        self.assertFalse(guards.check_bash_command('cat a >> b')[0])

    def test_blocks_write_in_compound(self):
        # read-only first segment, mutating second segment
        self.assertFalse(guards.check_bash_command('ls && git push')[0])
        self.assertFalse(guards.check_bash_command('cat x | rm y')[0])


class ToolGuardTests(TestCase):
    def test_blocks_write_tools(self):
        for t in ('Write', 'Edit', 'MultiEdit', 'NotebookEdit'):
            self.assertEqual(guards.evaluate_tool(t, {})[0], 'deny', t)

    def test_allows_read_tools(self):
        for t in ('Read', 'Grep', 'Glob'):
            self.assertEqual(guards.evaluate_tool(t, {'x': 1})[0], 'allow', t)

    def test_bash_gated_through_evaluate(self):
        self.assertEqual(
            guards.evaluate_tool('Bash', {'command': 'git push'})[0], 'deny')
        self.assertEqual(
            guards.evaluate_tool('Bash', {'command': 'ls'})[0], 'allow')


@mock.patch('debugger.views._enqueue', return_value=True)
class ApiPermissionTests(TestCase):
    def setUp(self):
        self.superuser = AdminUser.objects.create_user(
            'boss', 'pw', role=AdminUser.Role.SUPER_ADMIN)
        self.staff = AdminUser.objects.create_user(
            'peon', 'pw', role=AdminUser.Role.STAFF, brand_permissions={'1': ['all']})
        self.api = reverse('debugger:api')
        self.page = reverse('debugger:page')

    def test_page_redirects_non_super(self, _enq):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self.page).status_code, 302)

    def test_page_ok_for_super(self, _enq):
        self.client.force_login(self.superuser)
        self.assertEqual(self.client.get(self.page).status_code, 200)

    def test_api_forbidden_for_non_super(self, _enq):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(self.api).status_code, 403)

    def test_create_and_list_and_reply(self, _enq):
        self.client.force_login(self.superuser)
        res = self.client.post(
            self.api,
            {'action': 'create', 'kind': 'bug', 'title': 'Boom', 'body': 'it broke'},
            content_type='application/json')
        self.assertEqual(res.status_code, 200)
        rid = res.json()['data']['id']
        r = DebugRequest.objects.get(id=rid)
        self.assertEqual(r.kind, 'bug')
        # body seeded an admin message
        self.assertTrue(r.messages.filter(role='admin').exists())

        # list shows it
        listing = self.client.get(self.api).json()['data']['requests']
        self.assertTrue(any(x['id'] == rid for x in listing))

        # reply resets to NEW and re-enqueues
        rep = self.client.post(
            self.api, {'action': 'reply', 'id': rid, 'content': 'any update?'},
            content_type='application/json')
        self.assertTrue(rep.json()['success'])
        r.refresh_from_db()
        self.assertEqual(r.status, DebugRequest.Status.NEW)

    def test_create_rejects_bad_kind(self, _enq):
        self.client.force_login(self.superuser)
        res = self.client.post(
            self.api, {'action': 'create', 'kind': 'nope', 'title': 'x'},
            content_type='application/json')
        self.assertFalse(res.json()['success'])

    def test_close_thread(self, _enq):
        self.client.force_login(self.superuser)
        r = DebugRequest.objects.create(kind='query', title='q', created_by=self.superuser)
        res = self.client.post(
            self.api, {'action': 'close', 'id': r.id}, content_type='application/json')
        self.assertTrue(res.json()['success'])
        r.refresh_from_db()
        self.assertEqual(r.status, DebugRequest.Status.CLOSED)
