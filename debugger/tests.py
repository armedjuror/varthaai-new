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
        for cmd in ('grep -n foo settings.py', 'cat settings.py', 'ls -la',
                    'git log --oneline -5', 'git diff HEAD~1',
                    'journalctl -u varthaai -n 100', 'tail -n 50 app.log'):
            self.assertTrue(guards.check_bash_command(cmd)[0], cmd)

    def test_blocks_unscoped_recursive_grep(self):
        for cmd in ('grep -r foo .', 'grep -rn foo debugger/',
                    'grep --recursive foo .'):
            self.assertFalse(guards.check_bash_command(cmd)[0], cmd)

    def test_allows_recursive_grep_with_venv_excluded(self):
        ok, _ = guards.check_bash_command(
            'grep -r --exclude-dir=venv foo .')
        self.assertTrue(ok)

    def test_allows_ripgrep_unscoped(self):
        # rg respects .gitignore (which excludes venv/) so it's not restricted.
        ok, _ = guards.check_bash_command('rg foo .')
        self.assertTrue(ok)

    def test_blocks_unscoped_find(self):
        for cmd in ('find . -name "*.py"', "find -name '*.py'"):
            self.assertFalse(guards.check_bash_command(cmd)[0], cmd)

    def test_allows_find_scoped_to_subdirectory(self):
        ok, _ = guards.check_bash_command("find debugger -name '*.py'")
        self.assertTrue(ok)

    def test_allows_unscoped_find_with_venv_pruned(self):
        ok, _ = guards.check_bash_command(
            "find . -path '*/venv/*' -prune -o -name '*.py' -print")
        self.assertTrue(ok)

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


