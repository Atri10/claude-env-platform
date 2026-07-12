from claudenv.ports.hooks import IPostToolUseHook
from audit.audit_logger import AuditLogger
from typing import Dict, List, Any

class AuditHook(IPostToolUseHook):
    def __init__(self, audit_logger: AuditLogger):
        self.audit_logger = audit_logger
        self._sinks: List[Any] = []

    def on_tool_complete(self, tool_outcome: Dict) -> None:
        """
        Record tool outcomes in the audit log

        Args:
            tool_outcome (Dict): Dictionary containing tool execution results
                - 'tool_name': str
                - 'outcome': bool (success/failure)
                - 'details': dict
        """
        self.audit_logger.log_event(
            tool_outcome.get('tool_name', 'unknown'),
            tool_outcome.get('details', {}),
            metadata={'outcome': tool_outcome.get('outcome', False)}
        )

    def record_decision(self, approval: Dict) -> None:
        """
        Capture approval or denial in audit trail
        """
        self.audit_logger.log_event(
            'decision_recorded',
            {
                'type': 'approval' if approval.get('approved') else 'denial',
                'resource': approval.get('resource', ''),
                'reason': approval.get('reason', '')
            },
            metadata={'source': 'hook', 'approved': approval.get('approved')}
        )

    def add_sink(self, sink: Any) -> None:
        """Add an audit event sink for streaming output"""
        self._sinks.append(sink)

    def flush(self) -> None:
        """
        Flush batched audit writes
        """
        for sink in self._sinks:
            sink.flush()
        self.audit_logger.flush()