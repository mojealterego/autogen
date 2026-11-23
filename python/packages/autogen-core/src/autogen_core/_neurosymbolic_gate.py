"""Small, typed, total AST policy interpreter with no eval or code execution.

Only flat JSON primitives are allowed. Evidence hashes are unkeyed; callers
must pin them externally if they require tamper-evident logs.
"""
from __future__ import annotations

import ast
import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from ._governed_workflow import _canonical


class SymbolicDenied(ValueError):
    """A rule, context, or proof was denied."""


Types = Literal['str','int','bool']


def _name(n: Any) -> bool:
    return isinstance(n,str) and n.isidentifier() and not n.startswith('_') and 0<len(n)<=128


@dataclass(frozen=True)
class SymbolicRule:
    name: str
    expression: str

    def __post_init__(self) -> None:
        if not _name(self.name) or not isinstance(self.expression,str) or not 0<len(self.expression)<=512:
            raise SymbolicDenied('invalid symbolic rule')


@dataclass(frozen=True)
class SymbolicVerdict:
    accepted: bool
    rule_passes: tuple[bool, ...]
    context_digest: str
    evidence_head: str


_ALLOWED=(ast.Expression,ast.BoolOp,ast.Compare,ast.UnaryOp,ast.BinOp,
          ast.Name,ast.Load,ast.Constant,ast.And,ast.Or,ast.Not,ast.USub,
          ast.Add,ast.Sub,ast.Eq,ast.NotEq,ast.Lt,ast.LtE,ast.Gt,ast.GtE)


def _constant_bound(node: ast.AST) -> int:
    if isinstance(node,ast.Constant) and type(node.value) is int:
        return node.value
    if isinstance(node,ast.UnaryOp) and isinstance(node.op,ast.USub):
        return -_constant_bound(node.operand)
    if isinstance(node,ast.BinOp) and isinstance(node.op,(ast.Add,ast.Sub)):
        a,b=_constant_bound(node.left),_constant_bound(node.right)
        result=a+b if isinstance(node.op,ast.Add) else a-b
        if abs(result)>10**12: raise SymbolicDenied('constant arithmetic exceeds bound')
        return result
    return 0


def _compile(rule: SymbolicRule, variables: Mapping[str, Types]) -> ast.Expression:
    try:
        root=ast.parse(rule.expression,mode='eval')
    except (ValueError,SyntaxError,RecursionError) as exc:
        raise SymbolicDenied('invalid symbolic syntax') from exc
    pending=[(root,0)]
    seen=0
    while pending:
        node,depth=pending.pop()
        seen+=1
        if type(node) not in _ALLOWED or seen>100 or depth>16:
            raise SymbolicDenied('unsupported or unbounded symbolic expression')
        if isinstance(node,ast.Name) and node.id not in variables:
            raise SymbolicDenied('unknown symbolic identifier')
        if isinstance(node,ast.Constant):
            v=node.value
            if not (type(v) in (bool,int,str) and (type(v) is not int or abs(v)<=10**12)
                    and (type(v) is not str or len(v)<=256)):
                raise SymbolicDenied('unsupported or unbounded constant')
        pending.extend((child,depth+1) for child in ast.iter_child_nodes(node))
    _constant_bound(root.body)
    return root


def _bool(value: Any) -> bool:
    if type(value) is not bool: raise SymbolicDenied('boolean expression expected')
    return value


def _number(value: Any) -> int:
    if type(value) is not int or abs(value)>10**12:
        raise SymbolicDenied('bounded integer expected')
    return value


