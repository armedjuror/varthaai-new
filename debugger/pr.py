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


def current_code_sha(cwd=None):
    """HEAD of the read-only code checkout the agent inspects (or `cwd`).

    Captured when a fix is proposed and stored on the request as `base_sha`, so
    the PR can transplant the fix from the exact commit the diff was built
    against — even after the default branch has moved on. Returns '' on failure.
    """
    d = cwd or settings.DEBUGGER_CODE_DIR
    try:
        out = subprocess.run(['git', '-C', d, 'rev-parse', 'HEAD'],
                             capture_output=True, text=True, timeout=15)
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return ''


def _ensure_commit(repo, sha):
    """True if commit `sha` is present in `repo`, fetching it from origin if not.

    GitHub allows fetching a specific SHA when it is reachable from a ref; the
    stored base_sha was on the default branch, so it is reachable.
    """
    if not sha:
        return False
    have = _run(['git', 'cat-file', '-e', f'{sha}^{{commit}}'], cwd=repo, check=False)
    if have.returncode == 0:
        return True
    _run(['git', 'fetch', 'origin', sha], cwd=repo, check=False)
    have = _run(['git', 'cat-file', '-e', f'{sha}^{{commit}}'], cwd=repo, check=False)
    return have.returncode == 0


_DIFF_GIT_RE = re.compile(r'^diff --git ')
_EXTENDED_HEADER_RE = re.compile(
    r'^(index |new file mode|deleted file mode|old mode|new mode|'
    r'similarity index|rename from|rename to|copy from|copy to)'
)
_MINUS_RE = re.compile(r'^--- (?:a/(.+)|(/dev/null))$')
_PLUS_RE = re.compile(r'^\+\+\+ (?:b/(.+)|(/dev/null))$')


def _has_diff_git_header(out):
    """True if `out` (lines emitted so far) ends in a `diff --git` header,
    possibly followed by extended-header lines (index/mode/rename/...)."""
    j = len(out) - 1
    while j >= 0 and _EXTENDED_HEADER_RE.match(out[j]):
        j -= 1
    return j >= 0 and bool(_DIFF_GIT_RE.match(out[j]))


def _normalize_diff(diff):
    """
    Repair the most common malformation seen in LLM-generated unified diffs: a
    per-file `--- a/X` / `+++ b/Y` pair with no preceding `diff --git a/X b/Y`
    header (and, for new/deleted files, no `new file mode` / `deleted file
    mode` marker). Without a `diff --git` header, `git apply` can't find the
    file-section boundary, and one missing header corrupts parsing of the rest
    of a multi-file patch. Idempotent: a `--- ` line that already has a
    `diff --git` header directly above it (allowing intervening extended
    headers such as `index ...`) is left untouched, so this is a no-op on a
    well-formed diff (e.g. real `git diff` output).
    """
    lines = diff.split('\n')
    out = []
    n = len(lines)
    for i, line in enumerate(lines):
        m = _MINUS_RE.match(line)
        if m and not _has_diff_git_header(out):
            old_path = m.group(1)
            new_path = None
            if i + 1 < n:
                pm = _PLUS_RE.match(lines[i + 1])
                if pm:
                    new_path = pm.group(1)  # None when the target is /dev/null (deleted file)
            a_path = old_path or new_path or 'file'
            b_path = new_path or old_path or 'file'
            out.append(f'diff --git a/{a_path} b/{b_path}')
            if old_path is None:
                out.append('new file mode 100644')
            elif new_path is None:
                out.append('deleted file mode 100644')
        out.append(line)
    return '\n'.join(out)


def _write_new_files(repo, new_files):
    """
    Materialize brand-new files directly onto the working tree — bypassing
    diff hunks entirely. This is the fix for the dominant failure mode in
    LLM-authored diffs: a `@@ -0,0 +1,N @@` hunk for a large new file requires
    hand-counting every added line, and a single wrong N corrupts the whole
    patch. `new_files` is [{'path': str, 'content': str}, ...] (paths are
    repo-relative and untrusted — resolved and confined to `repo`).
    """
    repo_real = os.path.realpath(repo)
    for item in new_files or []:
        rel = (item.get('path') or '').strip().lstrip('/')
        content = item.get('content')
        if not rel or content is None:
            continue
        dest = os.path.realpath(os.path.join(repo_real, rel))
        if dest != repo_real and not dest.startswith(repo_real + os.sep):
            raise PRError(f'refusing to write outside the repo: {item.get("path")!r}')
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, 'w') as fh:
            fh.write(content)


def _apply_fix(repo, diff, new_files=None):
    """Apply the existing-file diff (if any) and write brand-new files (if
    any). Raises PRError if both are empty — nothing to apply."""
    if not (diff and diff.strip()) and not new_files:
        raise PRError('empty diff — nothing to apply.')
    if diff and diff.strip():
        _apply_diff(repo, diff)
    _write_new_files(repo, new_files)


