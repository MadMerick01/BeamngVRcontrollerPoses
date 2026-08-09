"""Source-contract checks for the public controller-pose integration boundary."""
from pathlib import Path


ROOT = Path(__file__).parents[1]
PROVIDER = (ROOT / "mod/lua/ge/extensions/beamngVRControllerPoses.lua").read_text()


def accessor_source() -> str:
    start = PROVIDER.index("function M.getControllerWorldPose(hand)")
    end = PROVIDER.index("\nend", start)
    return PROVIDER[start:end]


def test_right_controller_pose_accessor_and_valid_shape_are_public():
    accessor = accessor_source()
    assert "hand~='left' and hand~='right'" in accessor
    assert "state[hand..'ControllerWorld']" in accessor
    assert "valid=true" in accessor
    assert "ageMs=source.ageMs" in accessor
    assert "updateCounter=source.updateCounter" in accessor
    for component in ("x=source.position[1]", "y=source.position[2]", "z=source.position[3]"):
        assert component in accessor
    for component in (
        "x=source.orientation[1]",
        "y=source.orientation[2]",
        "z=source.orientation[3]",
        "w=source.orientation[4]",
    ):
        assert component in accessor


def test_invalid_pose_cannot_publish_stale_transform_data():
    invalid_branch = accessor_source().split("if not source or not source.valid then", 1)[1]
    invalid_branch = invalid_branch.split("end", 1)[0]
    assert "valid=false" in invalid_branch
    assert "position=nil" in invalid_branch
    assert "orientation=nil" in invalid_branch
