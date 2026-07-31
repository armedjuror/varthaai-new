"""
Git / GitHub PR engine for the Debugger Agent (Phase 2).

All git + `gh` work happens in an ISOLATED clone at settings.DEBUGGER_REPO_DIR —
never the running prod checkout. The agent itself never runs any of this; these
functions are called from Celery tasks after the admin approves.

Safety invariants:
  * Never push to the default branch. `_assert_pushable()` refuses it.
  * Never force-push. Revisions are plain commits stacked on the same branch.
  * `gh` / `git push` auth uses GITHUB_TOKEN, injected via env + an
    x-access-token remote; the token is never written to disk.
"""
import json
import os
import re
import subprocess
import tempfile

from django.conf import settings


class PRError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Low-level helpers                                                            #
# --------------------------------------------------------------------------- #
def _env():
    env = dict(os.environ)
    if settings.GITHUB_TOKEN:
        env['GH_TOKEN'] = settings.GITHUB_TOKEN
        env['GITHUB_TOKEN'] = settings.GITHUB_TOKEN
    env['GIT_TERMINAL_PROMPT'] = '0'          # never hang on a credential prompt
    env.setdefault('GIT_AUTHOR_NAME', 'Varthaai Debugger')
    env.setdefault('GIT_AUTHOR_EMAIL', 'debugger@varthaai.local')
    env.setdefault('GIT_COMMITTER_NAME', 'Varthaai Debugger')
    env.setdefault('GIT_COMMITTER_EMAIL', 'debugger@varthaai.local')
    return env


def _run(args, cwd=None, check=True, input_text=None):
    proc = subprocess.run(
        args, cwd=cwd, env=_env(), capture_output=True, text=True,
        input=input_text, timeout=120,
    )
    if check and proc.returncode != 0:
        raise PRError(
            f'command failed ({proc.returncode}): {" ".join(args)}\n'
            f'{proc.stdout}\n{proc.stderr}'.strip()
        )
    return proc


def _repo_dir():
    return settings.DEBUGGER_REPO_DIR


def _default_branch():
    return settings.GITHUB_DEFAULT_BRANCH


def _authed_remote():
    slug = settings.GITHUB_REPO
    token = settings.GITHUB_TOKEN
    if token:
        return f'https://x-access-token:{token}@github.com/{slug}.git'
    return f'https://github.com/{slug}.git'


def _assert_pushable(branch):
    """Refuse to ever push the default branch."""
    if not branch or branch.strip() == _default_branch():
        raise PRError(f'refusing to push protected branch "{branch}".')


def slugify(text, maxlen=40):
    s = re.sub(r'[^a-z0-9]+', '-', (text or '').lower()).strip('-')
    return (s[:maxlen].strip('-')) or 'change'


def branch_name(request):
    return f'debugger/{request.kind}-{request.id}-{slugify(request.title)}'


def pr_number_from_url(url):
    m = re.search(r'/pull/(\d+)', url or '')
    return int(m.group(1)) if m else None


# --------------------------------------------------------------------------- #
# Repo setup                                                                   #
# --------------------------------------------------------------------------- #
def ensure_clone():
    """Make sure the isolated clone exists and origin is fetched."""
    repo = _repo_dir()
    if not settings.GITHUB_TOKEN:
        raise PRError('GITHUB_TOKEN is not configured.')
    git_dir = os.path.join(repo, '.git')
    if not os.path.isdir(git_dir):
        os.makedirs(os.path.dirname(repo) or '.', exist_ok=True)
        _run(['git', 'clone', _authed_remote(), repo])
    else:
        # Keep the auth remote current (token may have rotated).
        _run(['git', 'remote', 'set-url', 'origin', _authed_remote()], cwd=repo)
        _run(['git', 'fetch', 'origin', '--prune'], cwd=repo)
    return repo


