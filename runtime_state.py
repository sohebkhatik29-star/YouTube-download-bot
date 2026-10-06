import json
import logging
import os
from typing import List, Set


class RuntimeState:
    """Manages persistent bot state such as user tracking and whitelisting."""

    def __init__(self, data_dir: str):
        self.data_dir = data_dir
        self.whitelist_file = os.path.join(data_dir, "whitelist.json")
        self.stats_file = os.path.join(data_dir, "bot_stats.json")
        self.whitelisted_users: Set[int] = set()
        self.known_users: Set[int] = set()
        self.total_downloads: int = 0
        self._load_state()

    def _load_state(self):
        """Load state from JSON files if they exist."""
        # Load Whitelist
        if os.path.exists(self.whitelist_file):
            try:
                with open(self.whitelist_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.whitelisted_users = set(data.get("user_ids", []))
                    logging.info(f"Loaded {len(self.whitelisted_users)} users from {self.whitelist_file}")
            except Exception as e:
                logging.error(f"Error loading whitelist: {e}")
        else:
            # Seed from env var if available
            initial_whitelist = os.getenv("WHITELIST", "")
            if initial_whitelist:
                try:
                    user_ids = [int(uid.strip()) for uid in initial_whitelist.split(",") if uid.strip()]
                    self.whitelisted_users = set(user_ids)
                    self.save_whitelist()
                    logging.info(f"Seeded {len(self.whitelisted_users)} users into {self.whitelist_file}")
                except Exception as e:
                    logging.error(f"Failed to parse initial WHITELIST env var: {e}")

        # Load Stats & Known Users
        if os.path.exists(self.stats_file):
            try:
                with open(self.stats_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.known_users = set(data.get("known_users", []))
                    self.total_downloads = data.get("total_downloads", 0)
            except Exception as e:
                logging.error(f"Error loading stats file: {e}")

    def save_whitelist(self):
        """Save whitelist to JSON."""
        try:
            with open(self.whitelist_file, "w", encoding="utf-8") as f:
                json.dump({"user_ids": list(self.whitelisted_users), "version": 1}, f, indent=2)
        except Exception as e:
            logging.error(f"Error saving whitelist: {e}")

    def save_stats(self):
        """Save known users and download count."""
        try:
            with open(self.stats_file, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "known_users": list(self.known_users),
                        "total_downloads": self.total_downloads,
                    },
                    f,
                    indent=2,
                )
        except Exception as e:
            logging.error(f"Error saving stats: {e}")

    def is_user_whitelisted(self, user_id: int) -> bool:
        return user_id in self.whitelisted_users

    def add_user_to_whitelist(self, user_id: int):
        self.whitelisted_users.add(user_id)
        self.save_whitelist()

    def remove_user_from_whitelist(self, user_id: int):
        if user_id in self.whitelisted_users:
            self.whitelisted_users.remove(user_id)
            self.save_whitelist()

    def record_user_activity(self, user_id: int):
        if user_id not in self.known_users:
            self.known_users.add(user_id)
            self.save_stats()

    def increment_download_count(self):
        self.total_downloads += 1
        self.save_stats()

    def get_whitelist_size(self) -> int:
        return len(self.whitelisted_users)

    def get_known_users_count(self) -> int:
        return len(self.known_users)

    def get_all_known_users(self) -> List[int]:
        return list(self.known_users)
