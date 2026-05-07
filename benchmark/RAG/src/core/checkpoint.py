import os
import json
import hashlib
import threading
from datetime import datetime
from typing import Dict, List, Any, Optional, Set
from .logger import get_logger


class CheckpointManager:
    def __init__(self, checkpoint_dir: str, config: Dict[str, Any]):
        self.logger = get_logger()
        self.checkpoint_dir = checkpoint_dir
        self.checkpoint_file = os.path.join(checkpoint_dir, "benchmark_checkpoint.json")
        self.config = config
        self.config_hash = self._compute_config_hash(config)
        self._lock = threading.Lock()
        
        if not os.path.exists(self.checkpoint_dir):
            os.makedirs(self.checkpoint_dir, exist_ok=True)
    
    def _compute_config_hash(self, config: Dict[str, Any]) -> str:
        config_copy = config.copy()
        
        config_str = json.dumps(config_copy, sort_keys=True)
        return hashlib.md5(config_str.encode('utf-8')).hexdigest()
    
    def _validate_config(self, checkpoint_data: Dict[str, Any]) -> bool:
        saved_hash = checkpoint_data.get("config_hash")
        if saved_hash != self.config_hash:
            self.logger.warning(
                f"Config mismatch detected! Saved hash: {saved_hash[:8]}..., "
                f"Current hash: {self.config_hash[:8]}..."
            )
            return False
        return True
    
    def checkpoint_exists(self) -> bool:
        return os.path.exists(self.checkpoint_file)
    
    def load_checkpoint(self) -> Optional[Dict[str, Any]]:
        if not self.checkpoint_exists():
            return None
        
        try:
            with self._lock:
                with open(self.checkpoint_file, "r", encoding="utf-8") as f:
                    checkpoint_data = json.load(f)
            
            if not self._validate_config(checkpoint_data):
                self.logger.error(
                    "Config mismatch detected! Checkpoint not loaded to prevent overwriting results. "
                    "Please either:\n"
                    "1. Revert to the original config, OR\n"
                    "2. Change the output_dir in config to use a different directory"
                )
                return None
            
            self.logger.info(f"Checkpoint loaded from {self.checkpoint_file}")
            return checkpoint_data
        except Exception as e:
            self.logger.error(f"Failed to load checkpoint: {e}")
            return None
    
    def save_checkpoint(self, execution_state: Dict[str, Any], metadata: Optional[Dict[str, Any]] = None):
        checkpoint_data = {
            "checkpoint_version": "1.0",
            "created_at": datetime.now().isoformat(),
            "last_updated": datetime.now().isoformat(),
            "config_hash": self.config_hash,
            "execution_state": execution_state,
            "metadata": metadata or {}
        }
        
        try:
            with self._lock:
                if os.path.exists(self.checkpoint_file):
                    with open(self.checkpoint_file, "r", encoding="utf-8") as f:
                        old_data = json.load(f)
                    checkpoint_data["created_at"] = old_data.get("created_at", checkpoint_data["created_at"])
                
                with open(self.checkpoint_file, "w", encoding="utf-8") as f:
                    json.dump(checkpoint_data, f, indent=2, ensure_ascii=False)
            
            self.logger.debug(f"Checkpoint saved to {self.checkpoint_file}")
        except Exception as e:
            self.logger.error(f"Failed to save checkpoint: {e}")
    
    def delete_checkpoint(self):
        with self._lock:
            if os.path.exists(self.checkpoint_file):
                os.remove(self.checkpoint_file)
                self.logger.info(f"Checkpoint deleted: {self.checkpoint_file}")
    
    def get_completed_tasks(self, step: str) -> Set[int]:
        checkpoint = self.load_checkpoint()
        if not checkpoint:
            return set()
        
        execution_state = checkpoint.get("execution_state", {})
        if execution_state.get("current_step") != step:
            return set()
        
        return set(execution_state.get("completed_tasks", []))
    
    def update_completed_tasks(self, step: str, completed_tasks: Set[int], total_tasks: int):
        execution_state = {
            "current_step": step,
            "completed_tasks": list(completed_tasks),
            "total_tasks": total_tasks
        }
        metadata = {
            "dataset_name": self.config.get("dataset_name", "Unknown"),
            "output_dir": self.config.get("paths", {}).get("output_dir", "")
        }
        self.save_checkpoint(execution_state, metadata)