def _prod_head_sha():
    """The exact commit the agent read when it generated the diff (prod checkout).

    Branching the isolated clone from THIS commit — rather than origin/<default>
    — makes the diff apply cleanly (matching context) and gives `git apply
    --3way` the blobs it needs, avoiding the "lacks the necessary blob" failure.
    """
    try:
        out = subprocess.run(
            ['git', '-C', settings.DEBUGGER_CODE_DIR, 'rev-parse', 'HEAD'],
            capture_output=True, text=True, timeout=15)
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return None


def _base_ref(repo, default_branch):
    """Prefer the prod-HEAD commit (if present on origin); else origin/<default>."""
    sha = _prod_head_sha()
    if sha:
        exists = _run(['git', 'cat-file', '-e', f'{sha}^{{commit}}'], cwd=repo, check=False)
        if exists.returncode != 0:
            _run(['git', 'fetch', 'origin', sha], cwd=repo, check=False)
            exists = _run(['git', 'cat-file', '-e', f'{sha}^{{commit}}'], cwd=repo, check=False)
        if exists.returncode == 0:
            return sha
    return f'origin/{default_branch}'


def _apply_diff(repo, diff):
    """
    Apply a unified diff to the working tree, trying progressively more lenient
    strategies and resetting to a clean base between attempts (git apply is
    atomic, but --3way/patch can leave partial state). The caller stages the
    result with `git add -A`. Raises PRError if nothing applies.
    """
    if not diff or not diff.strip():
        raise PRError('empty diff — nothing to apply.')
    text = diff if diff.endswith('\n') else diff + '\n'
    with tempfile.NamedTemporaryFile('w', suffix='.diff', delete=False) as fh:
        fh.write(text)
        path = fh.name

    head = _run(['git', 'rev-parse', 'HEAD'], cwd=repo, check=False).stdout.strip()

    def reset():
        if head:
            _run(['git', 'reset', '--hard', '-q', head], cwd=repo, check=False)
        _run(['git', 'clean', '-fdq'], cwd=repo, check=False)

    strategies = [
        ['git', 'apply', path],
        ['git', 'apply', '--3way', path],
        ['git', 'apply', '--recount', '--whitespace=fix', path],
        ['git', 'apply', '--ignore-whitespace', path],
        ['git', 'apply', '-C1', '--ignore-whitespace', path],
        ['git', 'apply', '-p0', '--ignore-whitespace', path],
        ['patch', '-p1', '--fuzz=3', '--no-backup-if-mismatch', '-i', path],
        ['patch', '-p0', '--fuzz=3', '--no-backup-if-mismatch', '-i', path],
    ]
    errors = []
    try:
        for cmd in strategies:
            reset()
            res = _run(cmd, cwd=repo, check=False)
            if res.returncode == 0:
                return
            msg = (res.stderr or res.stdout).strip()
            if msg:
                errors.append(msg.splitlines()[0])
        reset()
    finally:
        os.unlink(path)

    uniq = []
    for e in errors:
        if e and e not in uniq:
            uniq.append(e)
    raise PRError('could not apply the proposed diff (context drift). '
                  + ' | '.join(uniq)[:900])


# --------------------------------------------------------------------------- #
# Public operations                                                           #
# --------------------------------------------------------------------------- #
def create_pull_request(request):
    """
    Branch from origin/<default>, apply request.proposed_diff, commit, push,
    open a PR. Returns {'pr_url', 'branch', 'pr_number'}. Leaves the branch
    checked out in the worktree so a later revision run can read it.
    """
    repo = ensure_clone()
    branch = branch_name(request)
    _assert_pushable(branch)
    base = _default_branch()

    # Branch from the exact commit the agent read so the diff applies cleanly.
    _run(['git', 'checkout', '-B', branch, _base_ref(repo, base)], cwd=repo)
    _apply_diff(repo, request.proposed_diff)
    _run(['git', 'add', '-A'], cwd=repo)

    title = (request.pr_title or request.title)[:200]
    body = request.pr_body or _default_body(request)
    _run(['git', 'commit', '-m', title, '-m', body], cwd=repo)

    _assert_pushable(branch)
    _run(['git', 'push', '--set-upstream', 'origin', branch], cwd=repo)

    out = _run([
        'gh', 'pr', 'create', '-R', settings.GITHUB_REPO,
        '--base', base, '--head', branch, '--title', title, '--body', body,
    ], cwd=repo)
    url = (out.stdout or '').strip().splitlines()[-1].strip()
    return {'pr_url': url, 'branch': branch, 'pr_number': pr_number_from_url(url)}


