import json


def test_bfcl_maps_dict_schema_and_preserves_case_id():
    from antisentinel.evaluation.bfcl_adapter import adapt_case
    raw = {"id": "simple_python_0", "question": [[{"role": "user", "content": "area"}]], "function": [{"name": "triangle", "description": "area", "parameters": {"type": "dict", "properties": {"base": {"type": "float"}}, "required": ["base"]}}]}
    case = adapt_case(raw, category="simple_python", source_hash="abc")
    assert case.case_id == "simple_python_0"
    assert case.tools[0]["argument_schema"]["type"] == "object"
    assert case.tools[0]["argument_schema"]["properties"]["base"]["type"] == "number"
    assert case.messages == ({"role": "user", "content": "area"},)


def test_bfcl_preserves_multi_turn_boundaries_and_irrelevance():
    from antisentinel.evaluation.bfcl_adapter import adapt_case
    raw = {"id": "multi_turn_base_0", "question": [[{"role": "user", "content": "one"}], [{"role": "user", "content": "two"}]], "function": []}
    case = adapt_case(raw, category="multi_turn_base", source_hash="abc")
    assert case.turns == (({"role": "user", "content": "one"},), ({"role": "user", "content": "two"},))
    assert case.tools == ()
