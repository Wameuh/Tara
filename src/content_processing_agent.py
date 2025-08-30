#!/usr/bin/env python3
"""
Content Processing Agent

This agent handles content preparation and processing for AI analysis.
It provides functionality for token counting, formatting, filtering, and content optimization.
"""

from typing import List, Dict, Any, Optional, Union
from pathlib import Path
import json

from .agent import Agent


class ContentProcessingAgent(Agent):
    """
    Agent responsible for processing and preparing transcription content for AI analysis.

    This agent wraps the functionality from prepare_prompt.py and provides:
    - Token counting and estimation
    - Content formatting and filtering
    - Text preprocessing and optimization
    - Export in various formats
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None,
                 log_level: str = "INFO", log_file: Optional[str] = None):
        """
        Initialize the Content Processing Agent.

        Args:
            config (Dict[str, Any], optional): Configuration dictionary
            log_level (str): Logging level
            log_file (str, optional): Path to log file
        """
        # Default configuration for content processing
        default_config = {
            "model_name": "gpt-4o-mini",
            "include_timestamps": True,
            "include_usernames": True,
            "time_format": "seconds",  # "seconds", "minutes", "hms"
            "max_tokens": 100000,
            "encoding": "utf-8",
            "output_formats": ["json", "txt", "md"],
            "filter_empty_segments": True,
            "merge_consecutive_same_user": False
        }

        # Merge with provided config
        final_config = {**default_config, **(config or {})}

        super().__init__("ContentProcessing", final_config, log_level, log_file)

        # Initialize prompt preparer (will be imported when needed)
        self.prompt_preparer = None

    def _load_prompt_preparer(self):
        """Lazy load the prompt preparer to avoid import issues."""
        if self.prompt_preparer is None:
            try:
                # Import here to avoid circular imports and missing dependencies
                from ..prepare_prompt import PromptPreparer

                self.prompt_preparer = PromptPreparer(
                    model_name=self.get_config("model_name")
                )

                self.logger.info("PromptPreparer initialized successfully")

            except ImportError as e:
                self.handle_error(e, "loading PromptPreparer")

    def process(self, input_file: Union[str, Path],
                operation: str = "analyze", **kwargs) -> Dict[str, Any]:
        """
        Main processing method for content operations.

        Args:
            input_file (Union[str, Path]): Path to input transcription file
            operation (str): Type of operation ("analyze", "format", "filter", "export")
            **kwargs: Additional operation parameters

        Returns:
            Dict[str, Any]: Processing results
        """
        self.log_operation_start("content_processing", {
            "input_file": str(input_file),
            "operation": operation
        })

        try:
            input_file = Path(input_file)

            if not input_file.exists():
                raise FileNotFoundError(f"Input file not found: {input_file}")

            results = {}

            if operation in ["analyze", "full"]:
                results["analysis"] = self._analyze_content(input_file, **kwargs)

            if operation in ["format", "full"]:
                results["formatting"] = self._format_content(input_file, **kwargs)

            if operation in ["filter", "full"]:
                results["filtering"] = self._filter_content(input_file, **kwargs)

            if operation in ["export", "full"]:
                results["export"] = self._export_content(input_file, **kwargs)

            self.log_operation_end("content_processing", True, {
                "operations_completed": list(results.keys()),
                "input_file": str(input_file)
            })

            return results

        except Exception as e:
            self.log_operation_end("content_processing", False)
            self.handle_error(e, "content_processing")

    def _analyze_content(self, input_file: Path, **kwargs) -> Dict[str, Any]:
        """
        Analyze content and provide statistics.

        Args:
            input_file (Path): Path to input file
            **kwargs: Additional parameters

        Returns:
            Dict[str, Any]: Analysis results
        """
        self._load_prompt_preparer()

        self.logger.info(f"Analyzing content: {input_file}")

        # Get analysis options from kwargs and config
        options = {
            "username_filter": kwargs.get("username_filter"),
            "time_start": kwargs.get("time_start"),
            "time_end": kwargs.get("time_end"),
            "include_timestamps": kwargs.get("include_timestamps", self.get_config("include_timestamps")),
            "include_usernames": kwargs.get("include_usernames", self.get_config("include_usernames")),
            "time_format": kwargs.get("time_format", self.get_config("time_format"))
        }

        # Perform analysis
        results = self.prompt_preparer.analyze_file(str(input_file), **options)

        # Add our own analysis
        analysis_summary = {
            "file_path": str(input_file),
            "total_segments": results["total_segments"],
            "filtered_segments": results["filtered_segments"],
            "token_count": results["token_count"],
            "character_count": results["character_count"],
            "model_used": results["model_name"],
            "participants": results["merged_info"].get("usernames", []),
            "duration": results["merged_info"].get("total_duration", 0),
            "filters_applied": results["filters_applied"],
            "formatting_options": results["formatting_options"]
        }

        # Check if content exceeds token limits
        max_tokens = self.get_config("max_tokens")
        if results["token_count"] > max_tokens:
            analysis_summary["warning"] = f"Content exceeds max tokens ({max_tokens}). Consider filtering."

        return {
            "summary": analysis_summary,
            "full_analysis": results
        }

    def _format_content(self, input_file: Path, **kwargs) -> Dict[str, Any]:
        """
        Format content according to specifications.

        Args:
            input_file (Path): Path to input file
            **kwargs: Additional parameters

        Returns:
            Dict[str, Any]: Formatting results
        """
        self._load_prompt_preparer()

        self.logger.info(f"Formatting content: {input_file}")

        # Load the merged file
        data = self.prompt_preparer.load_merged_file(str(input_file))
        segments = data.get("segments", [])

        # Apply filters if specified
        filtered_segments = self.prompt_preparer.filter_segments(
            segments,
            username_filter=kwargs.get("username_filter"),
            time_start=kwargs.get("time_start"),
            time_end=kwargs.get("time_end")
        )

        # Apply content processing
        if self.get_config("filter_empty_segments"):
            filtered_segments = [s for s in filtered_segments if s.get("text", "").strip()]

        if self.get_config("merge_consecutive_same_user"):
            filtered_segments = self._merge_consecutive_segments(filtered_segments)

        # Format the text
        formatted_text = self.prompt_preparer.format_segments(
            filtered_segments,
            include_timestamps=kwargs.get("include_timestamps", self.get_config("include_timestamps")),
            include_usernames=kwargs.get("include_usernames", self.get_config("include_usernames")),
            time_format=kwargs.get("time_format", self.get_config("time_format"))
        )

        # Count tokens
        token_count = self.prompt_preparer.count_tokens(formatted_text)

        return {
            "original_segments": len(segments),
            "processed_segments": len(filtered_segments),
            "formatted_text": formatted_text,
            "token_count": token_count,
            "character_count": len(formatted_text),
            "processing_applied": {
                "filter_empty": self.get_config("filter_empty_segments"),
                "merge_consecutive": self.get_config("merge_consecutive_same_user")
            }
        }

    def _filter_content(self, input_file: Path, **kwargs) -> Dict[str, Any]:
        """
        Filter content based on criteria.

        Args:
            input_file (Path): Path to input file
            **kwargs: Additional parameters

        Returns:
            Dict[str, Any]: Filtering results
        """
        self._load_prompt_preparer()

        self.logger.info(f"Filtering content: {input_file}")

        # Load the merged file
        data = self.prompt_preparer.load_merged_file(str(input_file))
        segments = data.get("segments", [])

        # Apply various filters
        results = {}

        # Username filter
        if kwargs.get("username_filter"):
            username_filtered = self.prompt_preparer.filter_segments(
                segments, username_filter=kwargs["username_filter"]
            )
            results["username_filter"] = {
                "usernames": kwargs["username_filter"],
                "segments_kept": len(username_filtered),
                "segments_removed": len(segments) - len(username_filtered)
            }

        # Time range filter
        if kwargs.get("time_start") is not None or kwargs.get("time_end") is not None:
            time_filtered = self.prompt_preparer.filter_segments(
                segments,
                time_start=kwargs.get("time_start"),
                time_end=kwargs.get("time_end")
            )
            results["time_filter"] = {
                "time_range": f"{kwargs.get('time_start', 'start')} - {kwargs.get('time_end', 'end')}",
                "segments_kept": len(time_filtered),
                "segments_removed": len(segments) - len(time_filtered)
            }

        # Content filters
        if kwargs.get("min_duration"):
            duration_filtered = [
                s for s in segments
                if (s.get("end", 0) - s.get("start", 0)) >= kwargs["min_duration"]
            ]
            results["duration_filter"] = {
                "min_duration": kwargs["min_duration"],
                "segments_kept": len(duration_filtered),
                "segments_removed": len(segments) - len(duration_filtered)
            }

        # Text length filter
        if kwargs.get("min_text_length"):
            length_filtered = [
                s for s in segments
                if len(s.get("text", "").strip()) >= kwargs["min_text_length"]
            ]
            results["text_length_filter"] = {
                "min_length": kwargs["min_text_length"],
                "segments_kept": len(length_filtered),
                "segments_removed": len(segments) - len(length_filtered)
            }

        return {
            "original_segments": len(segments),
            "filters_applied": results
        }

    def _export_content(self, input_file: Path, **kwargs) -> Dict[str, Any]:
        """
        Export content in various formats.

        Args:
            input_file (Path): Path to input file
            **kwargs: Additional parameters

        Returns:
            Dict[str, Any]: Export results
        """
        self._load_prompt_preparer()

        output_path = kwargs.get("output_path", input_file.stem + "_processed")
        formats = kwargs.get("formats", self.get_config("output_formats"))

        self.logger.info(f"Exporting content to formats: {formats}")

        # Get formatted content
        format_result = self._format_content(input_file, **kwargs)
        formatted_text = format_result["formatted_text"]

        exported_files = []

        for format_type in formats:
            if format_type == "txt":
                txt_path = f"{output_path}.txt"
                with open(txt_path, 'w', encoding=self.get_config("encoding")) as f:
                    f.write(formatted_text)
                exported_files.append(txt_path)

            elif format_type == "json":
                json_path = f"{output_path}.json"
                export_data = {
                    "metadata": {
                        "source_file": str(input_file),
                        "export_timestamp": self.start_time.isoformat(),
                        "processing_config": self.config
                    },
                    "content": formatted_text,
                    "statistics": {
                        "token_count": format_result["token_count"],
                        "character_count": format_result["character_count"],
                        "segments_count": format_result["processed_segments"]
                    }
                }
                with open(json_path, 'w', encoding=self.get_config("encoding")) as f:
                    json.dump(export_data, f, indent=2, ensure_ascii=False)
                exported_files.append(json_path)

            elif format_type == "md":
                md_path = f"{output_path}.md"
                markdown_content = self._format_as_markdown(formatted_text, format_result)
                with open(md_path, 'w', encoding=self.get_config("encoding")) as f:
                    f.write(markdown_content)
                exported_files.append(md_path)

        return {
            "exported_files": exported_files,
            "formats": formats,
            "statistics": {
                "token_count": format_result["token_count"],
                "character_count": format_result["character_count"],
                "segments_count": format_result["processed_segments"]
            }
        }

    def _merge_consecutive_segments(self, segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Merge consecutive segments from the same user.

        Args:
            segments (List[Dict[str, Any]]): List of segments

        Returns:
            List[Dict[str, Any]]: Merged segments
        """
        if not segments:
            return segments

        merged = [segments[0].copy()]

        for segment in segments[1:]:
            last_segment = merged[-1]

            # Check if same user and close in time (within 2 seconds)
            if (segment.get("username") == last_segment.get("username") and
                segment.get("start", 0) - last_segment.get("end", 0) <= 2):

                # Merge segments
                last_segment["text"] += " " + segment.get("text", "")
                last_segment["end"] = segment.get("end", last_segment["end"])
            else:
                merged.append(segment.copy())

        return merged

    def _format_as_markdown(self, content: str, statistics: Dict[str, Any]) -> str:
        """
        Format content as markdown with metadata.

        Args:
            content (str): Formatted content
            statistics (Dict[str, Any]): Content statistics

        Returns:
            str: Markdown formatted content
        """
        markdown = f"""# RPG Session Transcription

## Statistics
- **Segments**: {statistics['processed_segments']}
- **Characters**: {statistics['character_count']:,}
- **Tokens**: {statistics['token_count']:,}
- **Processing**: {', '.join(f"{k}: {v}" for k, v in statistics.get('processing_applied', {}).items())}

## Content

{content}

---
*Generated by RPG Session Minutes - Content Processing Agent*
"""
        return markdown

    def estimate_tokens(self, text: str) -> int:
        """
        Estimate token count for given text.

        Args:
            text (str): Text to estimate

        Returns:
            int: Estimated token count
        """
        self._load_prompt_preparer()
        return self.prompt_preparer.count_tokens(text)

    def get_content_summary(self, input_file: Union[str, Path]) -> Dict[str, Any]:
        """
        Get a quick summary of content without full processing.

        Args:
            input_file (Union[str, Path]): Path to input file

        Returns:
            Dict[str, Any]: Content summary
        """
        self._load_prompt_preparer()

        try:
            data = self.prompt_preparer.load_merged_file(str(input_file))

            return {
                "file_path": str(input_file),
                "total_segments": len(data.get("segments", [])),
                "participants": data.get("merged_info", {}).get("usernames", []),
                "duration": data.get("merged_info", {}).get("total_duration", 0),
                "languages": data.get("merged_info", {}).get("languages", []),
                "merged_at": data.get("merged_info", {}).get("merged_at")
            }
        except Exception as e:
            self.handle_error(e, "get_content_summary", reraise=False)
            return {}

    def get_agent_state(self) -> Dict[str, Any]:
        """Get content processing agent specific state."""
        return {
            "prompt_preparer_loaded": self.prompt_preparer is not None,
            "model_name": self.get_config("model_name")
        }

    def set_agent_state(self, state: Dict[str, Any]):
        """Set content processing agent specific state."""
        if not state.get("prompt_preparer_loaded", False):
            self.prompt_preparer = None
