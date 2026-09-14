"""Deterministic intent resolution. No model, tool handler, storage or dispatch calls."""
from __future__ import annotations
import re

from antisentinel.domain.intent import (
    AuthorizedIntentContext, IntentCandidate, IntentIdentity, ResolvedIntent,
    content_hash, require,
)


def _gap(path: str, code: str, reason: str) -> dict:
    return {'path': path, 'code': code, 'reason': reason}


def resolve_candidate(candidate: IntentCandidate, context: AuthorizedIntentContext,
                      identity: IntentIdentity) -> ResolvedIntent:
    value, ctx, ident = candidate.to_dict(), context.to_dict(), identity.to_dict()
    require(all(ident[k] == ctx[k] for k in ('session_id', 'incident_id', 'message_id')), 'identity/context mismatch')
    validate_sources(value, ctx)
    _normalize_constraints(value)
    return _resolve_verified(value, ctx, ident)


def validate_sources(value: dict, ctx: dict) -> None:
    """Validate source claims before a merge can replace any candidate field."""
    if value['intent_type'] in ('diagnose','execute','task_control'):
        prefix=re.split(r'(?:日志内容|log\s+(?:content|output)|tool\s+output)\s*[:：]|```',
                        ctx['messages'][ctx['message_id']],maxsplit=1,flags=re.IGNORECASE)[0]
        prefix=re.sub(r'『.*?』|「.*?」|“.*?”|"[^"]*"','',prefix,flags=re.DOTALL)
        boundary=r'(?:^|[，,。.;；])\s*'
        patterns={'read_only':r'(?:请)?(?:只读|只分析|仅分析)|read[- ]only\b|only\s+(?:analy[sz]e|read)\b',
                  'forbid_restart':r'(?:请)?(?:不要|禁止|不允许)重启|(?:do not|don\x27t|never)\s+restart\b'}
        kinds={c['kind'] for c in value['constraints'] if c['value'] is True}
        for kind,pattern in patterns.items():
            if re.search(boundary+'(?:'+pattern+')',prefix,re.IGNORECASE):
                require(kind in kinds, f'explicit prohibition {kind} must be retained')
    if value['intent_type']=='task_control':
        message=ctx['messages'][ctx['message_id']]
        pattern=r'(?:(?:任务|计划)(?:\s*ID)?\s*[:#：]?\s*|\b(?:task|plan)(?:\s+id)?(?:\s*[:#]\s*|\s+))([A-Za-z0-9][A-Za-z0-9_.:-]*)'
        reserved={'status','progress','state','is','id','please','now'}
        explicit={m.rstrip('.,;:') for m in re.findall(pattern,message,flags=re.IGNORECASE)
                  if m.lower().rstrip('.,;:') not in reserved}
        targets={target for target in (value['control']['target'],value['entities'].get('task_target')) if target is not None}
        if explicit:
            require(bool(targets) and targets<=explicit, 'explicit task ID must be preserved; do not substitute an accessible task')
            require(len(explicit)==1 or bool(value['ambiguities']), 'multiple explicit task IDs require clarification')
    fields = {'/objective': value['objective']}
    fields.update({f'/entities/{key}': val for key, val in value['entities'].items()})
    fields.update({f'/constraints/{i}': val for i, val in enumerate(value['constraints'])})
    if value['control'] and (value['control']['target'] is not None or
                             any(p['path']=='/control/target' for p in value['provenance'])):
        fields['/control/target'] = value['control']['target']
    sources = {source['path']: source for source in value['provenance']}
    require(len(sources) == len(value['provenance']), 'duplicate provenance')
    require(set(sources) == set(fields), 'every extracted field requires exactly one source')
    for path, field in fields.items():
        source = sources[path]
        if source['source'] == 'user':
            message = ctx['messages'].get(source['ref'])
            require(message is not None and source['quote'] in message, 'unverifiable user source')
        else:
            known = {**ctx['verified_fields'], **ctx.get('known_fields', {})}
            require(source['ref'] == ctx['context_version'] and path in known
                    and known[path] == field, 'unverifiable context source')