class ApplyDiffTests(TestCase):
    """Exercise the real diff-application path + the staging fix in a temp repo."""

    def _git(self, d, *args):
        import os
        import subprocess
        env = {**os.environ,
               'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t',
               'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t'}
        return subprocess.run(['git', *args], cwd=d, check=True,
                              capture_output=True, text=True, env=env)

    def test_apply_diff_applies_and_stages(self):
        import os
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self._git(d, 'init', '-q')
            fp = os.path.join(d, 'a.txt')
            with open(fp, 'w') as f:
                f.write('l1\nl2\nl3\n')
            self._git(d, 'add', '-A')
            self._git(d, 'commit', '-qm', 'init')
            # Generate a real unified diff, then revert so we can re-apply it.
            with open(fp, 'w') as f:
                f.write('l1\nCHANGED\nl3\n')
            diff = self._git(d, 'diff').stdout
            self._git(d, 'checkout', '--', 'a.txt')

            pr._apply_diff(d, diff)
            self._git(d, 'add', '-A')
            self._git(d, 'commit', '-qm', 'fix')

            self.assertIn('CHANGED', open(fp).read())
            # The change was committed (staging gap regression guard).
            show = subprocess.run(['git', 'show', '--stat', 'HEAD'], cwd=d,
                                  capture_output=True, text=True).stdout
            self.assertIn('a.txt', show)

    def test_apply_diff_rejects_unapplicable(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self._git(d, 'init', '-q')
            with open(os.path.join(d, 'a.txt'), 'w') as f:
                f.write('totally different\n')
            self._git(d, 'add', '-A')
            self._git(d, 'commit', '-qm', 'init')
            bogus = ('diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n'
                     '@@ -1,3 +1,3 @@\n l1\n-l2\n+X\n l3\n')
            with self.assertRaises(pr.PRError):
                pr._apply_diff(d, bogus)


@override_settings(GITHUB_DEFAULT_BRANCH='main')
class BuildFixBranchTests(TestCase):
    """
    The core context-drift fix: a diff built against commit A must still land on
    a default branch that has moved on to B (a non-overlapping merge), via the
    cherry-pick transplant in _build_fix_branch.
    """

    def _git(self, d, *args):
        import os
        import subprocess
        env = {**os.environ,
               'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t',
               'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t'}
        return subprocess.run(['git', *args], cwd=d, check=True,
                              capture_output=True, text=True, env=env)

    _FILE = 'header\na\nb\nc\nd\ne\nf\ng\nh\nfooter\n'

    def _make_repo(self, d):
        """commit A on main; a diff (d->FIX) built at A; then advance origin/main
        to B with a non-overlapping change (header->HEADER-B). Returns (A, diff)."""
        import os
        self._git(d, 'init', '-q', '-b', 'main')
        fp = os.path.join(d, 'file.txt')
        with open(fp, 'w') as f:
            f.write(self._FILE)
        self._git(d, 'add', '-A')
        self._git(d, 'commit', '-qm', 'A')
        sha_a = self._git(d, 'rev-parse', 'HEAD').stdout.strip()

        # A diff built against A: change the middle line 'd' -> 'FIX'.
        with open(fp, 'w') as f:
            f.write(self._FILE.replace('d\n', 'FIX\n'))
        diff = self._git(d, 'diff').stdout
        self._git(d, 'checkout', '--', 'file.txt')

        # Advance to B (a merged PR): change a far-away line, commit, publish as
        # the remote-tracking origin/main the PR branch will be based on.
        with open(fp, 'w') as f:
            f.write(self._FILE.replace('header\n', 'HEADER-B\n'))
        self._git(d, 'add', '-A')
        self._git(d, 'commit', '-qm', 'B')
        sha_b = self._git(d, 'rev-parse', 'HEAD').stdout.strip()
        self._git(d, 'update-ref', 'refs/remotes/origin/main', sha_b)
        # Move working tree back to A so it doesn't taint the checkout.
        self._git(d, 'checkout', '-q', sha_a)
        return sha_a, diff

    def test_cherry_pick_transplants_fix_onto_moved_main(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            sha_a, diff = self._make_repo(d)
            req = DebugRequest(kind='bug', title='x', base_sha=sha_a,
                               proposed_diff=diff)
            pr._build_fix_branch(d, req, 'debugger/bug-1-x', 'main', 'ttl', 'body')

            content = open(os.path.join(d, 'file.txt')).read()
            self.assertIn('FIX', content)        # the fix landed
            self.assertIn('HEADER-B', content)   # B's merge preserved
            self.assertNotIn('\nd\n', content)
            self.assertNotIn('header\n', content)

    def test_fallback_applies_onto_main_without_base_sha(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            _sha_a, diff = self._make_repo(d)
            req = DebugRequest(kind='bug', title='x', base_sha='',
                               proposed_diff=diff)
            pr._build_fix_branch(d, req, 'debugger/bug-2-x', 'main', 'ttl', 'body')

            content = open(os.path.join(d, 'file.txt')).read()
            self.assertIn('FIX', content)
            self.assertIn('HEADER-B', content)


class ConsultAdvisorTests(TestCase):
    """The advisor tool is a direct Anthropic API call (not the Agent SDK) so
    the advisor model gets no tool/filesystem/DB access of its own."""

    def test_advisor_tool_is_wired_into_allowed_tools(self):
        from debugger import agent
        self.assertIn(agent.ADVISOR_TOOL, agent.ALLOWED_TOOLS)

    @mock.patch('debugger.agent.anthropic')
    def test_call_advisor_returns_text(self, mock_anthropic):
        from debugger import agent

        block = mock.Mock(type='text', text='Root cause: coupon applied after total.')
        response = mock.Mock(stop_reason='end_turn', content=[block])
        mock_anthropic.Anthropic.return_value.messages.create.return_value = response

        with override_settings(ANTHROPIC_API_KEY='sk-test'):
            result = agent._call_advisor('evidence summary')
        self.assertEqual(result, 'Root cause: coupon applied after total.')

    @mock.patch('debugger.agent.anthropic')
    def test_call_advisor_raises_on_refusal(self, mock_anthropic):
        from debugger import agent

        response = mock.Mock(stop_reason='refusal', content=[])
        mock_anthropic.Anthropic.return_value.messages.create.return_value = response

        with override_settings(ANTHROPIC_API_KEY='sk-test'):
            with self.assertRaises(RuntimeError):
                agent._call_advisor('evidence summary')

    def test_call_advisor_raises_without_api_key(self):
        from debugger import agent
        with override_settings(ANTHROPIC_API_KEY=''):
            with self.assertRaises(RuntimeError):
                agent._call_advisor('evidence summary')


class ProcessRequestErrorHandlingTests(TestCase):
    """The soft-timeout and max-turns failures both surface as opaque SDK/
    Celery exceptions — verify each gets a clear, distinct system message."""

    def _bug(self):
        return DebugRequest.objects.create(kind='bug', title='x', body='y')

    @mock.patch('debugger.agent.run_agent')
    def test_soft_time_limit_gets_clear_message(self, run_agent):
        from celery.exceptions import SoftTimeLimitExceeded

        from debugger import tasks
        run_agent.side_effect = SoftTimeLimitExceeded()
        r = self._bug()
        tasks.process_request(r.id)
        r.refresh_from_db()
        self.assertEqual(r.status, DebugRequest.Status.FAILED)
        msg = r.messages.filter(role='system').latest('created_at').content
        self.assertIn('timed out', msg)
        self.assertNotIn('SoftTimeLimitExceeded', msg)

    @mock.patch('debugger.agent.run_agent')
    def test_max_turns_gets_clear_message(self, run_agent):
        from debugger import tasks
        run_agent.side_effect = Exception(
            'Claude Code returned an error result: Reached maximum number of turns (40)')
        r = self._bug()
        tasks.process_request(r.id)
        r.refresh_from_db()
        self.assertEqual(r.status, DebugRequest.Status.FAILED)
        msg = r.messages.filter(role='system').latest('created_at').content
        self.assertIn('steps', msg)
        self.assertNotIn('Claude Code returned an error result', msg)

    @mock.patch('debugger.agent.run_agent')
    def test_other_exceptions_keep_generic_message(self, run_agent):
        from debugger import tasks
        run_agent.side_effect = RuntimeError('boom')
        r = self._bug()
        tasks.process_request(r.id)
        r.refresh_from_db()
        msg = r.messages.filter(role='system').latest('created_at').content
        self.assertIn('Analysis failed: boom', msg)


class RequeueOrphanedTests(TestCase):
    """Worker-restart recovery: transient statuses get re-dispatched."""

    def _backdate(self, req, seconds=600):
        from datetime import timedelta
        from django.utils import timezone
        DebugRequest.objects.filter(id=req.id).update(
            updated_at=timezone.now() - timedelta(seconds=seconds))

    def test_requeues_transient_statuses_and_skips_fresh(self):
        from debugger import tasks
        S = DebugRequest.Status

        analyzing = DebugRequest.objects.create(kind='bug', title='a', status=S.ANALYZING)
        review = DebugRequest.objects.create(
            kind='bug', title='r', status=S.ANALYZING,
            pr_url='https://github.com/o/r/pull/9')
        revising = DebugRequest.objects.create(
            kind='bug', title='v', status=S.REVISING, pr_branch='debugger/bug-3-x')
        finalizing = DebugRequest.objects.create(kind='bug', title='f', status=S.FINALIZING)
        fresh = DebugRequest.objects.create(kind='bug', title='n', status=S.ANALYZING)

        for r in (analyzing, review, revising, finalizing):
            self._backdate(r)
        # `fresh` keeps its recent updated_at → must be skipped.

        with mock.patch.object(tasks.process_request, 'delay') as proc, \
             mock.patch.object(tasks.revise_pr, 'delay') as rev, \
             mock.patch.object(tasks.finalize_learning, 'delay') as fin:
            n = tasks.requeue_orphaned_requests()

        self.assertEqual(n, 4)
        proc.assert_any_call(analyzing.id, mode=None)
        proc.assert_any_call(review.id, mode='review')
        self.assertEqual(proc.call_count, 2)  # `fresh` was not re-dispatched
        rev.assert_called_once_with(revising.id)
        fin.assert_called_once_with(finalizing.id)


class RetryApiTests(TestCase):
    def setUp(self):
        self.superuser = AdminUser.objects.create_user(
            'boss', 'pw', role=AdminUser.Role.SUPER_ADMIN)
        self.api = reverse('debugger:api')
        self.client.force_login(self.superuser)

    @mock.patch('debugger.tasks.process_request.delay')
    def test_retry_requeues_failed_request(self, delay):
        r = DebugRequest.objects.create(
            kind='bug', title='x', status=DebugRequest.Status.FAILED,
            error='Timed out after 900s', created_by=self.superuser)
        res = self.client.post(
            self.api, {'action': 'retry', 'id': r.id}, content_type='application/json')
        self.assertTrue(res.json()['success'])
        r.refresh_from_db()
        self.assertEqual(r.status, DebugRequest.Status.NEW)
        self.assertEqual(r.error, '')
        self.assertTrue(r.messages.filter(role='system', content__icontains='retry').exists())
        delay.assert_called_once_with(r.id, mode=None)

    @mock.patch('debugger.tasks.process_request.delay')
    def test_retry_review_mode_when_pr_open(self, delay):
        r = DebugRequest.objects.create(
            kind='bug', title='x', status=DebugRequest.Status.FAILED,
            pr_url='https://github.com/a/b/pull/1', created_by=self.superuser)
        res = self.client.post(
            self.api, {'action': 'retry', 'id': r.id}, content_type='application/json')
        self.assertTrue(res.json()['success'])
        delay.assert_called_once_with(r.id, mode='review')

    @mock.patch('debugger.tasks.process_request.delay')
    def test_retry_rejects_non_failed_request(self, delay):
        r = DebugRequest.objects.create(
            kind='bug', title='x', status=DebugRequest.Status.READY,
            created_by=self.superuser)
        res = self.client.post(
            self.api, {'action': 'retry', 'id': r.id}, content_type='application/json')
        self.assertFalse(res.json()['success'])
        delay.assert_not_called()

    def test_retry_rejects_unknown_request(self):
        res = self.client.post(
            self.api, {'action': 'retry', 'id': 999999}, content_type='application/json')
        self.assertFalse(res.json()['success'])


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
    def test_regenerate_reruns_analysis(self, delay):
        r = self._ready_bug()
        res = self.client.post(
            self.api, {'action': 'regenerate', 'id': r.id}, content_type='application/json')
        self.assertTrue(res.json()['success'])
        r.refresh_from_db()
        self.assertEqual(r.status, DebugRequest.Status.NEW)
        self.assertTrue(r.messages.filter(role='admin', content__icontains='regenerate').exists())
        delay.assert_called_once_with(r.id, mode=None)

    @mock.patch('debugger.tasks.process_request.delay')
    def test_regenerate_rejects_without_diff(self, delay):
        r = DebugRequest.objects.create(
            kind='bug', title='x', status=DebugRequest.Status.READY, created_by=self.superuser)
        res = self.client.post(
            self.api, {'action': 'regenerate', 'id': r.id}, content_type='application/json')
        self.assertFalse(res.json()['success'])
        delay.assert_not_called()

    @mock.patch('debugger.tasks.process_request.delay')
    def test_regenerate_rejects_when_pr_open(self, delay):
        r = self._ready_bug()
        r.pr_url = 'https://github.com/a/b/pull/1'
        r.save()
        res = self.client.post(
            self.api, {'action': 'regenerate', 'id': r.id}, content_type='application/json')
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