def _apply_diff(repo, diff):
    """
    Apply a unified diff to the working tree, trying progressively more lenient
    strategies and resetting to a clean base between attempts (git apply is
    atomic, but --3way/patch can leave partial state). The caller stages the
    result with `git add -A`. Raises PRError if nothing applies.
    """
    if not diff or not diff.strip():
        raise PRError('empty diff — nothing to apply.')
    diff = _normalize_diff(diff)
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
    Open a PR for request.proposed_diff against the LATEST default branch.

    The diff was generated by the agent reading the code at request.base_sha.
    Between then and now other PRs may have merged and prod may have redeployed,
    so the diff no longer applies to the current tip. We therefore:
      1. base the PR branch on a freshly-fetched origin/<default> (so the PR is
         mergeable and never reverts intervening merges), and
      2. transplant the fix by building it as a commit on base_sha and
         cherry-picking it across — a real 3-way merge that tolerates
         non-overlapping drift.
    When base_sha is unknown/unfetchable we fall back to applying the diff
    directly onto the latest default branch with the lenient strategies in
    _apply_diff. Returns {'pr_url', 'branch', 'pr_number'} and leaves the branch
    checked out so a later revision run can read it.
    """
    repo = ensure_clone()
    branch = branch_name(request)
    _assert_pushable(branch)
    base = _default_branch()
    _run(['git', 'fetch', 'origin', base], cwd=repo)

    title = (request.pr_title or request.title)[:200]
    body = request.pr_body or _default_body(request)

    _build_fix_branch(repo, request, branch, base, title, body)

    _assert_pushable(branch)
    _run(['git', 'push', '--set-upstream', 'origin', branch], cwd=repo)

    out = _run([
        'gh', 'pr', 'create', '-R', settings.GITHUB_REPO,
        '--base', base, '--head', branch, '--title', title, '--body', body,
    ], cwd=repo)
    url = (out.stdout or '').strip().splitlines()[-1].strip()
    return {'pr_url': url, 'branch': branch, 'pr_number': pr_number_from_url(url)}


def _build_fix_branch(repo, request, branch, base, title, body):
    """Leave `branch` checked out, based on origin/<base>, with the fix committed.

    Prefers a clean cherry-pick transplant from base_sha; falls back to a direct
    lenient apply onto the latest default branch when base_sha is unavailable or
    the transplant conflicts.
    """
    base_ref = f'origin/{base}'
    base_sha = (getattr(request, 'base_sha', '') or '').strip()

    if base_sha and _ensure_commit(repo, base_sha):
        try:
            _cherry_pick_fix(repo, request, branch, base_ref, base_sha, title, body)
            return
        except PRError:
            # Genuine overlap or a malformed diff — clean up and try direct apply.
            _run(['git', 'cherry-pick', '--abort'], cwd=repo, check=False)
            _run(['git', 'checkout', '-f', '-B', branch, base_ref], cwd=repo, check=False)
            _run(['git', 'clean', '-fdq'], cwd=repo, check=False)

    _run(['git', 'checkout', '-B', branch, base_ref], cwd=repo)
    _apply_fix(repo, request.proposed_diff, request.proposed_new_files)
    _run(['git', 'add', '-A'], cwd=repo)
    _run(['git', 'commit', '-m', title, '-m', body], cwd=repo)


def _cherry_pick_fix(repo, request, branch, base_ref, base_sha, title, body):
    """
    Build the fix as one commit on top of base_sha (where the diff applies
    cleanly), then cherry-pick it onto a fresh branch off base_ref. Cherry-pick
    is a 3-way merge with base_sha as the merge base, so intervening merges that
    don't touch the same lines are absorbed automatically. Raises PRError on a
    real conflict so the caller can fall back.
    """
    tmp = f'{branch}--base'
    _run(['git', 'checkout', '-B', tmp, base_sha], cwd=repo)
    _apply_fix(repo, request.proposed_diff, request.proposed_new_files)
    _run(['git', 'add', '-A'], cwd=repo)
    _run(['git', 'commit', '-m', title, '-m', body], cwd=repo)
    fix_sha = _run(['git', 'rev-parse', 'HEAD'], cwd=repo).stdout.strip()

    _run(['git', 'checkout', '-B', branch, base_ref], cwd=repo)
    res = _run(['git', 'cherry-pick', fix_sha], cwd=repo, check=False)
    _run(['git', 'branch', '-D', tmp], cwd=repo, check=False)
    if res.returncode != 0:
        detail = (res.stderr or res.stdout).strip().splitlines()
        first = detail[0] if detail else 'cherry-pick failed'
        raise PRError(f'fix conflicts with newer changes on {base_ref}: {first}')


def checkout_branch(request):
    """Ensure the clone has request.pr_branch checked out and up to date."""
    repo = ensure_clone()
    branch = request.pr_branch or branch_name(request)
    _run(['git', 'fetch', 'origin', branch], cwd=repo, check=False)
    _run(['git', 'checkout', '-B', branch, f'origin/{branch}'], cwd=repo, check=False)
    # Fall back to local branch if remote fetch failed.
    _run(['git', 'checkout', branch], cwd=repo, check=False)
    return repo


def push_revision(request, diff, new_files=None, message='Address review feedback'):
    """
    Apply a delta diff (plus any brand-new files) on top of the existing
    branch and push a NEW commit (never a force-push). Returns the branch name.
    """
    repo = checkout_branch(request)
    branch = request.pr_branch or branch_name(request)
    _assert_pushable(branch)
    _apply_fix(repo, diff, new_files)
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
