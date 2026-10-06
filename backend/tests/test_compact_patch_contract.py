"""Live-shape patch schemas and content-free terminal diagnostics; no providers."""
import copy

import pytest
from jsonschema import Draft202012Validator

from app.ai.Cognitive.system.v0.two_call.compact_provider import compact_schema
from app.ai.Cognitive.system.v0.collaborative_content_v0.compiler import patch_rejection_detail
from test_compact_two_call import CompactDouble, run_compact, assert_replay
from simulated_authority_support import DINNER


@pytest.mark.parametrize('mutation,detail', [
    ('missing_dish','set_main_dish.dish.missing'),
    ('invalid_integer','set_servings.value.greater_than_equal'),
    ('invalid_prep_time','set_main_dish.prep_time_minutes.less_than_equal'),
])
def test_patch_field_rejection_reaches_durable_failure_code(mutation,detail):
    def change(stage,raw,payload):
        if stage!='call_two':return
        operations=raw['content_ops'][0]['output']['operations']
        if mutation=='invalid_integer':
            next(op for op in operations if op['op']=='set_servings')['integer_value']=0
        else:
            op=next(op for op in operations if op['op']=='set_main_dish')
            if mutation=='missing_dish':op['dish']=None
            else:op['dish']['prep_time_minutes']=1441
    record,_,model=run_compact(CompactDouble(artifact=True,mutation=change),text=DINNER['turns'][1]['user'])
    assert record.status.value=='failed' and len(model.calls)==2
    assert f'.content_compilation.compiler.patch_rejected.{detail}.' in record.failure.code
    assert record.realization_status.value=='not_requested'


def test_patch_schema_matches_required_payloads_and_numeric_bounds():
    def check(stage,raw,payload):
        if stage!='call_two':return
        schema=compact_schema(stage,payload)
        Draft202012Validator.check_schema(schema)
        operations_schema=schema['properties']['content_ops']['items']['properties']['output']['properties']['operations']['items']
        allowed={op for branch in operations_schema['anyOf'] for op in branch['properties']['op']['enum']}
        assert not any(op.startswith(('update_','remove_')) for op in allowed)
        validator=Draft202012Validator({'$defs':schema['$defs'],**operations_schema})
        operations=raw['content_ops'][0]['output']['operations']
        for op in operations:validator.validate(op)
        dish=copy.deepcopy(next(op for op in operations if op['op']=='set_main_dish'))
        integer=copy.deepcopy(next(op for op in operations if op['op']=='set_servings'))
        malformed=[]
        bad=copy.deepcopy(dish);bad['dish']=None;malformed.append(bad)
        bad=copy.deepcopy(dish);bad['dish']['prep_time_minutes']=1441;malformed.append(bad)
        bad=copy.deepcopy(dish);bad['dish']['name']=' ';malformed.append(bad)
        bad=copy.deepcopy(integer);bad['integer_value']=0;malformed.append(bad)
        bad=copy.deepcopy(integer);bad['integer_class']=None;malformed.append(bad)
        bad=copy.deepcopy(integer);bad['dish']=dish['dish'];malformed.append(bad)
        for op in malformed:assert list(validator.iter_errors(op)),op['op']
    record,_,_=run_compact(CompactDouble(artifact=True,mutation=check),text=DINNER['turns'][1]['user'])
    assert record.status.value=='completed',record.failure
    assert_replay(record)


def test_workspace_patch_rejection_keeps_its_content_free_reason():
    def duplicate(stage,raw,payload):
        if stage!='call_two':return
        operations=raw['content_ops'][0]['output']['operations']
        operations.append(copy.deepcopy(next(op for op in operations if op['op']=='set_main_dish')))
    record,_,_=run_compact(CompactDouble(artifact=True,mutation=duplicate),text=DINNER['turns'][1]['user'])
    assert record.status.value=='failed'
    assert '.content_compilation.compiler.patch_rejected.conflicting_operations.' in record.failure.code


@pytest.mark.parametrize('subject', ['private user text','set_main_dish.secret.missing',
    'private.name.string_too_short','duplicate_element.private.value_error'])
def test_patch_diagnostics_never_propagate_unrecognized_subjects(subject):
    assert patch_rejection_detail(subject)=='invalid_patch'
