"""Action safety model (ADR-006).

LEVEL 0 READ, 1 ANALYZE, 2 PREPARE, 3 EXECUTE_REVERSIBLE, 4 EXECUTE_SENSITIVE.
Levels 0-2 never touch external systems. Level 3 requires approval unless policy says otherwise.
Level 4 ALWAYS requires explicit approval (policy may only relax it deliberately, per action).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class ActionLevel(IntEnum):
    READ = 0
    ANALYZE = 1
    PREPARE = 2
    EXECUTE_REVERSIBLE = 3
    EXECUTE_SENSITIVE = 4


@dataclass
class PermissionVerdict:
    allowed: bool
    requires_approval: bool
    reason: str


@dataclass
class PermissionPolicy:
    auto_approve_reversible: bool = False
    # explicit allow-list of "tool.action" the user deliberately configured as auto-approved at level 4
    auto_approve_sensitive: set[str] = field(default_factory=set)
    denied_actions: set[str] = field(default_factory=set)

    def evaluate(self, tool: str, action: str, level: ActionLevel) -> PermissionVerdict:
        key = f"{tool}.{action}"
        if key in self.denied_actions:
            return PermissionVerdict(False, False, f"{key} is denied by policy")
        if level <= ActionLevel.PREPARE:
            return PermissionVerdict(True, False, f"level {level.name} never needs approval")
        if level == ActionLevel.EXECUTE_REVERSIBLE:
            if self.auto_approve_reversible:
                return PermissionVerdict(True, False, "reversible actions auto-approved by policy")
            return PermissionVerdict(True, True, "reversible external action requires approval")
        if key in self.auto_approve_sensitive:
            return PermissionVerdict(True, False, f"{key} explicitly auto-approved by user configuration")
        return PermissionVerdict(True, True, "sensitive action always requires explicit approval")
