from __future__ import annotations
from .domain import ConflictError, ValidationError
TITLE='建筑抗震鉴定与加固排序'; ENTITY='抗震鉴定'; ID_PREFIX='SR'
SEVERITIES=['low', 'medium', 'high', 'severe']; STATES=['proposed', 'assessed', 'design', 'construction', 'accepted', 'rejected']; TRANSITIONS={'proposed': ['assessed'], 'assessed': ['design', 'rejected'], 'design': ['construction'], 'construction': ['accepted'], 'accepted': ['rejected'], 'rejected': []}; TRANSITION_ROLES={'assessed': ['assessor'], 'design': ['structural_engineer'], 'construction': ['structural_engineer'], 'accepted': ['review_board'], 'rejected': ['review_board']}
CREATE_ROLES=set(['assessor']); RECORD_ROLES=set(['assessor', 'structural_engineer']); AUDIT_ROLES=set(['review_board', 'viewer']); VIEW_ROLES=set(['assessor', 'structural_engineer', 'review_board', 'viewer'])
SEVERITY_WEIGHT={'low': 1.0, 'medium': 3.0, 'high': 6.0, 'severe': 9.0}; DEADLINE_HOURS={'low': 72, 'medium': 24, 'high': 8, 'severe': 4}; TERMINAL_STATES=set(['accepted', 'rejected'])
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
BATCH_ENTITY='鉴定批次'; BATCH_STATES=['active','closed']; ACTIVE_BATCH=BATCH_STATES[0]; CLOSED_BATCH=BATCH_STATES[-1]
BATCH_CREATE_ROLES=set(['assessor']); BATCH_CLOSE_ROLES=set(['chief_engineer']); BATCH_VIEW_ROLES=set(['assessor','structural_engineer','review_board','viewer','chief_engineer'])
def batch_blockers(entries):
    blockers=[]
    for entry in entries:
        reasons=[]
        if entry['status'] not in TERMINAL_STATES: reasons.append(f"项目未到终态({entry['status']})")
        if entry['open_records']>0: reasons.append(f"未关闭事项{entry['open_records']}项")
        if reasons: blockers.append({'item_id':entry['item_id'],'title':entry.get('title'),'reasons':reasons})
    return blockers
def days_remaining(planned_date,today=None):
    from datetime import date
    if today is None: today=date.today()
    if isinstance(today,str): today=date.fromisoformat(today)
    if isinstance(planned_date,str): planned_date=date.fromisoformat(planned_date)
    return (planned_date-today).days
def batch_priority(max_item_priority,total_open_records,remaining_days):
    if remaining_days<0: urgency=3
    elif remaining_days<=1: urgency=2
    elif remaining_days<=3: urgency=1
    else: urgency=0
    score=max_item_priority/2.0+min(2,total_open_records)+urgency
    return max(0,min(10,int(round(score))))
