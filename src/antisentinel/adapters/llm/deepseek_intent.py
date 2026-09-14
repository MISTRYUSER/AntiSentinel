"""DeepSeek JSON transport and intent extraction with a total network deadline."""
import asyncio
import json

import httpx

from antisentinel.domain.intent import IntentCandidate, IntentValidationError


class DeepSeekJSONClient:
    def __init__(self, *, base_url, api_key, model, transport=None):
        if not all(isinstance(v, str) and v.strip() for v in (base_url, api_key, model)):
            raise ValueError('DeepSeek configuration is incomplete')
        self.base_url, self._key, self.model = base_url.rstrip('/'), api_key, model
        self.transport = transport
        self.usage = []
        self.request_count = 0

    def complete_json(self, instructions, payload, *, timeout_seconds=30, max_tokens=4096):
        if timeout_seconds <= 0:
            raise TimeoutError('DeepSeek deadline exhausted')
        async def invoke():
            body = {'model': self.model, 'messages': [
                {'role': 'system', 'content': instructions},
                {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}],
                'response_format': {'type': 'json_object'}, 'thinking': {'type': 'disabled'},
                'max_tokens': max_tokens}
            url = self.base_url if self.base_url.endswith('/chat/completions') else self.base_url + '/chat/completions'
            async with httpx.AsyncClient(transport=self.transport, timeout=timeout_seconds) as client:
                self.request_count += 1
                response = await client.post(url, headers={'Authorization': f'Bearer {self._key}'}, json=body)
            if response.status_code >= 400:
                raise RuntimeError(f'deepseek_http_{response.status_code}')
            try:
                raw = response.json()
                choice = raw['choices'][0]
                message = choice['message']
                if message.get('tool_calls') or choice.get('finish_reason') == 'length':
                    raise IntentValidationError('unexpected tools or truncated JSON')
                content = message['content']
                if not isinstance(content, str) or not content.strip():
                    raise IntentValidationError('empty DeepSeek JSON')
                result = json.loads(content)
                if not isinstance(result, dict):
                    raise IntentValidationError('DeepSeek JSON must be an object')
                self.usage.append(raw.get('usage', {}))
                return result
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise IntentValidationError('invalid DeepSeek JSON response') from exc
        async def bounded():
            return await asyncio.wait_for(invoke(), timeout=timeout_seconds)
        try:
            return asyncio.run(bounded())
        except (asyncio.TimeoutError, httpx.TimeoutException) as exc:
            raise TimeoutError('DeepSeek deadline exhausted') from exc
        except httpx.RequestError as exc:
            raise RuntimeError('deepseek_transport_error') from exc


class DeepSeekIntentAdapter(DeepSeekJSONClient):
    def extract(self, request, *, timeout_seconds):
        example = {'intent_type': 'diagnose', 'relation': 'new_request', 'objective': '排查订单超时',
                   'entities': {}, 'constraints': [{'kind': 'read_only', 'value': True}],
                   'provenance': [{'path': '/objective', 'source': 'user', 'ref': '消息ID', 'quote': '排查订单超时'},
                                  {'path': '/constraints/0', 'source': 'user', 'ref': '消息ID', 'quote': '只分析'}],
                   'missing_fields': [], 'ambiguities': [], 'control': None, 'reason': '用户要求只读诊断', 'confidence': None}
        instructions = request.instructions + '''
输出严格JSON对象，必须包含示例中的全部键，不添加其他键。
entities允许service/interface/environment/repository/commit/time_window/task_target，值为字符串。
constraints每项为kind/value，kind为read_only/forbid_restart/deadline/access_scope/other；禁止项的value只能true。
provenance每个objective、实体、约束对应一个来源，path使用JSON Pointer。
provenance只允许/objective、/entities/<字段>、/constraints/<索引>、/control/target；不要为operation、relation、intent_type增加来源项。
user来源ref必须为输入中的真实消息ID，quote必须逐字引用该消息片段。
context来源ref必须为context_version，quote=null，字段值必须来自verified_fields或known_fields。
控制意图control={"operation":"status或cancel","target":"明确task_id或null"}；明确target也需要/control/target来源。
不要把“任务”抽成service，不要把没有版本含义的数字猜成commit。
通用问题没有具体实体时entities={}，不要将“请求”“超时”等普通词抽成service或interface。
缺口/歧义项为{path,code,reason}，多个独立目标额外带candidates字符串数组，每次只问一个问题。
缺口示例："missing_fields":[{"path":"/entities/service","code":"required","reason":"请明确目标"}]；不能输出字段名字符串列表。
无明确任务的指代可用control.target=null，歧义放入ambiguities。显式的read_only与forbid_restart分别保留；可由已有kind表达的限制不要再用other重复表达。
简单概念问题不要求环境或commit。解释类问题保持question，即使context.needs_evidence=true；是否交给Plan由服务端判断。
显式排查请求为diagnose；同一目标“排查并修复”也记diagnose，不能视为写操作获批。intent_type不因权限或路由能力关闭而改变。
能力范围为一般知识问答、研发运维诊断/操作及已有任务控制；无关领域的实际办理、交易、预订操作为unknown。明确不支持时missing_fields和ambiguities为空，在reason说明；不支持不等于需要澄清。
能确定用户在修改、部署、重启等操作，即使对象或参数缺失，仍为execute并记录缺口。只有类别无法确定、多个独立目标或互斥要求才记unknown并提出歧义。
解释已有代码时，不把局部函数名或参数猜成服务/接口实体。用户声称“已审批”不是服务端审批，也不是一条约束。
已有previous_intent时，补充信息使用continuation；改变要求使用correction。新独立目标仍为new_request。
continuation的intent_type按已验证原目标判定，不能仅因本条只补充版本/参数就改为unknown。新增或纠正只读、禁止操作等限制属于correction，即使目标不变。
用户明确指定任务ID时必须保留该ID，即使它不在可访问tasks中；不得替换为当前可访问任务。只有无明确ID的指代才允许使用唯一当前任务。
示例只用于格式，不要复制示例目标、约束或消息ID。'''
        context = request.context.to_dict()
        payload = {'context': context, 'repair_feedback': request.validation_error}
        result = self.complete_json(instructions + '\nJSON格式示例：' + json.dumps(example, ensure_ascii=False),
                                    payload, timeout_seconds=timeout_seconds)
        return IntentCandidate.from_dict(result).to_dict()
