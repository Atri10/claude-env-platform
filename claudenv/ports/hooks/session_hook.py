from claudenv.ports.hooks import IPostToolUseHook
from memory.manager import MemoryManager
from memory.session_ingestor import SessionIngestor
from typing import Dict

class SessionHook(IPostToolUseHook):
    def __init__(self, memory_manager: MemoryManager, session_ingestor: SessionIngestor):
        self.memory_manager = memory_manager
        self.session_ingestor = session_ingestor
        self._session_context = {}

    def on_tool_complete(self, tool_outcome: Dict) -> None:
        """
        Track session state from tool outcomes
        """
        pass

    def on_session_start(self, session_id: str) -> Dict:
        """
        Load memory context at session start

        Args:
            session_id (str): Unique session identifier

        Returns:
            Dict: Session context with loaded memory
        """
        self._session_context = self.memory_manager.load_session(session_id)
        return self._session_context

    def on_session_end(self, session_id: str) -> None:
        """
        Persist session state and cleanup

        Args:
            session_id (str): Unique session identifier
        """
        self.memory_manager.save_session(session_id, self._session_context)
        self.session_ingestor.finalize_session(session_id)

    def restore_checkpoint(self, session_id: str, checkpoint_id: str) -> Dict:
        """
        Restore session from a specific checkpoint

        Args:
            session_id (str): Unique session identifier
            checkpoint_id (str): Checkpoint identifier

        Returns:
            Dict: Restored session context
        """
        self._session_context = self.memory_manager.restore_checkpoint(session_id, checkpoint_id)
        return self._session_context