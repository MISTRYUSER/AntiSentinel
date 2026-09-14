"""Generate diagnostic plan drafts against the real tool catalog; never execute them."""
from datetime import datetime, timezone
from uuid import uuid4

from antisentinel.domain.intent import canonical, content_hash, object_fields, require, text


class PlanningService:
    def __init__(self, model, registry):
        self.model, self.registry = model, registry

    def generate(self, request):
        catalog = self.registry.manifests()
        execution = request.get('intent_type') == 'execute'
        result = self.model.complete_json(
            '生成诊断计划JSON，格式为{"hypotheses":["可证伪假设"],"checks":[{"purpose":"检查目的","tool_name":"目录中精确名称","arguments":{}}]}。'
            '如果intent_type是execute，生成执行变更草案，hypotheses可以为空，不要将用户目标改成排障。'
            '只使用提供的工具目录，不执行任何工具。保留只读/禁止重启限制。缺少可用工具时checks返回空数组，不编造工具。最多8条检查。',
            {'intent': request, 'tool_catalog': catalog}, timeout_seconds=30)
        object_fields(result, {'hypotheses', 'checks'}, 'diagnostic plan response')
        require(isinstance(result['hypotheses'], list) and (0 if execution else 1) <= len(result['hypotheses']) <= 8
                and all(text(x) for x in result['hypotheses']), 'invalid hypotheses')
        require(isinstance(result['checks'], list) and len(result['checks']) <= 8, 'invalid checks')
        read_only = any(c['kind'] in {'read_only', 'forbid_restart'} for c in request['constraints'])
        steps = []
        for index, check in enumerate(result['checks']):
            object_fields(check, {'purpose', 'tool_name', 'arguments'}, 'plan check')
            require(text(check['purpose']) and text(check['tool_name']), 'invalid check description')
            tool = self.registry.resolve(check['tool_name'])
            require(tool is not None, 'unknown planned tool')
            require(not self.registry.validate_arguments(tool, check['arguments']), 'invalid planned arguments')
            require(not read_only or tool.read_only, 'plan violates explicit restriction')
            steps.append({**check, 'step_id': f'step-{index+1}', 'requires_approval': tool.requires_approval or not tool.read_only,
                          'read_only': tool.read_only})
        plan = {'plan_id': f'plan-{uuid4()}', 'revision': 1, 'schema_version': '1',
                'plan_type': 'execution' if execution else 'diagnostic',
                'intent_ref': request['intent_ref'], 'objective': request['objective'],
                'scope': request['entities'], 'constraints': request['constraints'], 'provenance': request['provenance'],
                'hypotheses': result['hypotheses'], 'steps': steps, 'tool_catalog_hash': content_hash({'tools': catalog}),
                'status': 'draft' if steps else 'needs_capability', 'execution_status': 'not_submitted',
                'created_at': datetime.now(timezone.utc).isoformat()}
        plan['content_hash'] = content_hash(plan)
        return plan
