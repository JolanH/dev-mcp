"""Error taxonomy: stable codes + envelope-ready ``as_dict()`` shape."""

import pytest

from dev_helper_mcp import errors

# (subclass, expected stable code) for the complete taxonomy.
TAXONOMY = [
    (errors.BranchExists, "BranchExists"),
    (errors.WorktreePathInUse, "WorktreePathInUse"),
    (errors.BaseRefNotFound, "BaseRefNotFound"),
    (errors.DirtyWorktree, "DirtyWorktree"),
    (errors.UnmergedBranch, "UnmergedBranch"),
    (errors.TaskNotFound, "TaskNotFound"),
    (errors.ActiveTaskConflict, "ActiveTaskConflict"),
    (errors.LockedWorktree, "LockedWorktree"),
    (errors.InvalidTaskName, "InvalidTaskName"),
    (errors.InvalidStatus, "InvalidStatus"),
    (errors.GitTimeout, "GitTimeout"),
    (errors.InstanceConflict, "InstanceConflict"),
    (errors.NotAGitRepo, "NotAGitRepo"),
    (errors.RollbackIncomplete, "RollbackIncomplete"),
    (errors.PortUnavailable, "PortUnavailable"),
    (errors.Internal, "Internal"),
]


@pytest.mark.parametrize("cls,code", TAXONOMY)
def test_each_subclass_has_its_stable_code(cls, code):
    err = cls("boom")
    assert err.code == code
    assert isinstance(err, errors.DevHelperError)


def test_as_dict_shape():
    err = errors.BranchExists("branch exists", {"branch": "agent/x"})
    assert err.as_dict() == {
        "code": "BranchExists",
        "message": "branch exists",
        "details": {"branch": "agent/x"},
    }


def test_details_default_to_empty_dict():
    err = errors.GitTimeout("slow")
    assert err.as_dict()["details"] == {}


def test_base_default_code_is_internal():
    assert errors.DevHelperError("x").code == "Internal"
