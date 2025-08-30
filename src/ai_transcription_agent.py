#!/usr/bin/env python3
"""
AI Transcription Agent

This agent handles audio transcription using Whisper AI models.
It provides functionality for batch processing, deduplication, and merging of audio files.
"""

from typing import List, Dict, Any, Optional, Union
from pathlib import Path

from .agent import Agent


class AITranscriptionAgent(Agent):
    """
    Agent responsible for converting audio recordings to text transcriptions.

    This agent wraps the functionality from transcription.py and provides:
    - Batch audio file processing
    - Multiple Whisper model support
    - GPU acceleration
    - Deduplication of transcript segments
    - Timeline-based merging of multiple recordings
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None,
                 log_level: str = "INFO", log_file: Optional[str] = None):
        """
        Initialize the AI Transcription Agent.

        Args:
            config (Dict[str, Any], optional): Configuration dictionary
            log_level (str): Logging level
            log_file (str, optional): Path to log file
        """
        # Default configuration for transcription
        default_config = {
            "model_size": "large-v3",
            "device": "cuda",
            "compute_type": "float16",
            "language": "fr",
            "beam_size": 5,
            "load_model_on_init": True,
            "audio_formats": [".ogg", ".wav", ".mp3", ".m4a"],
            "output_format": "json",
            "clean_suffix": "_deduped",
            "merge_pattern": "*_transcription*.json"
        }

        # Merge with provided config
        final_config = {**default_config, **(config or {})}

        super().__init__("AITranscription", final_config, log_level, log_file)

        # Initialize transcriber (will be imported when needed)
        self.transcriber = None
        self._model_loaded = False

    def _load_transcriber(self):
        """Lazy load the transcriber to avoid import issues."""
        if self.transcriber is None:
            try:
                # Import here to avoid circular imports and missing dependencies
                from ..transcription import AudioTranscriber

                self.transcriber = AudioTranscriber(
                    folder_path=".",  # Will be overridden in operations
                    model_size=self.get_config("model_size"),
                    device=self.get_config("device"),
                    compute_type=self.get_config("compute_type"),
                    language=self.get_config("language"),
                    load_model=self.get_config("load_model_on_init")
                )

                self._model_loaded = True
                self.logger.info("AudioTranscriber initialized successfully")

            except ImportError as e:
                self.handle_error(e, "loading AudioTranscriber")

    def process(self, folder_path: Union[str, Path],
                operation: str = "full", **kwargs) -> Dict[str, Any]:
        """
        Main processing method for transcription operations.

        Args:
            folder_path (Union[str, Path]): Path to folder containing audio files
            operation (str): Type of operation ("transcribe", "clean", "merge", "full")
            **kwargs: Additional operation parameters

        Returns:
            Dict[str, Any]: Operation results
        """
        self.log_operation_start("transcription_process", {
            "folder_path": str(folder_path),
            "operation": operation
        })

        try:
            folder_path = Path(folder_path)

            if not folder_path.exists():
                raise FileNotFoundError(f"Folder not found: {folder_path}")

            results = {}

            if operation in ["transcribe", "full"]:
                results["transcription"] = self._transcribe_folder(folder_path, **kwargs)

            if operation in ["clean", "full"]:
                results["cleaning"] = self._clean_transcriptions(folder_path, **kwargs)

            if operation in ["merge", "full"]:
                results["merging"] = self._merge_transcriptions(folder_path, **kwargs)

            self.log_operation_end("transcription_process", True, {
                "operations_completed": list(results.keys()),
                "total_files_processed": sum(len(r.get("files", [])) for r in results.values() if isinstance(r, dict))
            })

            return results

        except Exception as e:
            self.log_operation_end("transcription_process", False)
            self.handle_error(e, "transcription_process")

    def _transcribe_folder(self, folder_path: Path, **kwargs) -> Dict[str, Any]:
        """
        Transcribe all audio files in a folder.

        Args:
            folder_path (Path): Path to folder containing audio files
            **kwargs: Additional parameters

        Returns:
            Dict[str, Any]: Transcription results
        """
        self._load_transcriber()

        # Update transcriber folder path
        self.transcriber.folder_path = folder_path

        # Get transcription parameters
        output_folder = kwargs.get("output_folder")
        language = kwargs.get("language", self.get_config("language"))
        beam_size = kwargs.get("beam_size", self.get_config("beam_size"))

        self.logger.info(f"Starting transcription of folder: {folder_path}")

        created_files = self.transcriber.process_folder(
            output_folder=output_folder,
            language=language,
            beam_size=beam_size
        )

        return {
            "folder_path": str(folder_path),
            "files": created_files,
            "count": len(created_files),
            "parameters": {
                "language": language,
                "beam_size": beam_size,
                "model_size": self.get_config("model_size")
            }
        }

    def _clean_transcriptions(self, folder_path: Path, **kwargs) -> Dict[str, Any]:
        """
        Clean transcription files by removing duplicates.

        Args:
            folder_path (Path): Path to folder containing transcription files
            **kwargs: Additional parameters

        Returns:
            Dict[str, Any]: Cleaning results
        """
        self._load_transcriber()

        # Update transcriber folder path
        self.transcriber.folder_path = folder_path

        # Get cleaning parameters
        output_folder = kwargs.get("output_folder")
        suffix = kwargs.get("suffix", self.get_config("clean_suffix"))

        self.logger.info(f"Starting cleaning of transcriptions in: {folder_path}")

        cleaned_files = self.transcriber.deduplicate_json_files(
            output_folder=output_folder,
            suffix=suffix
        )

        return {
            "folder_path": str(folder_path),
            "files": cleaned_files,
            "count": len(cleaned_files),
            "suffix": suffix
        }

    def _merge_transcriptions(self, folder_path: Path, **kwargs) -> Dict[str, Any]:
        """
        Merge transcription files by timestamp.

        Args:
            folder_path (Path): Path to folder containing transcription files
            **kwargs: Additional parameters

        Returns:
            Dict[str, Any]: Merging results
        """
        self._load_transcriber()

        # Update transcriber folder path
        self.transcriber.folder_path = folder_path

        # Get merging parameters
        output_filename = kwargs.get("output_filename", "merged_transcription.json")
        output_folder = kwargs.get("output_folder")
        pattern = kwargs.get("pattern", self.get_config("merge_pattern"))

        self.logger.info(f"Starting merging of transcriptions in: {folder_path}")

        merged_file = self.transcriber.merge_transcriptions(
            output_filename=output_filename,
            output_folder=output_folder,
            json_pattern=pattern
        )

        return {
            "folder_path": str(folder_path),
            "merged_file": merged_file,
            "output_filename": output_filename,
            "pattern": pattern
        }

    def transcribe_single_file(self, audio_path: Union[str, Path],
                              output_path: Optional[Union[str, Path]] = None,
                              **kwargs) -> Dict[str, Any]:
        """
        Transcribe a single audio file.

        Args:
            audio_path (Union[str, Path]): Path to audio file
            output_path (Union[str, Path], optional): Output path for transcription
            **kwargs: Additional parameters

        Returns:
            Dict[str, Any]: Transcription result
        """
        self.log_operation_start("single_file_transcription", {
            "audio_path": str(audio_path)
        })

        try:
            self._load_transcriber()

            audio_path = Path(audio_path)

            if not audio_path.exists():
                raise FileNotFoundError(f"Audio file not found: {audio_path}")

            # Get transcription parameters
            language = kwargs.get("language", self.get_config("language"))
            beam_size = kwargs.get("beam_size", self.get_config("beam_size"))

            # Perform transcription
            result = self.transcriber.transcribe_audio(
                str(audio_path),
                language=language,
                beam_size=beam_size
            )

            # Save if output path provided
            if output_path:
                self.transcriber.save_transcription(result, str(output_path))
                result["saved_to"] = str(output_path)

            self.log_operation_end("single_file_transcription", True, {
                "segments_count": len(result.get("segments", [])),
                "duration": result.get("duration", 0)
            })

            return result

        except Exception as e:
            self.log_operation_end("single_file_transcription", False)
            self.handle_error(e, "single_file_transcription")

    def get_supported_formats(self) -> List[str]:
        """
        Get list of supported audio formats.

        Returns:
            List[str]: List of supported file extensions
        """
        return self.get_config("audio_formats", [".ogg", ".wav", ".mp3", ".m4a"])

    def find_audio_files(self, folder_path: Union[str, Path]) -> List[Path]:
        """
        Find all supported audio files in a folder.

        Args:
            folder_path (Union[str, Path]): Path to search folder

        Returns:
            List[Path]: List of audio file paths
        """
        folder_path = Path(folder_path)
        supported_formats = self.get_supported_formats()

        audio_files = []
        for format_ext in supported_formats:
            audio_files.extend(folder_path.glob(f"*{format_ext}"))

        return sorted(audio_files)

    def get_model_info(self) -> Dict[str, Any]:
        """
        Get information about the loaded model.

        Returns:
            Dict[str, Any]: Model information
        """
        if self.transcriber:
            return self.transcriber.get_model_info()
        else:
            return {
                "model_size": self.get_config("model_size"),
                "device": self.get_config("device"),
                "compute_type": self.get_config("compute_type"),
                "language": self.get_config("language"),
                "loaded": self._model_loaded
            }

    def get_agent_state(self) -> Dict[str, Any]:
        """Get transcription agent specific state."""
        return {
            "model_loaded": self._model_loaded,
            "model_info": self.get_model_info()
        }

    def set_agent_state(self, state: Dict[str, Any]):
        """Set transcription agent specific state."""
        self._model_loaded = state.get("model_loaded", False)
