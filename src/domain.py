from __future__ import annotations
from dataclasses import dataclass
from datetime import date as _date
from typing import Any, Dict, Optional
class ErrorKind:
    VALIDATION="validation"; NOT_FOUND="not_found"; FORBIDDEN="forbidden"; CONFLICT="conflict"
class DomainError(Exception):
    kind=ErrorKind.VALIDATION
    def __init__(self,message): super().__init__(message); self.message=message
class ValidationError(DomainError): kind=ErrorKind.VALIDATION
class NotFoundError(DomainError): kind=ErrorKind.NOT_FOUND
class PermissionDenied(DomainError): kind=ErrorKind.FORBIDDEN
class ConflictError(DomainError): kind=ErrorKind.CONFLICT
SEVERITIES=['low', 'medium', 'high', 'severe']; STATES=['proposed', 'assessed', 'design', 'construction', 'accepted', 'rejected']; ROLES=['assessor', 'structural_engineer', 'review_board', 'viewer', 'chief_engineer']
@dataclass(frozen=True)
class Item:
    id:int; title:str; description:str; severity:str; quantity:float; threshold:float; status:str; version:int; external_ref:Optional[str]; created_by:str; created_at:str; updated_at:str
@dataclass(frozen=True)
class Record:
    id:int; item_id:int; kind:str; detail:str; status:str; external_ref:Optional[str]; created_by:str; created_at:str
@dataclass(frozen=True)
class AuditEntry:
    id:int; action:str; entity_type:str; entity_id:int; actor:str; detail:Dict[str,Any]; previous_hash:str; entry_hash:str; created_at:str
def require_text(value,field,max_length=2000):
    if not isinstance(value,str) or not value.strip(): raise ValidationError(f"{field}不能为空")
    value=value.strip()
    if len(value)>max_length: raise ValidationError(f"{field}不能超过{max_length}个字符")
    return value
def normalize_severity(value):
    if value not in SEVERITIES: raise ValidationError("severity不在允许范围内")
    return value
def require_number(value,field,minimum=0.0):
    if isinstance(value,bool): raise ValidationError(f"{field}必须是数字")
    try: number=float(value)
    except (TypeError,ValueError): raise ValidationError(f"{field}必须是数字")
    if number<minimum: raise ValidationError(f"{field}不能小于{minimum}")
    return number
def require_date(value,field):
    text=require_text(value,field,10)
    if len(text)!=10 or text[4]!='-' or text[7]!='-':
        raise ValidationError(f"{field}必须是YYYY-MM-DD格式")
    try: _date.fromisoformat(text)
    except ValueError as exc: raise ValidationError(f"{field}必须是YYYY-MM-DD格式") from exc
    return text
def require_id_list(value,field,min_items=1,max_items=500):
    if not isinstance(value,list) or not value: raise ValidationError(f"{field}必须是非空列表")
    result=[]
    for entry in value:
        if isinstance(entry,bool) or not isinstance(entry,int): raise ValidationError(f"{field}只能包含项目整数ID")
        if entry<1: raise ValidationError(f"{field}中的项目ID必须为正整数")
        if entry not in result: result.append(entry)
    if len(result)<min_items: raise ValidationError(f"{field}至少包含{min_items}个不同项目")
    if len(result)>max_items: raise ValidationError(f"{field}最多包含{max_items}个项目")
    return result
def ensure_role(role,allowed):
    if role not in allowed: raise PermissionDenied("当前角色无权执行该操作")