def _normalize_constraints(value):
    """Only remove a config-write alias already covered by an explicit read_only flag."""
    kinds={c['kind'] for c in value['constraints']}
    if 'read_only' not in kinds or 'other' not in kinds:return
    pattern=r'(?:(?:不|不要|禁止)(?:修改|更改|改动)配置|(?:do not|don\x27t|never)\s+(?:change|modify|edit)\s+(?:the\s+)?config(?:uration)?)'
    remap={};kept=[]
    for index,constraint in enumerate(value['constraints']):
        if constraint['kind']=='other' and re.fullmatch(pattern,constraint['value'].strip(' 。.;'),re.IGNORECASE):continue
        remap[f'/constraints/{index}']=f'/constraints/{len(kept)}';kept.append(constraint)
    # Context paths are also source addresses. Keep the original representation
    # if removal would move a surviving verified context reference.
    if any(p['source']=='context' and p['path'] in remap and remap[p['path']]!=p['path']
           for p in value['provenance']):return
    sources=[]
    for source in value['provenance']:
        if source['path'].startswith('/constraints/'):
            if source['path'] not in remap:continue
            source={**source,'path':remap[source['path']]}
        sources.append(source)
    value['constraints']=kept;value['provenance']=sources


def _resolve_verified(value, ctx, ident):
    fields = {'/objective': value['objective']}
    fields.update({f'/entities/{key}': val for key, val in value['entities'].items()})
    missing = value['missing_fields']
    ambiguities = value['ambiguities']
    for path in ctx['required_fields']:
        if path not in fields:
            missing.append(_gap(path, 'required', f'请补充 {path}。'))
    for key, target in value['entities'].items():
        if key == 'task_target':
            continue  # Resolved exclusively against current accessible task candidates below.
        path = f'/entities/{key}'
        if ctx['verified_fields'].get(path) != target:
            missing.append(_gap(path, 'binding_required', f'请确认 {key} 的可访问目标绑定。'))
    previous = ctx['previous_intent']
    if value['relation'] != 'new_request':
        if previous is None:
            missing.append(_gap('/context_binding/previous_intent', 'ambiguous_reference', '你要补充或纠正哪个请求？'))
        else:
            require(previous['intent_id'] == ident['intent_id'] and ident['revision'] > previous['revision'], 'stale intent revision')
    task = None
    if value['intent_type'] == 'task_control':
        target = value['control']['target']
        entity_target = value['entities'].get('task_target')
        if target is not None and entity_target is not None and target != entity_target:
            ambiguities.append(_gap('/control/target', 'conflicting_target', '两个任务目标不同，请确认要控制哪个任务。'))
        target = target or entity_target
        matches = [t for t in ctx['tasks'] if target is None or t['task_id'] == target]
        if len(matches) == 1:
            task = matches[0]
        else:
            missing.append(_gap('/control/target', 'ambiguous_reference', '请指定一个当前可访问的任务。'))
    elif 'task_target' in value['entities']:
        if not any(t['task_id'] == value['entities']['task_target'] for t in ctx['tasks']):
            missing.append(_gap('/entities/task_target', 'binding_required', '请确认当前可访问的任务。'))
    if any(c['kind'] == 'other' for c in value['constraints']):
        missing.append(_gap('/constraints', 'unsupported_constraint', '请明确可由当前系统检查的限制。'))

    kind = value['intent_type']
    if missing or ambiguities:
        status, route = 'needs_clarification', 'clarify'
    elif kind == 'unknown':
        status, route = 'unsupported', 'unsupported'
    else:
        route = 'task_control' if kind == 'task_control' else (
            'direct_answer' if kind == 'question' and not ctx['needs_evidence'] else 'plan')
        status = 'resolved'
        if route not in ctx['available_routes']:
            status, route = 'unsupported', 'unsupported'
    value.update(identity=ident, resolution_status=status, route=route,
                 context_binding={'context_version': ctx['context_version'], 'previous_intent': previous, 'task': task},
                 clarification=None)
    if status == 'needs_clarification':
        value['clarification'] = {'question': (missing + ambiguities)[0]['reason'],
                                  'candidates': (missing + ambiguities)[0].get('candidates', []),
                                  'revision': ident['revision']}
    value['identity']['content_hash'] = content_hash(value)
    return ResolvedIntent.from_dict(value)
