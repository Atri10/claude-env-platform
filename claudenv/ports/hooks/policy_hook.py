from claudenv.ports.hooks import IPreToolUseHook
from security.policy_engine import PolicyEngine
from typing import Dict

class PolicyHook(IPreToolUseHook):
    def __init__(self, policy_engine: PolicyEngine):
        self.policy_engine = policy_engine

    def on_tool_call(self, tool_call: Dict) -> bool:
        """
        Validate path and command string against policy rules

        Args:
            tool_call (Dict): Dictionary containing tool_call information
                - 'command': str (Bash command string)
                - 'path': str (file path being accessed)

        Returns:
            bool: True if allowed, False to block execution
        """
        return self.policy_engine.evaluate_bash_command(
            tool_call.get('command', ''),
            tool_call.get('path', '')
        )

    def evaluate_bash_command(self, command: str, path: str) -> bool:
        """
        Delegate to PolicyEngine for detailed validation
        """
        return self.policy_engine.evaluate_bash_command(command, path)