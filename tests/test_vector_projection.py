from antisentinel.memory.vector_projection import project_session, projection_batches


class Turn:
    def __init__(self, turn_id, content): self.turn_id, self.content = turn_id, content
class Session:
    session_id = "session-1"
    turns = tuple(Turn(f"turn-{i}", "x" * 900) for i in range(30))


def test_projection_bounds_long_session_and_preserves_turn_sources():
    blocks = project_session(Session())
    assert len(blocks) == 8
    assert all(len(block.content) <= 2000 for block in blocks)
    assert all(block.session_id == "session-1" and block.turn_ids for block in blocks)
    assert blocks[0].turn_ids[0] == "turn-0"


def test_projection_batches_bound_items_and_total_characters():
    blocks = project_session(Session()) * 4
    batches = list(projection_batches(blocks))
    assert all(len(batch) <= 20 and sum(len(block.content) for block in batch) <= 20000 for batch in batches)
