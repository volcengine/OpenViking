# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
import os
from typing import Dict, Optional

from openviking.observability.log_safety import safe_path_for_log, short_hash
from openviking.storage.vectordb.project.local_project import (
    LocalProject,
    get_or_create_local_project,
)
from openviking.storage.vectordb.utils.dict_utils import ThreadSafeDictManager
from openviking.storage.vectordb.utils.path_safety import (
    resolve_storage_path,
    safe_join,
    safe_join_name,
)
from openviking.storage.vectordb.utils.validation import validate_name_str
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


def get_or_create_project_group(path: str = ""):
    """
    Get or create project group

    Args:
        path: Project group path
            - If empty: Create volatile project group, all projects stored in memory
            - If not empty: Create persistent project group, auto-load all existing projects in directory

    Returns:
        ProjectGroup instance
    """
    if not path:
        # Volatile project group - not persisted
        group = ProjectGroup(path="")
        return group
    else:
        # Persistent project group - persisted to disk
        group_path = str(resolve_storage_path(path))
        os.makedirs(group_path, exist_ok=True)
        group = ProjectGroup(path=group_path)
        return group


class ProjectGroup:
    """
    Project group class, manages multiple Projects

    Supports two modes:
    1. Volatile mode (path=""): projects stored in memory, not persisted
    2. Persistent mode (path!=""): projects persisted to disk, auto-load existing projects
    """

    def __init__(self, path: str = ""):
        """
        Initialize project group

        Args:
            path: Project group path
                - If empty: Create volatile project group
                - If not empty: Create persistent project group, auto-load all existing projects in directory
        """
        self.path = str(resolve_storage_path(path)) if path else ""
        self.projects = ThreadSafeDictManager[LocalProject]()

        # If persistent project group, load existing projects
        if self.path:
            self._load_existing_projects()
        else:
            # Volatile mode: create default project
            self.projects.set("default", get_or_create_local_project(path=""))

    def _load_existing_projects(self):
        """
        Load existing projects from disk
        Scan all subdirectories under path, each subdirectory is treated as a project
        """
        if not os.path.exists(self.path):
            logger.info("ProjectGroup path does not exist: %s", safe_path_for_log(str(self.path)))
            # Create default project
            default_path = safe_join(self.path, "default")
            os.makedirs(default_path, exist_ok=True)
            self.projects.set("default", get_or_create_local_project(path=str(default_path)))
            return

        # Scan all subdirectories
        try:
            entries = os.listdir(self.path)
        except Exception as e:
            logger.info(
                "Failed to list directory %s: %s",
                safe_path_for_log(str(self.path)),
                type(e).__name__,
            )
            return

        loaded_count = 0
        skipped_projects = 0
        for entry in entries:
            try:
                entry_path = safe_join(self.path, entry)
            except ValueError as _log_error:
                skipped_projects += 1
                logger.debug(
                    "Skipping invalid project directory under %s: %s error_type=%s",
                    safe_path_for_log(str(self.path)),
                    safe_path_for_log(str(entry)),
                    type(_log_error).__name__,
                )
                continue

            # Only process directories
            if not entry_path.is_dir():
                continue

            # Use directory name as project name
            project_name = entry

            try:
                # Load project
                logger.debug(
                    "Loading project: %s from %s",
                    short_hash(str(project_name)),
                    safe_path_for_log(str(entry_path)),
                )
                project = get_or_create_local_project(path=str(entry_path))
                self.projects.set(project_name, project)
                loaded_count += 1
                logger.debug("Successfully loaded project: %s", short_hash(str(project_name)))
            except Exception as e:
                skipped_projects += 1
                logger.debug(
                    "Failed to load project from %s: %s",
                    safe_path_for_log(str(entry_path)),
                    type(e).__name__,
                )
                continue

        if skipped_projects:
            logger.info("Project loading incomplete skipped=%d degraded=true", skipped_projects)
        logger.info("Loaded %s projects from %s", loaded_count, safe_path_for_log(str(self.path)))

        # If no projects loaded, create default project
        if loaded_count == 0:
            logger.info("No projects found, creating default project")
            default_path = safe_join(self.path, "default")
            os.makedirs(default_path, exist_ok=True)
            self.projects.set("default", get_or_create_local_project(path=str(default_path)))

    def close(self):
        """Close project group, release all project resources"""

        def close_project(name, project):
            project.close()

        self.projects.iterate(close_project)
        self.projects.clear()

    def has_project(self, project_name: str) -> bool:
        """
        Check if project exists

        Args:
            project_name: Project name

        Returns:
            True if exists, otherwise False
        """
        return self.projects.has(project_name)

    def get_project(self, project_name: str) -> Optional[LocalProject]:
        """
        Get project by name

        Args:
            project_name: Project name

        Returns:
            LocalProject instance, returns None if not exists
        """
        return self.projects.get(project_name)

    def get_projects(self) -> Dict[str, LocalProject]:
        """
        Get all projects

        Returns:
            Dictionary of project_name -> LocalProject
        """
        return self.projects.get_all()

    def create_project(self, project_name: str) -> LocalProject:
        """
        Create new project

        Args:
            project_name: Project name

        Returns:
            Newly created LocalProject instance

        Raises:
            ValueError: If project already exists
        """
        if self.has_project(project_name):
            raise ValueError(f"Project {project_name} already exists")

        # Decide whether to create volatile or persistent project based on project group path
        if self.path:
            # Persistent project
            validate_name_str(project_name)
            project_path = safe_join_name(self.path, project_name)
            os.makedirs(project_path, exist_ok=True)
            logger.debug(
                "Creating persistent project: %s at %s",
                short_hash(str(project_name)),
                safe_path_for_log(str(project_path)),
            )
            project = get_or_create_local_project(path=str(project_path))
        else:
            # Volatile project
            logger.debug("Creating volatile project: %s", short_hash(str(project_name)))
            project = get_or_create_local_project(path="")

        self.projects.set(project_name, project)
        return project

    def get_or_create_project(self, project_name: str) -> LocalProject:
        """
        Get or create project

        Args:
            project_name: Project name

        Returns:
            LocalProject instance
        """
        project = self.get_project(project_name)
        if project:
            return project

        return self.create_project(project_name)

    def create_local_project(self, project_name: str) -> LocalProject:
        """
        Create local project (compatible with old interface)

        Args:
            project_name: Project name

        Returns:
            LocalProject instance
        """
        return self.create_project(project_name)

    def get_or_create_local_project(self, project_name: str) -> LocalProject:
        """
        Get or create local project (compatible with old interface)

        Args:
            project_name: Project name

        Returns:
            LocalProject instance
        """
        return self.get_or_create_project(project_name)

    def delete_project(self, project_name: str):
        """
        Delete specified project

        Args:
            project_name: Project name
        """
        project = self.projects.get(project_name)
        if project:
            # Close project and delete all collections
            for collection_name in list(project.list_collections()):
                project.drop_collection(collection_name)
            project.close()
            self.projects.remove(project_name)
            logger.debug("Deleted project: %s", short_hash(str(project_name)))

    def drop_project(self, project_name: str):
        """
        Delete specified project (alias)

        Args:
            project_name: Project name
        """
        self.delete_project(project_name)
