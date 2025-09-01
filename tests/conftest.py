"""
Pytest configuration and shared fixtures.
"""

import pytest
import sys
import os
from pathlib import Path

# Add src directory to Python path for imports
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))


@pytest.fixture
def sample_translations():
    """Sample translations fixture for testing."""
    return {
        "en": {
            "title": "🎲 RPG Session Minutes",
            "subtitle": "Automated transcription and analysis for tabletop RPG sessions",
            "interface_language": "Interface Language",
            "tab_welcome": "Welcome",
            "tab_transcription": "🎤 Transcription",
            "tab_analysis": "🤖 AI Analysis",
            "welcome_text": "Welcome to the RPG Session Minutes application!",
            "upload_audio": "Upload Audio Files",
            "transcription_language": "Transcription Language",
            "whisper_model": "Whisper Model",
            "transcribe_button": "Start Transcription",
            "upload_transcription": "Upload Transcription File",
            "analysis_prompt": "Analysis Prompt",
            "ai_provider": "AI Provider",
            "ai_model": "AI Model",
            "analyze_button": "Analyze Session",
            "transcription_placeholder": "Transcription will appear here...",
            "analysis_placeholder": "AI analysis will appear here...",
            "upload_transcription_first": "Please upload a transcription first",
            "footer_text": "🚀 <strong>RPG Session Minutes</strong> - Transform your RPG sessions into lasting memories",
            "footer_info": "Powered by Whisper AI, OpenAI, and Gradio | Version 1.0.0"
        },
        "fr": {
            "title": "🎲 Comptes-Rendus de Sessions JdR",
            "subtitle": "Transcription et analyse automatisées pour sessions de jeux de rôle",
            "interface_language": "Langue de l'Interface",
            "tab_welcome": "Bienvenue",
            "tab_transcription": "🎤 Transcription",
            "tab_analysis": "🤖 Analyse IA",
            "welcome_text": "Bienvenue dans l'application Comptes-Rendus de Sessions JdR !",
            "upload_audio": "Téléverser des Fichiers Audio",
            "transcription_language": "Langue de Transcription",
            "whisper_model": "Modèle Whisper",
            "transcribe_button": "Démarrer la Transcription",
            "upload_transcription": "Téléverser un Fichier de Transcription",
            "analysis_prompt": "Prompt d'Analyse",
            "ai_provider": "Fournisseur IA",
            "ai_model": "Modèle IA",
            "analyze_button": "Analyser la Session",
            "transcription_placeholder": "La transcription apparaîtra ici...",
            "analysis_placeholder": "L'analyse IA apparaîtra ici...",
            "upload_transcription_first": "Veuillez d'abord téléverser une transcription",
            "footer_text": "🚀 <strong>Comptes-Rendus de Sessions JdR</strong> - Transformez vos sessions JdR en souvenirs durables",
            "footer_info": "Alimenté par Whisper AI, OpenAI, et Gradio | Version 1.0.0"
        }
    }


@pytest.fixture
def mock_config_files(tmp_path):
    """Create mock configuration files for testing."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    return {
        "config_dir": config_dir,
        "language_config": config_dir / "language_config.json",
        "translations": config_dir / "translations.json"
    }
