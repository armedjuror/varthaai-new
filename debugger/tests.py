"""
Guardrail tests — the security-critical core. These verify the read-only
contract without needing the Claude CLI or a live DB.

Plus HTTP/permission tests for the super-admin-gated API.
"""
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import AdminUser
from debugger import guards, pr
from debugger.models import DebugLearning, DebugMessage, DebugRequest


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

    @mock.patch('debugger.tasks.finalize_learning.delay')
    def test_close_starts_finalization(self, delay, _enq):
        self.client.force_login(self.superuser)
        r = DebugRequest.objects.create(kind='query', title='q', created_by=self.superuser)
        res = self.client.post(
            self.api, {'action': 'close', 'id': r.id}, content_type='application/json')
        self.assertTrue(res.json()['success'])
        r.refresh_from_db()
        self.assertEqual(r.status, DebugRequest.Status.FINALIZING)
        delay.assert_called_once_with(r.id)


@override_settings(GITHUB_DEFAULT_BRANCH='main')
class PrLogicTests(TestCase):
    def test_slugify(self):
        self.assertEqual(pr.slugify('Order total is Wrong!!'), 'order-total-is-wrong')
        self.assertEqual(pr.slugify(''), 'change')

    def test_branch_name(self):
        r = DebugRequest(id=7, kind='bug', title='Cart breaks on empty pincode')
        self.assertEqual(pr.branch_name(r), 'debugger/bug-7-cart-breaks-on-empty-pincode')

    def test_pr_number_from_url(self):
        self.assertEqual(pr.pr_number_from_url('https://github.com/a/b/pull/42'), 42)
        self.assertIsNone(pr.pr_number_from_url(''))

    def test_assert_pushable_refuses_default(self):
        with self.assertRaises(pr.PRError):
            pr._assert_pushable('main')
        with self.assertRaises(pr.PRError):
            pr._assert_pushable('')
        pr._assert_pushable('debugger/bug-1-x')  # no raise


class PrApiTests(TestCase):
    def setUp(self):
        self.superuser = AdminUser.objects.create_user(
            'boss', 'pw', role=AdminUser.Role.SUPER_ADMIN)
        self.api = reverse('debugger:api')
        self.client.force_login(self.superuser)

    def _ready_bug(self):
        return DebugRequest.objects.create(
            kind='bug', title='Boom', status=DebugRequest.Status.READY,
            proposed_diff='--- a\n+++ b\n', created_by=self.superuser)

    @mock.patch('debugger.tasks.create_pr.delay')
    def test_request_pr_enqueues(self, delay):
        r = self._ready_bug()
        res = self.client.post(
            self.api, {'action': 'request_pr', 'id': r.id}, content_type='application/json')
        self.assertTrue(res.json()['success'])
        delay.assert_called_once_with(r.id)
        r.refresh_from_db()
        self.assertEqual(r.status, DebugRequest.Status.PR_REQUESTED)

    @mock.patch('debugger.tasks.create_pr.delay')
    def test_request_pr_rejects_without_diff(self, delay):
        r = DebugRequest.objects.create(
            kind='bug', title='x', status=DebugRequest.Status.READY, created_by=self.superuser)
        res = self.client.post(
            self.api, {'action': 'request_pr', 'id': r.id}, content_type='application/json')
        self.assertFalse(res.json()['success'])
        delay.assert_not_called()

    @mock.patch('debugger.tasks.create_pr.delay')
    def test_request_pr_rejects_when_pr_open(self, delay):
        r = self._ready_bug()
        r.pr_url = 'https://github.com/a/b/pull/1'
        r.save()
        res = self.client.post(
            self.api, {'action': 'request_pr', 'id': r.id}, content_type='application/json')
        self.assertFalse(res.json()['success'])
        delay.assert_not_called()

    @mock.patch('debugger.tasks.process_request.delay')
    def test_approve_plan(self, delay):
        r = DebugRequest.objects.create(
            kind='feature', title='new report', rca='the plan',
            status=DebugRequest.Status.AWAITING_INPUT, created_by=self.superuser)
        res = self.client.post(
            self.api, {'action': 'approve_plan', 'id': r.id}, content_type='application/json')
        self.assertTrue(res.json()['success'])
        r.refresh_from_db()
        self.assertEqual(r.status, DebugRequest.Status.NEW)
        self.assertTrue(r.messages.filter(role='admin', content__icontains='approved').exists())
        delay.assert_called_once()

    @mock.patch('debugger.tasks.sync_pr_reviews.delay')
    def test_sync_pr_requires_open_pr(self, delay):
        r = self._ready_bug()
        res = self.client.post(
            self.api, {'action': 'sync_pr', 'id': r.id}, content_type='application/json')
        self.assertFalse(res.json()['success'])
        delay.assert_not_called()


class LearningTests(TestCase):
    def setUp(self):
        self.superuser = AdminUser.objects.create_user(
            'boss', 'pw', role=AdminUser.Role.SUPER_ADMIN)
        self.api = reverse('debugger:api')
        self.client.force_login(self.superuser)

    def _reviewing(self):
        return DebugRequest.objects.create(
            kind='bug', title='coupon bug',
            status=DebugRequest.Status.LEARNING_REVIEW,
            learning_title='Coupon applied after total',
            learning_draft='Apply coupons inside _total().',
            created_by=self.superuser)

    def test_finalize_close_saves_learning(self):
        r = self._reviewing()
        res = self.client.post(self.api, {
            'action': 'finalize_close', 'id': r.id,
            'title': 'Coupon ordering', 'content': 'Apply coupon inside _total() and persist.',
        }, content_type='application/json')
        self.assertTrue(res.json()['data']['saved'])
        r.refresh_from_db()
        self.assertEqual(r.status, DebugRequest.Status.CLOSED)
        lrn = DebugLearning.objects.get(source_request=r)
        self.assertEqual(lrn.kind, 'bug')
        self.assertIn('coupon', ' '.join(lrn.tags))

    def test_finalize_close_discard_saves_nothing(self):
        r = self._reviewing()
        res = self.client.post(self.api, {
            'action': 'finalize_close', 'id': r.id, 'discard': True,
        }, content_type='application/json')
        self.assertFalse(res.json()['data']['saved'])
        r.refresh_from_db()
        self.assertEqual(r.status, DebugRequest.Status.CLOSED)
        self.assertFalse(DebugLearning.objects.filter(source_request=r).exists())

    def test_recall_prefers_keyword_match(self):
        from debugger.agent import _relevant_learnings
        DebugLearning.objects.create(kind='bug', title='Unrelated caching note',
                                     content='x', tags=['cache', 'redis'])
        DebugLearning.objects.create(kind='bug', title='Coupon math',
                                     content='apply inside _total', tags=['coupon', 'total', 'orders'])
        req = DebugRequest.objects.create(
            kind='bug', title='coupon discount wrong', body='orders total off',
            created_by=self.superuser)
        out = _relevant_learnings(req)
        # The coupon learning should rank above the caching one.
        self.assertLess(out.index('Coupon math'), out.index('Unrelated caching note'))
