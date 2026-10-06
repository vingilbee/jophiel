from enum import StrEnum

class GoalFailurePhase(StrEnum):
    VALIDATION="validation"; EVIDENCE_RECONCILIATION="evidence_reconciliation"; LIFECYCLE_UPDATE="lifecycle_update"; CANDIDATE_FORMATION="candidate_formation"; ADOPTION="adoption"; PRIORITY_ASSIGNMENT="priority_assignment"; CONFLICT_RESOLUTION="conflict_resolution"; FOCUS_SELECTION="focus_selection"; SNAPSHOT_VALIDATION="snapshot_validation"

class GoalExecutionError(RuntimeError):
    def __init__(self, cognitive_turn_id: str, failed_phase: GoalFailurePhase, safe_message_category: str, safe_message: str):
        super().__init__(safe_message); self.cognitive_turn_id=cognitive_turn_id; self.failed_phase=failed_phase; self.safe_message_category=safe_message_category; self.safe_message=safe_message
