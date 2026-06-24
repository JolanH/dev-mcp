"""Pure parser for ``git worktree list --porcelain`` output (Story 1.5, AC 1).

A static fixture corpus of sample porcelain byte blobs — no git spawn, no tmp
repo, so there is NO git-safety surface here (these are pure-parser unit tests).
Covers both delimiter forms the parser must accept:

* newline-delimited ``--porcelain`` (git >= 2.7 — what the runner actually invokes
  on this machine's git 2.34, where ``worktree list -z`` is unsupported), and
* NUL-delimited ``--porcelain -z`` (git >= 2.36) for forward-robustness.

Plus the detached-HEAD, locked, prunable, bare and unicode-path cases.
"""

from dev_helper_mcp.git.porcelain import WorktreeEntry, parse_worktree_porcelain

# ── newline-delimited (default --porcelain) fixtures ──

_NL_BASIC = (
    b"worktree /code/repo\n"
    b"HEAD 7d5781ce1bec5e274d3bf6ba6a1d1ec6c38101fb\n"
    b"branch refs/heads/main\n"
    b"\n"
    b"worktree /code/repo.worktrees/feat\n"
    b"HEAD 7d5781ce1bec5e274d3bf6ba6a1d1ec6c38101fb\n"
    b"branch refs/heads/agent/feat\n"
    b"\n"
)

_NL_DETACHED = (
    b"worktree /code/repo\n"
    b"HEAD abc1230000000000000000000000000000000000\n"
    b"branch refs/heads/main\n"
    b"\n"
    b"worktree /code/repo.worktrees/detached\n"
    b"HEAD def4560000000000000000000000000000000000\n"
    b"detached\n"
    b"\n"
)

_NL_LOCKED_PRUNABLE = (
    b"worktree /code/repo.worktrees/locked\n"
    b"HEAD aaa0000000000000000000000000000000000000\n"
    b"branch refs/heads/agent/locked\n"
    b"locked tool is using it\n"
    b"\n"
    b"worktree /code/repo.worktrees/prune\n"
    b"HEAD bbb0000000000000000000000000000000000000\n"
    b"branch refs/heads/agent/prune\n"
    b"prunable gitdir file points to non-existent location\n"
    b"\n"
)

_NL_BARE = b"worktree /code/bare\nbare\n\n"


def test_parses_basic_main_and_agent_worktree():
    entries = parse_worktree_porcelain(_NL_BASIC)
    assert len(entries) == 2
    main, feat = entries
    assert main == WorktreeEntry(
        path="/code/repo",
        branch="main",
        head="7d5781ce1bec5e274d3bf6ba6a1d1ec6c38101fb",
        detached=False,
        locked=False,
        prunable=False,
        bare=False,
    )
    assert feat.path == "/code/repo.worktrees/feat"
    # refs/heads/ prefix normalized away (the store stores the agent/<slug> form).
    assert feat.branch == "agent/feat"
    assert feat.detached is False


def test_detached_head_has_no_branch():
    entries = parse_worktree_porcelain(_NL_DETACHED)
    detached = entries[1]
    assert detached.path == "/code/repo.worktrees/detached"
    assert detached.branch is None
    assert detached.detached is True
    assert detached.head == "def4560000000000000000000000000000000000"


def test_locked_and_prunable_flags():
    locked, prune = parse_worktree_porcelain(_NL_LOCKED_PRUNABLE)
    assert locked.branch == "agent/locked"
    assert locked.locked is True
    assert locked.prunable is False
    assert prune.branch == "agent/prune"
    assert prune.prunable is True
    assert prune.locked is False


def test_bare_entry_flagged():
    (bare,) = parse_worktree_porcelain(_NL_BARE)
    assert bare.path == "/code/bare"
    assert bare.bare is True
    assert bare.branch is None
    assert bare.head is None


def test_unicode_path_round_trips():
    blob = (
        "worktree /code/répo.worktrees/feat-é\n"
        "HEAD ccc0000000000000000000000000000000000000\n"
        "branch refs/heads/agent/feat-é\n"
        "\n"
    ).encode("utf-8")
    (entry,) = parse_worktree_porcelain(blob)
    assert entry.path == "/code/répo.worktrees/feat-é"
    assert entry.branch == "agent/feat-é"


def test_path_with_space_preserved():
    blob = (
        b"worktree /code/my repo.worktrees/feat\n"
        b"HEAD ddd0000000000000000000000000000000000000\n"
        b"branch refs/heads/agent/feat\n"
        b"\n"
    )
    (entry,) = parse_worktree_porcelain(blob)
    assert entry.path == "/code/my repo.worktrees/feat"


def test_empty_input_yields_no_entries():
    assert parse_worktree_porcelain(b"") == []
    assert parse_worktree_porcelain(b"\n") == []


def test_no_trailing_blank_line_still_parses_last_record():
    # git always emits a trailing blank line, but be defensive: the final record
    # must still flush even without it.
    blob = b"worktree /code/repo\nHEAD eee0000000000000000000000000000000000000\nbranch refs/heads/main"
    (entry,) = parse_worktree_porcelain(blob)
    assert entry.path == "/code/repo"
    assert entry.branch == "main"


# ── NUL-delimited (-z) robustness: same logical records, NUL terminators ──


def test_parses_nul_delimited_z_form():
    blob = (
        b"worktree /code/repo\x00"
        b"HEAD fff0000000000000000000000000000000000000\x00"
        b"branch refs/heads/main\x00"
        b"\x00"
        b"worktree /code/repo.worktrees/feat\x00"
        b"HEAD fff0000000000000000000000000000000000000\x00"
        b"branch refs/heads/agent/feat\x00"
        b"detached\x00"  # contrived: exercise a flag line in -z form
        b"\x00"
    )
    main, feat = parse_worktree_porcelain(blob)
    assert main.path == "/code/repo"
    assert main.branch == "main"
    assert feat.path == "/code/repo.worktrees/feat"
    assert feat.detached is True
