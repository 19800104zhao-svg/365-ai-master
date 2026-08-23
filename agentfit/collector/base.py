import hashlib
from abc import ABC, abstractmethod
from pathlib import Path
from agentfit.storage.database import DatabaseManager

class BaseCollector(ABC):
    def __init__(self, log_dir: Path, db: DatabaseManager, local_salt: str = "default_salt"):
        self.log_dir = log_dir
        self.db = db
        self.local_salt = local_salt

    def hash_project_path(self, path_str: str) -> str:
        salted = f"{self.local_salt}:{path_str}"
        return hashlib.sha256(salted.encode("utf-8")).hexdigest()[:16]

    @abstractmethod
    def scan(self) -> int:
        pass
