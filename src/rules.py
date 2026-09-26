from __future__ import annotations
from .domain import ConflictError, ValidationError
TITLE='水库防汛调度与操作确认'; ENTITY='调度指令'; ID_PREFIX='RF'
SEVERITIES=['routine', 'attention', 'urgent', 'emergency']; STATES=['draft', 'checked', 'authorized', 'executed', 'closed']; TRANSITIONS={'draft': ['checked'], 'checked': ['authorized'], 'authorized': ['executed'], 'executed': ['closed'], 'closed': []}; TRANSITION_ROLES={'checked': ['duty_officer'], 'authorized': ['chief_engineer'], 'executed': ['dispatcher'], 'closed': ['chief_engineer']}
CREATE_ROLES=set(['duty_officer']); RECORD_ROLES=set(['duty_officer', 'dispatcher']); AUDIT_ROLES=set(['chief_engineer', 'viewer']); VIEW_ROLES=set(['duty_officer', 'chief_engineer', 'dispatcher', 'viewer'])
TICKET_ENTITY='操作票据'; TICKET_STATES=['registered', 'confirmed', 'pending_redo']
TICKET_CREATE_ROLES=set(['dispatcher']); TICKET_CONFIRM_ROLES=set(['dispatcher', 'duty_officer']); TICKET_REVISE_ROLES=set(['dispatcher'])
TICKET_REGISTER_ITEM_STATES=set(['authorized', 'executed']); TICKET_CONFIRM_ITEM_STATES=set(['executed'])
SEVERITY_WEIGHT={'routine': 1.0, 'attention': 3.0, 'urgent': 6.0, 'emergency': 9.0}; DEADLINE_HOURS={'routine': 72, 'attention': 24, 'urgent': 8, 'emergency': 4}; TERMINAL_STATES=set(['closed'])
def priority_score(severity,quantity=0.0,threshold=1.0,open_records=0):
    if severity not in SEVERITY_WEIGHT: raise ValidationError("unknown severity")
    ratio=quantity/threshold if threshold>0 else 1.0
    return max(0,min(10,int(round(SEVERITY_WEIGHT[severity]+min(4.0,ratio*4.0)+min(3.0,float(open_records))))))
def response_deadline_hours(severity,quantity=0.0,threshold=1.0):
    if severity not in DEADLINE_HOURS: raise ValidationError("unknown severity")
    ratio=quantity/threshold if threshold>0 else 1.0
    return max(1,int(DEADLINE_HOURS[severity]/max(1.0,ratio)))
def escalation_required(severity,quantity=0.0,threshold=1.0):
    return severity==SEVERITIES[-1] or (threshold>0 and quantity>=threshold)
def can_transition(current,target): return target in TRANSITIONS.get(current,[])
def validate_transition(current,target):
    if current not in STATES or target not in STATES: raise ValidationError("未知状态")
    if not can_transition(current,target): raise ConflictError(f"不能从{current}转换到{target}")
def completion_blockers(target,open_records): return ["仍有未关闭事项"] if target in TERMINAL_STATES and open_records>0 else []
def role_for_transition(target): return set(TRANSITION_ROLES.get(target,[]))
def validate_ticket_plan(executor,reviewer):
    if executor==reviewer: raise ValidationError("执行人与复核人不能是同一账号")
def ticket_confirmation_outcome(field_level,actual_flow,level_lower,flow_upper,reviewer,actor):
    reasons=[]
    if actor!=reviewer: reasons.append("复核人已换班")
    if field_level<level_lower: reasons.append("现场库位低于库位下限")
    if actual_flow>flow_upper: reasons.append("实际过流超过过流上限")
    return ('pending_redo' if reasons else 'confirmed'),reasons
def ticket_archive_blockers(target,unconfirmed_tickets): return ["仍有未确认操作票据"] if target in TERMINAL_STATES and unconfirmed_tickets>0 else []