def _eval(node: ast.AST, ctx: Mapping[str,Any]) -> Any:
    if isinstance(node,ast.Expression):return _eval(node.body,ctx)
    if isinstance(node,ast.Constant):return node.value
    if isinstance(node,ast.Name):return ctx[node.id]
    if isinstance(node,ast.BoolOp):
        vals=[_bool(_eval(n,ctx)) for n in node.values]
        return all(vals) if isinstance(node.op,ast.And) else any(vals)
    if isinstance(node,ast.UnaryOp):
        val=_eval(node.operand,ctx)
        if isinstance(node.op,ast.Not):return not _bool(val)
        return _number(-_number(val))
    if isinstance(node,ast.BinOp):
        a,b=_number(_eval(node.left,ctx)),_number(_eval(node.right,ctx))
        return _number(a+b if isinstance(node.op,ast.Add) else a-b)
    if isinstance(node,ast.Compare):
        left=_eval(node.left,ctx)
        for op,rightnode in zip(node.ops,node.comparators):
            right=_eval(rightnode,ctx)
            if type(left) is not type(right): raise SymbolicDenied('incompatible symbolic types')
            if isinstance(op,ast.Eq):pass_=(left==right)
            elif isinstance(op,ast.NotEq):pass_=(left!=right)
            elif isinstance(op,ast.Lt):pass_=(left<right)
            elif isinstance(op,ast.LtE):pass_=(left<=right)
            elif isinstance(op,ast.Gt):pass_=(left>right)
            elif isinstance(op,ast.GtE):pass_=(left>=right)
            else: raise SymbolicDenied('unsupported comparator')
            if not pass_:return False
            left=right
        return True
    raise SymbolicDenied('unsupported AST')


class SymbolicGate:
    """Fail-closed allowlist with deterministic evaluation and chained metadata."""

    def __init__(self, variables: Mapping[str,Types], rules: Sequence[SymbolicRule]) -> None:
        if (not isinstance(variables,Mapping) or not 1<=len(variables)<=32
                or any(not _name(k) or v not in ('int','str','bool') for k,v in variables.items())
                or not 1<=len(rules)<=64 or any(not isinstance(r,SymbolicRule) for r in rules)
                or len({r.name for r in rules})!=len(rules)):
            raise SymbolicDenied('invalid symbolic policy')
        self._variables=dict(variables)
        self._rules=tuple(rules)
        self._trees=tuple(_compile(r,self._variables) for r in rules)
        self._identity=hashlib.sha256(_canonical([self._variables,[(r.name,r.expression) for r in rules]])).hexdigest()

    def _context(self, context: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(context,Mapping) or set(context)!=set(self._variables):
            raise SymbolicDenied('missing or additional symbolic inputs')
        values={}
        for k,t in self._variables.items():
            v=context[k]
            if ((t=='int' and (type(v) is not int or abs(v)>10**12)) or
                (t=='str' and (type(v) is not str or len(v)>256)) or
                (t=='bool' and type(v) is not bool)):
                raise SymbolicDenied('symbolic input type or budget denied')
            values[k]=v
        return values

    def evaluate(self, context: Mapping[str,Any]) -> SymbolicVerdict:
        values=self._context(context)
        results=[]
        for tree in self._trees:
            try: results.append(_bool(_eval(tree,values)))
            except (ValueError,TypeError,OverflowError,RecursionError) as exc:
                raise SymbolicDenied('symbolic expression execution denied') from exc
        digest=hashlib.sha256(_canonical(values)).hexdigest()
        checks=tuple(results)
        accepted=all(checks)
        head=hashlib.sha256(_canonical(['omega-symbolic-v1',self._identity,digest,checks,accepted])).hexdigest()
        return SymbolicVerdict(accepted,checks,digest,head)

    def verify_evidence(self, verdict: SymbolicVerdict) -> bool:
        if (not isinstance(verdict,SymbolicVerdict) or type(verdict.accepted) is not bool
                or len(verdict.rule_passes)!=len(self._rules)
                or any(type(x) is not bool for x in verdict.rule_passes)
                or verdict.accepted!=all(verdict.rule_passes)):
            return False
        candidate=hashlib.sha256(_canonical(['omega-symbolic-v1',self._identity,
                                             verdict.context_digest,verdict.rule_passes,verdict.accepted])).hexdigest()
        return candidate==verdict.evidence_head

    def require(self, context: Mapping[str,Any]) -> SymbolicVerdict:
        result=self.evaluate(context)
        if not result.accepted: raise SymbolicDenied('symbolic policy denied')
        return result


def symbolically_guarded(gate: SymbolicGate,
                         handler: Callable[[Mapping[str,Any]],Awaitable[Any]],
                         extract_context: Callable[[Mapping[str,Any]],Mapping[str,Any]]) -> Callable[[Mapping[str,Any]],Awaitable[Any]]:
    if not isinstance(gate,SymbolicGate) or not callable(handler) or not callable(extract_context):
        raise SymbolicDenied('trusted symbolic policy, handler and extractor required')
    async def invoke(inputs: Mapping[str,Any]) -> Any:
        gate.require(extract_context(inputs))
        return await handler(inputs)
    return invoke
