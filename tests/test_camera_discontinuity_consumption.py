"""Lifecycle regression oracle for one-shot raw-anchor discontinuities."""


class ReanchorModel:
    def __init__(self, accepted=(0.0, 0.0, 0.0), threshold=5.0):
        self.accepted = accepted
        self.threshold = threshold
        self.pending = False
        self.generation = 0
        self.requested = 0
        self.completed = 0
        self.resets = 0

    def frame(self, anchor, sequence, *, hmd_valid=True, capture_valid=True):
        distance = sum((a - b) ** 2 for a, b in zip(anchor, self.accepted)) ** 0.5
        if not self.pending and distance > self.threshold:
            self.pending = True
            self.generation += 1
            self.requested += 1
        elif not self.pending:
            self.accepted = tuple(anchor)

        if self.pending and hmd_valid and capture_valid:
            # Mirrors the runtime transaction: acceptance follows successful rebuild.
            self.resets += 1
            self.completed += 1
            self.accepted = tuple(anchor)
            self.accepted_sequence = sequence
            self.pending = False


def test_same_context_cut_and_repeated_increasing_setter_sequences_are_consumed_once():
    model = ReanchorModel()
    model.frame((10.0, 0.0, 0.0), 2)
    for sequence in range(3, 103):
        model.frame((10.0, 0.0, 0.0), sequence)
    assert (model.requested, model.completed, model.resets) == (1, 1, 1)
    assert model.accepted == (10.0, 0.0, 0.0)
    assert not model.pending


def test_small_jitter_then_genuine_later_cut_creates_only_second_generation():
    model = ReanchorModel()
    model.frame((10.0, 0.0, 0.0), 2)
    for sequence in range(3, 103):
        model.frame((10.0 + (sequence % 3) * 0.001, 0.0, 0.0), sequence)
    assert (model.requested, model.completed) == (1, 1)
    model.frame((20.0, 0.0, 0.0), 103)
    assert (model.generation, model.requested, model.completed, model.resets) == (2, 2, 2, 2)


def test_invalid_hmd_defers_one_request_and_does_not_accept_until_success():
    model = ReanchorModel()
    for sequence in range(2, 102):
        model.frame((10.0, 0.0, 0.0), sequence, hmd_valid=False)
    assert (model.requested, model.completed, model.resets) == (1, 0, 0)
    assert model.accepted == (0.0, 0.0, 0.0)
    assert model.pending
    model.frame((10.0, 0.0, 0.0), 102)
    assert (model.requested, model.completed, model.resets) == (1, 1, 1)
    assert model.accepted == (10.0, 0.0, 0.0)


def test_stale_or_malformed_capture_never_overwrites_accepted_reference():
    model = ReanchorModel()
    for sequence in range(2, 12):
        model.frame((10.0, 0.0, 0.0), sequence, capture_valid=False)
    assert model.pending and model.requested == 1 and model.completed == 0
    assert model.accepted == (0.0, 0.0, 0.0)


def test_ordinary_subthreshold_motion_advances_observation_without_reattachment():
    model = ReanchorModel()
    for sequence in range(2, 202):
        model.frame((sequence * 0.02, 0.0, 0.0), sequence)
    assert (model.requested, model.completed, model.resets) == (0, 0, 0)
