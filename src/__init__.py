"""
RPG Session Minutes - A comprehensive toolkit for analyzing tabletop RPG sessions.

This package provides automated transcription, content processing, and AI
analysis capabilities for recorded RPG sessions.
"""

__version__ = "1.0.0"
__author__ = "RPG Session Minutes Contributors"
__email__ = "contact@rpgsessionminutes.com"

from .agent import Agent
from .ai_transcription_agent import AITranscriptionAgent
from .content_processing_agent import ContentProcessingAgent
from .ai_analysis_agent import AIAnalysisAgent
from .interface_agent import InterfaceAgent

__all__ = [
    "Agent",
    "AITranscriptionAgent",
    "ContentProcessingAgent",
    "AIAnalysisAgent",
    "InterfaceAgent"
]
