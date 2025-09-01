#!/usr/bin/env python3
"""
Interface Agent - Reactive I18n Implementation

This agent provides a clean Gradio web interface with reactive
internationalization that updates instantly when language changes,
without page reloads.
"""

from typing import Optional
import json
from pathlib import Path
import gradio as gr

try:
    from ..agent import Agent
except ImportError:
    # Fallback for direct execution or testing
    import sys
    import os
    sys.path.append(os.path.dirname(__file__))
    from agent import Agent


class InterfaceAgent(Agent):
    """Agent providing a reactive Gradio interface with i18n support."""

    def __init__(self):
        super().__init__()
        self.language = "en"  # Default to English
        self.current_dir = Path(__file__).parent
        self.translations_file = (
            self.current_dir.parent / "config" / "translations.json"
        )
        self.language_config_file = (
            self.current_dir.parent / "config" / "language_config.json"
        )

        # Load translations and user preference
        self.translations = self._load_translations()
        self._load_saved_language()  # Load but keep default as English for startup

        # Pre-render translations for performance
        self.rendered_translations = (
            self._prerender_translations()
        )

        self.info(f"InterfaceAgent initialized with language: {self.language}")

    def _load_translations(self) -> dict:
        """Load translation dictionary from JSON file.
        Returns None if loading fails.
        """
        try:
            if self.translations_file.exists():
                with open(self.translations_file, 'r', encoding='utf-8') as f:
                    translations = json.load(f)
                    self.info(f"Loaded translations from {self.translations_file}")
                    return translations
            else:
                self.warning(f"Translations file not found: "
                           f"{self.translations_file}")
        except Exception as e:
            self.error(f"Failed to load translations: {e}")

        # Fallback translations
        return {
            "en": {
                "title": "🎲 RPG Session Minutes",
                "subtitle": "Automated transcription and analysis for "
                           "tabletop RPG sessions",
                "interface_language": "Interface Language",
                "tab_welcome": "Welcome",
                "tab_transcription": "🎤 Transcription",
                "tab_analysis": "🤖 AI Analysis",
                "welcome_text": (
                    "\nWelcome to the RPG Session Minutes application! "
                    "This tool helps you process and analyze your tabletop "
                    "RPG sessions.\n\n## Features:\n"
                    "- **🎤 Audio Transcription**: Convert recorded sessions "
                    "to text\n"
                    "- **📝 Content Processing**: Clean and format "
                    "transcriptions\n"
                    "- **🤖 AI Analysis**: Generate structured session "
                    "reports\n"
                    "- **📊 Session Summaries**: Get insights and "
                    "recommendations\n\n"
                    "## Getting Started:\n"
                    "1. Upload your session audio files\n"
                    "2. Configure transcription settings\n"
                    "3. Process and analyze your session\n"
                    "4. Download the generated reports\n\n"
                    "**Note**: This is currently a demo interface. "
                    "Full functionality will be implemented in future "
                    "updates."
                ),
                "upload_audio": "Upload Audio Files",
                "transcription_language": "Transcription Language",
                "whisper_model": "Whisper Model",
                "start_transcription": "🎤 Start Transcription",
                "transcription_results": "Transcription Results",
                "transcription_placeholder": "Transcription results will appear here...",
                "upload_first": "⚠️ Please upload audio files first.",
                "upload_transcription": "Upload Transcription File",
                "analysis_prompt": "Analysis Prompt",
                "prompt_placeholder": "Enter your analysis prompt here...",
                "prompt_default": ("Analyze this RPG session transcription "
                                  "and provide a structured summary."),
                "ai_provider": "AI Provider",
                "model_label": "Model",
                "analyze_session": "🤖 Analyze Session",
                "analysis_results": "Analysis Results",
                "analysis_placeholder": "AI analysis will appear here...",
                "upload_transcription_first": "⚠️ Please upload transcription first.",
                "footer_text": ("🚀 <strong>RPG Session Minutes</strong> - "
                               "Transform your RPG sessions into lasting memories"),
                "footer_info": ("Powered by Whisper AI, OpenAI, and Gradio | "
                              "Version 1.0.0")
            },
            "fr": {
                "title": "🎲 Comptes-Rendus de Sessions JdR",
                "subtitle": ("Transcription et analyse automatisées pour "
                           "sessions de jeu de rôle sur table"),
                "interface_language": "Langue de l'Interface",
                "tab_welcome": "Accueil",
                "tab_transcription": "🎤 Transcription",
                "tab_analysis": "🤖 Analyse IA",
                "welcome_text": (
                    "\nBienvenue dans l'application Comptes-Rendus de "
                    "Sessions JdR ! Cet outil vous aide à traiter et "
                    "analyser vos sessions de jeu de rôle sur table.\n\n"
                    "## Fonctionnalités :\n"
                    "- **🎤 Transcription Audio** : Convertir les sessions "
                    "enregistrées en texte\n"
                    "- **📝 Traitement du Contenu** : Nettoyer et formater "
                    "les transcriptions\n"
                    "- **🤖 Analyse IA** : Générer des rapports de session "
                    "structurés\n"
                    "- **📊 Résumés de Session** : Obtenir des insights "
                    "et recommandations\n\n"
                    "## Pour Commencer :\n"
                    "1. Téléversez vos fichiers audio de session\n"
                    "2. Configurez les paramètres de transcription\n"
                    "3. Traitez et analysez votre session\n"
                    "4. Téléchargez les rapports générés\n\n"
                    "**Note** : Il s'agit actuellement d'une interface "
                    "de démonstration. Les fonctionnalités complètes "
                    "seront implémentées dans les prochaines mises à jour."
                ),
                "upload_audio": "Téléverser Fichiers Audio",
                "transcription_language": "Langue de Transcription",
                "whisper_model": "Modèle Whisper",
                "start_transcription": "🎤 Commencer Transcription",
                "transcription_results": "Résultats de Transcription",
                "transcription_placeholder": ("Les résultats de transcription "
                                            "apparaîtront ici..."),
                "upload_first": ("⚠️ Veuillez d'abord téléverser des "
                               "fichiers audio."),
                "upload_transcription": "Téléverser Fichier de Transcription",
                "analysis_prompt": "Prompt d'Analyse",
                "prompt_placeholder": ("Entrez votre prompt d'analyse ici..."),
                "prompt_default": ("Analysez cette transcription de session "
                                 "JdR et fournissez un résumé structuré."),
                "ai_provider": "Fournisseur IA",
                "model_label": "Modèle",
                "analyze_session": "🤖 Analyser Session",
                "analysis_results": "Résultats d'Analyse",
                "analysis_placeholder": ("L'analyse IA apparaîtra ici..."),
                "upload_transcription_first": ("⚠️ Veuillez d'abord téléverser "
                                             "la transcription."),
                "footer_text": ("🚀 <strong>Comptes-Rendus de Sessions "
                               "JdR</strong> - Transformez vos sessions JdR "
                               "en souvenirs durables"),
                "footer_info": ("Alimenté par Whisper AI, OpenAI, et Gradio | "
                              "Version 1.0.0")
            }
        }

    def _load_saved_language(self) -> Optional[str]:
        """Load user's saved language preference from
        configuration file.
        """
        try:
            if self.language_config_file.exists():
                with open(
                    self.language_config_file, 'r', encoding='utf-8'
                ) as f:
                    config = json.load(f)
                    return config.get('language')
        except Exception as e:
            self.warning(f"Could not load saved language: {e}")
        return None

    def _save_language_preference(self, language: str) -> None:
        """Save the selected language to configuration file
        for persistence.
        """
        try:
            # Ensure directory exists
            self.language_config_file.parent.mkdir(
                parents=True, exist_ok=True
            )

            config = {'language': language}
            with open(
                self.language_config_file, 'w', encoding='utf-8'
            ) as f:
                json.dump(config, f, ensure_ascii=False, indent=2)

            self.info(f"Saved language preference: {language}")
        except Exception as e:
            self.error(f"Failed to save language preference: {e}")

    def get_translation(self, key: str, language: str = None) -> str:
        """Get translation for a key in the current or specified language."""
        if language is None:
            language = self.language

        try:
            return self.translations[language][key]
        except KeyError:
            self.warning(f"Translation key '{key}' not found for "
                        f"language '{language}'")
            # Try English fallback
            try:
                fallback = self.translations["en"][key]
                self.debug(f"Using English fallback for '{key}': {fallback}")
                return fallback
            except KeyError:
                self.error(f"Translation key '{key}' not found in "
                          f"English fallback")
                return f"[MISSING: {key}]"

    def _prerender_translations(self) -> dict:
        """Pre-render translations for better performance."""
        self.debug("Pre-rendering translations for performance optimization")
        rendered = {}

        for lang in self.translations:
            rendered[lang] = {}
            for key, value in self.translations[lang].items():
                # Pre-render HTML content if needed
                if 'footer_text' in key or 'welcome_text' in key:
                    rendered[lang][key] = value
                else:
                    rendered[lang][key] = value

        return rendered

    def create_interface(self):
        """Create and return the Gradio interface with reactive i18n."""
        self.info("Creating Gradio interface with reactive i18n")

        # Get initial translations
        def get_trans(key: str) -> str:
            return self.get_translation(key, self.language)

        with gr.Blocks(
            title=get_trans("title"),
            theme=gr.themes.Soft(),
            css="""
            .flag-emoji {
                font-family: "Apple Color Emoji", "Segoe UI Emoji",
                           "Noto Color Emoji", "Twemoji Mozilla",
                           "EmojiOne Color", "Android Emoji",
                           "EmojiSymbols", sans-serif !important;
                font-size: 16px !important;
                line-height: 1 !important;
            }
            .gradio-container .flag-emoji {
                font-family: "Apple Color Emoji", "Segoe UI Emoji",
                           "Noto Color Emoji", "Twemoji Mozilla",
                           "EmojiOne Color", "Android Emoji",
                           "EmojiSymbols", sans-serif !important;
            }
            .radio-group .flag-emoji {
                font-family: "Apple Color Emoji", "Segoe UI Emoji",
                           "Noto Color Emoji", "Twemoji Mozilla",
                           "EmojiOne Color", "Android Emoji",
                           "EmojiSymbols", sans-serif !important;
            }
            .center-title {
                text-align: center;
            }
            """
        ) as interface:

            # Header with centered title and right-aligned language selector
            with gr.Row():
                with gr.Column(scale=1):
                    gr.HTML("")  # Empty space
                with gr.Column(scale=2, elem_classes=["center-title"]):
                    title_comp = gr.Markdown(f"# {get_trans('title')}")
                    subtitle_comp = gr.Markdown(f"*{get_trans('subtitle')}*")
                with gr.Column(scale=1):
                    lang_label = gr.Markdown(
                        f"**{get_trans('interface_language')}:**"
                    )
                    lang_selector = gr.Radio(
                        choices=["🇬🇧", "🇫🇷"],
                        value="🇬🇧" if self.language == "en" else "🇫🇷",
                        show_label=False,
                        elem_classes=["flag-emoji"]
                    )

            # Define update function for reactive i18n
            def update_interface_language(selected_flag):
                # Map flags to language codes
                flag_to_lang = {"🇬🇧": "en", "🇫🇷": "fr"}
                new_language = flag_to_lang.get(selected_flag, "en")

                # Update instance language
                self.language = new_language
                self._save_language_preference(new_language)

                # Get new translations
                def get_new_trans(key: str) -> str:
                    return self.get_translation(key, new_language)

                # Return all updated components (excluding Tab, File, and
                # Dropdown components as they can't be updated this way)
                return [
                    f"# {get_new_trans('title')}",  # title_comp
                    f"*{get_new_trans('subtitle')}*",  # subtitle_comp
                    f"**{get_new_trans('interface_language')}:**",  # lang_label
                    get_new_trans('welcome_text'),  # welcome content
                    get_new_trans('start_transcription'),  # trans_button
                    f"## {get_new_trans('transcription_results')}",  # trans_results_label
                    get_new_trans('transcription_placeholder'),  # trans_output
                    f"## {get_new_trans('analysis_prompt')}",  # analysis_prompt_label
                    get_new_trans('prompt_default'),  # analysis_prompt value
                    get_new_trans('analyze_session'),  # analysis_button
                    f"## {get_new_trans('analysis_results')}",  # analysis_results_label
                    get_new_trans('analysis_placeholder'),  # analysis_output
                    get_new_trans('footer_text'),  # footer
                    get_new_trans('footer_info')
                ]

            # Tabs
            with gr.Tabs():
                # Welcome Tab
                with gr.Tab(get_trans('tab_welcome')):
                    welcome_content = gr.Markdown(get_trans('welcome_text'))

                # Transcription Tab
                with gr.Tab(get_trans('tab_transcription')):
                    audio_upload = gr.File(
                        label=get_trans('upload_audio'),
                        file_count="multiple",
                        file_types=["audio"]
                    )
                    with gr.Row():
                        trans_lang = gr.Dropdown(
                            label=get_trans('transcription_language'),
                            choices=["auto", "en", "fr", "es", "de"],
                            value="auto"
                        )
                        whisper_model = gr.Dropdown(
                            label=get_trans('whisper_model'),
                            choices=["tiny", "base", "small", "medium", "large"],
                            value="base"
                        )
                    trans_button = gr.Button(
                        get_trans('start_transcription'),
                        variant="primary"
                    )
                    trans_results_label = gr.Markdown(
                        f"## {get_trans('transcription_results')}"
                    )
                    trans_output = gr.Textbox(
                        label="",
                        placeholder=get_trans('transcription_placeholder'),
                        lines=10,
                        max_lines=15
                    )

                # AI Analysis Tab
                with gr.Tab(get_trans('tab_analysis')):
                    trans_file_upload = gr.File(
                        label=get_trans('upload_transcription'),
                        file_types=[".txt", ".json"]
                    )
                    analysis_prompt_label = gr.Markdown(
                        f"## {get_trans('analysis_prompt')}"
                    )
                    analysis_prompt = gr.Textbox(
                        label="",
                        placeholder=get_trans('prompt_placeholder'),
                        value=get_trans('prompt_default'),
                        lines=3
                    )
                    with gr.Row():
                        ai_provider = gr.Dropdown(
                            label=get_trans('ai_provider'),
                            choices=["openai", "ollama"],
                            value="openai"
                        )
                        ai_model = gr.Dropdown(
                            label=get_trans('model_label'),
                            choices=["gpt-4o-mini", "gpt-4", "llama3"],
                            value="gpt-4o-mini"
                        )
                    analysis_button = gr.Button(
                        get_trans('analyze_session'),
                        variant="primary"
                    )
                    analysis_results_label = gr.Markdown(
                        f"## {get_trans('analysis_results')}"
                    )
                    analysis_output = gr.Textbox(
                        label="",
                        placeholder=get_trans('analysis_placeholder'),
                        lines=15,
                        max_lines=20
                    )

            # Footer
            footer_text = gr.HTML(get_trans('footer_text'))
            footer_info = gr.HTML(f"<small>{get_trans('footer_info')}</small>")

            # Event handlers
            def process_transcription(files, lang, model):
                if not files:
                    return self.get_translation("upload_first")
                file_count = len(files)
                return (f"🎤 Transcription simulée avec {model} en {lang} "
                        f"pour {file_count} fichier(s)")

            def process_analysis(file, prompt, provider, model):
                if not file:
                    return self.get_translation("upload_transcription_first")
                prompt_preview = prompt[:100]
                return (f"🤖 Analyse IA simulée avec {provider} ({model})\n\n"
                        f"Prompt: {prompt_preview}...\n\n"
                        f"Fichier: {file.name if hasattr(file, 'name') else 'uploaded_file'}")

            # Event bindings
            trans_button.click(
                process_transcription,
                inputs=[audio_upload, trans_lang, whisper_model],
                outputs=trans_output
            )

            analysis_button.click(
                process_analysis,
                inputs=[trans_file_upload, analysis_prompt, ai_provider, ai_model],
                outputs=analysis_output
            )

            # Language change event - update all reactive components
            # (excluding Tab, File, and Dropdown components)
            lang_selector.change(
                update_interface_language,
                inputs=lang_selector,
                outputs=[
                    title_comp, subtitle_comp, lang_label,
                    welcome_content,
                    trans_button,
                    trans_results_label, trans_output,
                    analysis_prompt_label, analysis_prompt,
                    analysis_button,
                    analysis_results_label, analysis_output,
                    footer_text, footer_info
                ]
            )

            # Pre-warm WebSocket connection for performance
            def preload_connection():
                return None

            hidden_warmup = gr.HTML(visible=False)
            interface.load(preload_connection, outputs=hidden_warmup)

        return interface

    def run(self, server_name: str = "127.0.0.1",
            server_port: int = 7860, share: bool = False,
            debug: bool = False):
        """Launch the Gradio interface."""
        self.info(f"Starting Gradio interface with reactive i18n on "
                  f"{server_name}:{server_port}")

        interface = self.create_interface()
        interface.launch(
            server_name=server_name,
            server_port=server_port,
            share=share,
            debug=debug,
            show_error=True,
            quiet=False
        )


if __name__ == "__main__":
    agent = InterfaceAgent()
    agent.run()