def checkout_branch(request):
    """Ensure the clone has request.pr_branch checked out and up to date."""
    repo = ensure_clone()
    branch = request.pr_branch or branch_name(request)
    _run(['git', 'fetch', 'origin', branch], cwd=repo, check=False)
    _run(['git', 'checkout', '-B', branch, f'origin/{branch}'], cwd=repo, check=False)
    # Fall back to local branch if remote fetch failed.
    _run(['git', 'checkout', branch], cwd=repo, check=False)
    return repo


def push_revision(request, diff, message='Address review feedback'):
    """
    Apply a delta diff on top of the existing branch and push a NEW commit
    (never a force-push). Returns the branch name.
    """
    repo = checkout_branch(request)
    branch = request.pr_branch or branch_name(request)
    _assert_pushable(branch)
    _apply_diff(repo, diff)
    _run(['git', 'add', '-A'], cwd=repo)
    _run(['git', 'commit', '-m', message], cwd=repo)
    _assert_pushable(branch)
    _run(['git', 'push', 'origin', branch], cwd=repo)
    return branch


def comment_on_pr(pr_number, body):
    _run(['gh', 'pr', 'comment', str(pr_number), '-R', settings.GITHUB_REPO,
          '--body', body], cwd=_repo_dir(), check=False)


def fetch_pr_activity(pr_number, last_review_id=None, last_comment_id=None):
    """
    Return NEW review + comment activity since the given ids, via `gh api`.
    Combines: PR reviews, PR inline review comments, and issue comments.
    """
    repo = ensure_clone()
    reviews = _gh_api(f'repos/{settings.GITHUB_REPO}/pulls/{pr_number}/reviews', repo)
    review_comments = _gh_api(f'repos/{settings.GITHUB_REPO}/pulls/{pr_number}/comments', repo)
    issue_comments = _gh_api(f'repos/{settings.GITHUB_REPO}/issues/{pr_number}/comments', repo)

    items = []
    max_review = last_review_id or 0
    for r in reviews:
        rid = r.get('id', 0)
        if rid > (last_review_id or 0) and (r.get('body') or r.get('state')):
            items.append({
                'kind': 'review', 'id': rid,
                'author': (r.get('user') or {}).get('login', '?'),
                'state': r.get('state', ''),
                'body': r.get('body', ''),
            })
        max_review = max(max_review, rid)

    max_comment = last_comment_id or 0
    for c in review_comments + issue_comments:
        cid = c.get('id', 0)
        if cid > (last_comment_id or 0):
            items.append({
                'kind': 'comment', 'id': cid,
                'author': (c.get('user') or {}).get('login', '?'),
                'path': c.get('path', ''),
                'body': c.get('body', ''),
            })
        max_comment = max(max_comment, cid)

    items.sort(key=lambda x: x['id'])
    return {'items': items, 'max_review_id': max_review, 'max_comment_id': max_comment}


def _gh_api(path, repo):
    out = _run(['gh', 'api', '--paginate', path], cwd=repo, check=False)
    if out.returncode != 0 or not out.stdout.strip():
        return []
    try:
        data = json.loads(out.stdout)
        return data if isinstance(data, list) else [data]
    except json.JSONDecodeError:
        return []


def _default_body(request):
    return (
        f'Automated fix proposed by the Varthaai Debugger Agent for '
        f'{request.kind} #{request.id}: "{request.title}".\n\n'
        f'{request.rca or ""}\n\n'
        f'_Review carefully before merging. Generated with Claude Code._'
    )
