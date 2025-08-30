#!/usr/bin/env python3
"""
Interface Agent

This agent provides a Gradio web interface for the RPG Session Minutes system.
It serves as the main entry point for users to interact with the application.
"""

from typing import Optional

try:
    import gradio as gr
except ImportError:
    print("Error: gradio is not installed. Please install it with:")
    print("pip install gradio")
    exit(1)

if __name__ == "__main__":
    from agent import Agent
else:
    from .agent import Agent


class InterfaceAgent(Agent):
    """
    Main interface agent that provides a Gradio web interface.

    This agent provides:
    - Local web interface using Gradio
    - User-friendly interface for RPG session processing
    - File upload and download capabilities
    - Real-time progress updates
    """

    def __init__(self, log_level: str = "INFO", log_file: Optional[str] = None, language: str = "en"):
        """
        Initialize the Interface Agent.

        Args:
            log_level (str): Logging level
            log_file (str, optional): Path to log file
            language (str): Interface language ("en" or "fr")
        """
        super().__init__(log_level, log_file)

        # Gradio app configuration
        self.app = None
        self.server_name = "127.0.0.1"
        self.server_port = 7860
        self.share = False
        self.language = language

        # Load translations
        self.translations = self._load_translations()

        self.info("Interface Agent initialized with Gradio support")

    def _load_translations(self):
        """Load translations for the interface."""
        translations = {
            "en": {
                "title": "🎲 RPG Session Minutes",
                "subtitle": "Automated transcription and analysis for tabletop RPG sessions",
                "welcome_title": "🎲 RPG Session Minutes",
                "welcome_text": """
Welcome to the RPG Session Minutes application! This tool helps you process and analyze your tabletop RPG sessions.

## Features:
- **🎤 Audio Transcription**: Convert recorded sessions to text
- **📝 Content Processing**: Clean and format transcriptions
- **🤖 AI Analysis**: Generate structured session reports
- **📊 Session Summaries**: Get insights and recommendations

## Getting Started:
1. Upload your session audio files
2. Configure transcription settings
3. Process and analyze your session
4. Download the generated reports

**Note**: This is currently a placeholder interface. Full functionality will be implemented in future updates.
                """,
                "tab_welcome": "Welcome",
                "tab_transcription": "🎤 Transcription",
                "tab_analysis": "🤖 AI Analysis",
                "tab_settings": "⚙️ Settings",
                "system_status": "🔧 System Status",
                "agent_status": "Agent Status",
                "refresh_status": "🔄 Refresh Status",
                "status_running": "Interface Agent: ✅ Running\nGradio Server: ✅ Active",
                "status_updated": "Interface Agent: ✅ Running\nGradio Server: ✅ Active\nOther Agents: 🔄 Not yet implemented",
                "transcription_title": "## Audio Transcription",
                "transcription_note": "*Coming Soon: Upload audio files and configure transcription settings*",
                "upload_audio": "Upload Audio Files",
                "whisper_model": "Whisper Model",
                "language_label": "Language",
                "start_transcription": "🎤 Start Transcription",
                "output_label": "Output",
                "transcription_placeholder": "Transcription results will appear here...",
                "upload_first": "⚠️ Please upload audio files first.",
                "transcription_started": "🔄 Transcription started with {model} model in {lang}. Processing {count} file(s)...",
                "analysis_title": "## AI-Powered Session Analysis",
                "analysis_note": "*Coming Soon: Generate structured reports from transcriptions*",
                "upload_transcription": "Upload Transcription File",
                "system_prompt": "System Prompt",
                "prompt_placeholder": "Enter your analysis prompt here...",
                "prompt_default": "Analyze this RPG session transcription and provide a structured summary.",
                "ai_provider": "AI Provider",
                "model_label": "Model",
                "analyze_session": "🤖 Analyze Session",
                "analysis_results": "Analysis Results",
                "analysis_placeholder": "AI analysis will appear here...",
                "analysis_started": "🔄 Starting AI analysis with {provider} ({model})...\n\nPrompt: {prompt}...",
                "upload_and_prompt": "⚠️ Please upload a transcription file and enter a prompt.",
                "settings_title": "## Application Settings",
                "output_settings": "### 🗂️ Output Settings",
                "output_directory": "Output Directory",
                "output_placeholder": "Path where results will be saved",
                "auto_cleanup": "Auto-cleanup temporary files",
                "logging_settings": "### 📝 Logging Settings",
                "log_level": "Log Level",
                "log_to_file": "Save logs to file",
                "save_settings": "💾 Save Settings",
                "status_label": "Status",
                "settings_loaded": "Settings loaded successfully",
                "settings_saved": "✅ Settings saved successfully!",
                "footer_text": "🚀 <strong>RPG Session Minutes</strong> - Transform your RPG sessions into lasting memories",
                "footer_info": "Powered by Whisper AI, OpenAI, and Gradio | Version 1.0.0"
            },
            "fr": {
                "title": "🎲 Comptes-Rendus de Sessions JdR",
                "subtitle": "Transcription et analyse automatisées pour sessions de jeu de rôle sur table",
                "welcome_title": "🎲 Comptes-Rendus de Sessions JdR",
                "welcome_text": """
Bienvenue dans l'application Comptes-Rendus de Sessions JdR ! Cet outil vous aide à traiter et analyser vos sessions de jeu de rôle sur table.

## Fonctionnalités :
- **🎤 Transcription Audio** : Convertir les sessions enregistrées en texte
- **📝 Traitement du Contenu** : Nettoyer et formater les transcriptions
- **🤖 Analyse IA** : Générer des rapports de session structurés
- **📊 Résumés de Session** : Obtenir des insights et recommandations

## Pour Commencer :
1. Téléversez vos fichiers audio de session
2. Configurez les paramètres de transcription
3. Traitez et analysez votre session
4. Téléchargez les rapports générés

**Note** : Il s'agit actuellement d'une interface de démonstration. Les fonctionnalités complètes seront implémentées dans les prochaines mises à jour.
                """,
                "tab_welcome": "Accueil",
                "tab_transcription": "🎤 Transcription",
                "tab_analysis": "🤖 Analyse IA",
                "tab_settings": "⚙️ Paramètres",
                "system_status": "🔧 État du Système",
                "agent_status": "État des Agents",
                "refresh_status": "🔄 Actualiser l'État",
                "status_running": "Agent Interface : ✅ En fonctionnement\nServeur Gradio : ✅ Actif",
                "status_updated": "Agent Interface : ✅ En fonctionnement\nServeur Gradio : ✅ Actif\nAutres Agents : 🔄 Pas encore implémentés",
                "transcription_title": "## Transcription Audio",
                "transcription_note": "*Bientôt Disponible : Téléversez des fichiers audio et configurez les paramètres de transcription*",
                "upload_audio": "Téléverser des Fichiers Audio",
                "whisper_model": "Modèle Whisper",
                "language_label": "Langue",
                "start_transcription": "🎤 Démarrer la Transcription",
                "output_label": "Sortie",
                "transcription_placeholder": "Les résultats de transcription apparaîtront ici...",
                "upload_first": "⚠️ Veuillez d'abord téléverser des fichiers audio.",
                "transcription_started": "🔄 Transcription démarrée avec le modèle {model} en {lang}. Traitement de {count} fichier(s)...",
                "analysis_title": "## Analyse de Session Alimentée par l'IA",
                "analysis_note": "*Bientôt Disponible : Générez des rapports structurés à partir des transcriptions*",
                "upload_transcription": "Téléverser un Fichier de Transcription",
                "system_prompt": "Prompt Système",
                "prompt_placeholder": "Entrez votre prompt d'analyse ici...",
                "prompt_default": "Analysez cette transcription de session JdR et fournissez un résumé structuré.",
                "ai_provider": "Fournisseur IA",
                "model_label": "Modèle",
                "analyze_session": "🤖 Analyser la Session",
                "analysis_results": "Résultats d'Analyse",
                "analysis_placeholder": "L'analyse IA apparaîtra ici...",
                "analysis_started": "🔄 Démarrage de l'analyse IA avec {provider} ({model})...\n\nPrompt : {prompt}...",
                "upload_and_prompt": "⚠️ Veuillez téléverser un fichier de transcription et entrer un prompt.",
                "settings_title": "## Paramètres de l'Application",
                "output_settings": "### 🗂️ Paramètres de Sortie",
                "output_directory": "Répertoire de Sortie",
                "output_placeholder": "Chemin où les résultats seront sauvegardés",
                "auto_cleanup": "Nettoyage automatique des fichiers temporaires",
                "logging_settings": "### 📝 Paramètres de Journalisation",
                "log_level": "Niveau de Log",
                "log_to_file": "Sauvegarder les logs dans un fichier",
                "save_settings": "💾 Sauvegarder les Paramètres",
                "status_label": "État",
                "settings_loaded": "Paramètres chargés avec succès",
                "settings_saved": "✅ Paramètres sauvegardés avec succès !",
                "footer_text": "🚀 <strong>Comptes-Rendus de Sessions JdR</strong> - Transformez vos sessions JdR en souvenirs durables",
                "footer_info": "Alimenté par Whisper AI, OpenAI, et Gradio | Version 1.0.0"
            }
        }
        return translations

    def t(self, key: str, **kwargs) -> str:
        """Get translated text for the current language."""
        text = self.translations.get(self.language, self.translations["en"]).get(key, key)
        if kwargs:
            text = text.format(**kwargs)
        return text

    def _create_gradio_interface(self):
        """Create the Gradio interface."""
        self.info("Creating Gradio interface")

                # Create the main interface
        with gr.Blocks(
            title=self.t("title"),
            theme=gr.themes.Soft(),
        ) as app:
            gr.HTML(f"""
            <div style="text-align: center; padding: 20px;">
                <h1>{self.t("title")}</h1>
                <p>{self.t("subtitle")}</p>
            </div>
            """)

            with gr.Tab(self.t("tab_welcome")):
                gr.Markdown(f"# {self.t('welcome_title')}\n{self.t('welcome_text')}")

                # Status section
                gr.Markdown(self.t("system_status"))
                status_output = gr.Textbox(
                    label=self.t("agent_status"),
                    value=self.t("status_running"),
                    interactive=False,
                    lines=3
                )

                def refresh_status():
                    return self.t("status_updated")

                refresh_btn = gr.Button(self.t("refresh_status"))
                refresh_btn.click(refresh_status, outputs=status_output)

                with gr.Tab(self.t("tab_transcription")):
                    gr.Markdown(self.t("transcription_title"))
                    gr.Markdown(self.t("transcription_note"))

                    # Placeholder components
                    audio_files = gr.File(
                        label=self.t("upload_audio"),
                        file_count="multiple",
                        file_types=[".ogg", ".wav", ".mp3", ".m4a"]
                    )

                with gr.Row():
                    model_choice = gr.Dropdown(
                        label=self.t("whisper_model"),
                        choices=["large-v3", "medium", "small", "base", "tiny"],
                        value="large-v3"
                    )
                    language = gr.Dropdown(
                        label=self.t("language_label"),
                        choices=["fr", "en", "es", "de", "it"],
                        value="fr"
                    )

                transcribe_btn = gr.Button(self.t("start_transcription"), variant="primary")

                output_text = gr.Textbox(
                    label=self.t("output_label"),
                    placeholder=self.t("transcription_placeholder"),
                    lines=5
                )

                def placeholder_transcription(files, model, lang):
                    if files:
                        return self.t("transcription_started", model=model, lang=lang, count=len(files))
                    return self.t("upload_first")

                transcribe_btn.click(
                    placeholder_transcription,
                    inputs=[audio_files, model_choice, language],
                    outputs=output_text
                )

                with gr.Tab(self.t("tab_analysis")):
                    gr.Markdown(self.t("analysis_title"))
                    gr.Markdown(self.t("analysis_note"))

                    transcription_file = gr.File(
                        label=self.t("upload_transcription"),
                        file_types=[".json", ".txt"]
                    )

                system_prompt = gr.Textbox(
                    label=self.t("system_prompt"),
                    placeholder=self.t("prompt_placeholder"),
                    lines=5,
                    value=self.t("prompt_default")
                )

                with gr.Row():
                    provider = gr.Radio(
                        label=self.t("ai_provider"),
                        choices=["OpenAI", "Ollama"],
                        value="OpenAI"
                    )
                    model = gr.Dropdown(
                        label=self.t("model_label"),
                        choices=["gpt-4o-mini", "gpt-4", "llama3.1:8b"],
                        value="gpt-4o-mini"
                    )

                analyze_btn = gr.Button(self.t("analyze_session"), variant="primary")

                analysis_output = gr.Textbox(
                    label=self.t("analysis_results"),
                    placeholder=self.t("analysis_placeholder"),
                    lines=10
                )

                def placeholder_analysis(file, prompt, ai_provider, ai_model):
                    if file and prompt:
                        return self.t("analysis_started", provider=ai_provider, model=ai_model, prompt=prompt[:100])
                    return self.t("upload_and_prompt")

                analyze_btn.click(
                    placeholder_analysis,
                    inputs=[transcription_file, system_prompt, provider, model],
                    outputs=analysis_output
                )

                with gr.Tab(self.t("tab_settings")):
                    gr.Markdown(self.t("settings_title"))

                with gr.Group():
                    gr.Markdown(self.t("output_settings"))
                    output_dir = gr.Textbox(
                        label=self.t("output_directory"),
                        value="./output",
                        placeholder=self.t("output_placeholder")
                    )

                    auto_cleanup = gr.Checkbox(
                        label=self.t("auto_cleanup"),
                        value=True
                    )

                with gr.Group():
                    gr.Markdown(self.t("logging_settings"))
                    log_level = gr.Dropdown(
                        label=self.t("log_level"),
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                        value="INFO"
                    )

                    log_to_file = gr.Checkbox(
                        label=self.t("log_to_file"),
                        value=False
                    )

                save_settings_btn = gr.Button(self.t("save_settings"))

                settings_status = gr.Textbox(
                    label=self.t("status_label"),
                    value=self.t("settings_loaded"),
                    interactive=False
                )

                def save_settings(output, cleanup, level, log_file):
                    self.info(f"Settings updated: output={output}, cleanup={cleanup}, log_level={level}, log_to_file={log_file}")
                    return self.t("settings_saved")

                save_settings_btn.click(
                    save_settings,
                    inputs=[output_dir, auto_cleanup, log_level, log_to_file],
                    outputs=settings_status
                )

            # Footer
            gr.HTML(f"""
            <div style="text-align: center; padding: 20px; margin-top: 40px; border-top: 1px solid #eee;">
                <p>{self.t("footer_text")}</p>
                <p style="font-size: 0.9em; color: #666;">
                    {self.t("footer_info")}
                </p>
            </div>
            """)

        return app

    def process(self, **kwargs):
        """Main process method - runs the Gradio application."""
        self.info("Starting Gradio application")

        # Create the interface
        self.app = self._create_gradio_interface()

        # Launch the app
        self.info(f"Launching Gradio app on {self.server_name}:{self.server_port}")

        try:
            self.app.launch(
                server_name=self.server_name,
                server_port=self.server_port,
                share=self.share,
                show_error=True,
                quiet=False
            )
        except Exception as e:
            self.error(f"Failed to launch Gradio app: {e}")
            raise

    def run(self, server_name: str = "127.0.0.1", server_port: int = 7860, share: bool = False, language: str = None):
        """
        Run the Gradio application with custom settings.

        Args:
            server_name (str): Server address to bind to
            server_port (int): Port to run the server on
            share (bool): Whether to create a public link
            language (str): Interface language ("en" or "fr"). If None, uses current language.
        """
        self.server_name = server_name
        self.server_port = server_port
        self.share = share

        # Update language if provided
        if language:
            self.language = language
            self.translations = self._load_translations()
            self.info(f"Interface language changed to: {language}")

        self.info(f"Configuring server: {server_name}:{server_port}, share={share}, language={self.language}")

        # Run the application
        self.process()

    def stop(self):
        """Stop the Gradio application."""
        if self.app:
            self.info("Stopping Gradio application")
            try:
                self.app.close()
                self.info("Gradio application stopped successfully")
            except Exception as e:
                self.error(f"Error stopping Gradio app: {e}")
        else:
            self.warning("No Gradio app running to stop")


if __name__ == "__main__":
    # Create and run the interface
    interface = InterfaceAgent(log_level="INFO")

    # Run with default settings (localhost:7860)
    interface.run()
