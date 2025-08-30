#!/usr/bin/env python3
"""
Base Agent Class

This module provides the foundational Agent class that all specialized agents inherit from.
It provides simple logging functionality with automatic class name detection.
"""

import logging
import sys
from abc import ABC
from pathlib import Path
from typing import Optional


class Agent(ABC):
    """
    Base class for all RPG Session Minutes agents.

    Provides simple logging functionality where child classes automatically
    get their class name in the log traces.
    """

    def __init__(self, log_level: str = "INFO", log_file: Optional[str] = None):
        """
        Initialize the base Agent.

        Args:
            log_level (str): Logging level ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
            log_file (str, optional): Path to log file. If None, logs to console only.
        """
        self.log_level = log_level.upper()
        self.log_file = log_file
        self._setup_logging()

    def _setup_logging(self):
        """Setup logging configuration."""
        # Get numeric level
        numeric_level = getattr(logging, self.log_level, logging.INFO)

        # Create formatter with class name
        formatter = logging.Formatter(
            fmt='%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

        # Setup console logging
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(numeric_level)
        console_handler.setFormatter(formatter)

        # Setup file logging if specified
        self.file_handler = None
        if self.log_file:
            try:
                log_path = Path(self.log_file)
                log_path.parent.mkdir(parents=True, exist_ok=True)

                self.file_handler = logging.FileHandler(self.log_file, encoding='utf-8')
                self.file_handler.setLevel(numeric_level)
                self.file_handler.setFormatter(formatter)
            except Exception:
                self.file_handler = None

    def logging(self, level: str, message: str):
        """
        Core logging function that adds class name and handles output.

        Args:
            level (str): Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
            message (str): Message to log
        """
        # Get the class name dynamically
        class_name = self.__class__.__name__

        # Format message with class name
        formatted_message = f"[{class_name}] {message}"

        # Get numeric level
        numeric_level = getattr(logging, level.upper(), logging.INFO)

        # Check if we should log at this level
        if numeric_level >= getattr(logging, self.log_level, logging.INFO):
            # Create log record
            from datetime import datetime
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            log_line = f"{timestamp} - {level.upper()} - {formatted_message}"

            # Output to console
            print(log_line)

            # Output to file if configured
            if self.file_handler:
                try:
                    with open(self.log_file, 'a', encoding='utf-8') as f:
                        f.write(log_line + '\n')
                except Exception:
                    pass  # Silently fail if file logging doesn't work

    def debug(self, message: str):
        """Log a debug message."""
        self.logging("DEBUG", message)

    def info(self, message: str):
        """Log an info message."""
        self.logging("INFO", message)

    def warning(self, message: str):
        """Log a warning message."""
        self.logging("WARNING", message)

    def error(self, message: str):
        """Log an error message."""
        self.logging("ERROR", message)

    def critical(self, message: str):
        """Log a critical message."""
        self.logging("CRITICAL", message)
