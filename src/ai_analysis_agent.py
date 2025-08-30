#!/usr/bin/env python3
"""
AI Analysis Agent

This agent handles AI-powered analysis of RPG session transcriptions.
It provides functionality for generating structured reports using OpenAI or Ollama models.
"""

from typing import Dict, Any, Optional, Union
from pathlib import Path
import json
from datetime import datetime

from .agent import Agent


class AIAnalysisAgent(Agent):
    """
    Agent responsible for AI-powered analysis of RPG session transcriptions.

    This agent wraps the functionality from ai_analyzer.py and provides:
    - Multi-provider AI support (OpenAI, Ollama)
    - Structured session analysis
    - Customizable system prompts
    - Cost tracking and token management
    - Multiple output formats
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None,
                 log_level: str = "INFO", log_file: Optional[str] = None):
        """
        Initialize the AI Analysis Agent.

        Args:
            config (Dict[str, Any], optional): Configuration dictionary
            log_level (str): Logging level
            log_file (str, optional): Path to log file
        """
        # Default configuration for AI analysis
        default_config = {
            "provider": "openai",  # "openai" or "ollama"
            "model": "gpt-4o-mini",  # Model to use
            "api_key": None,  # Will load from environment
            "env_file": ".env",
            "ollama_url": "http://localhost:11434",
            "temperature": 0.7,
            "max_tokens": 4000,
            "default_system_prompt": "system_prompt.txt",
            "output_formats": ["json", "txt", "md"],
            "token_tracking": True,
            "cost_estimation": True
        }

        # Merge with provided config
        final_config = {**default_config, **(config or {})}

        super().__init__("AIAnalysis", final_config, log_level, log_file)

        # Initialize AI analyzer (will be imported when needed)
        self.ai_analyzer = None
        self.analysis_history = []
        self.total_tokens_used = 0
        self.total_cost_estimate = 0.0

    def _load_ai_analyzer(self):
        """Lazy load the AI analyzer to avoid import issues."""
        if self.ai_analyzer is None:
            try:
                # Import here to avoid circular imports and missing dependencies
                from ..ai_analyzer import AIAnalyzer

                self.ai_analyzer = AIAnalyzer(
                    api_key=self.get_config("api_key"),
                    model=self.get_config("model"),
                    env_file=self.get_config("env_file"),
                    provider=self.get_config("provider"),
                    ollama_url=self.get_config("ollama_url")
                )

                self.logger.info(f"AIAnalyzer initialized with {self.get_config('provider')} provider")

            except ImportError as e:
                self.handle_error(e, "loading AIAnalyzer")

    def process(self, analysis_file: Union[str, Path],
                system_prompt: Union[str, Path],
                operation: str = "analyze", **kwargs) -> Dict[str, Any]:
        """
        Main processing method for AI analysis operations.

        Args:
            analysis_file (Union[str, Path]): Path to transcription file to analyze
            system_prompt (Union[str, Path]): System prompt (text or file path)
            operation (str): Type of operation ("analyze", "preview", "batch")
            **kwargs: Additional operation parameters

        Returns:
            Dict[str, Any]: Analysis results
        """
        self.log_operation_start("ai_analysis", {
            "analysis_file": str(analysis_file),
            "system_prompt": str(system_prompt)[:100] + "..." if len(str(system_prompt)) > 100 else str(system_prompt),
            "operation": operation
        })

        try:
            results = {}

            if operation == "analyze":
                results = self._analyze_content(analysis_file, system_prompt, **kwargs)

            elif operation == "preview":
                results = self._preview_analysis(analysis_file, system_prompt, **kwargs)

            elif operation == "batch":
                results = self._batch_analyze(analysis_file, system_prompt, **kwargs)

            else:
                raise ValueError(f"Unknown operation: {operation}")

            self.log_operation_end("ai_analysis", True, {
                "operation": operation,
                "tokens_used": results.get("usage", {}).get("total_tokens", 0)
            })

            return results

        except Exception as e:
            self.log_operation_end("ai_analysis", False)
            self.handle_error(e, "ai_analysis")

    def _analyze_content(self, analysis_file: Union[str, Path],
                        system_prompt: Union[str, Path], **kwargs) -> Dict[str, Any]:
        """
        Perform AI analysis of content.

        Args:
            analysis_file (Union[str, Path]): Path to content file
            system_prompt (Union[str, Path]): System prompt
            **kwargs: Additional parameters

        Returns:
            Dict[str, Any]: Analysis results
        """
        self._load_ai_analyzer()

        analysis_file = Path(analysis_file)

        if not analysis_file.exists():
            raise FileNotFoundError(f"Analysis file not found: {analysis_file}")

        self.logger.info(f"Starting AI analysis of: {analysis_file}")

        # Get analysis parameters
        output_path = kwargs.get("output_path", f"{analysis_file.stem}_analysis_result")
        temperature = kwargs.get("temperature", self.get_config("temperature"))
        max_tokens = kwargs.get("max_tokens", self.get_config("max_tokens"))

        # Perform analysis using the analyzer
        result_file = self.ai_analyzer.analyze_file(
            analysis_file=str(analysis_file),
            system_prompt=str(system_prompt),
            output_path=output_path,
            temperature=temperature,
            max_tokens=max_tokens
        )

        # Load the result for processing
        with open(result_file, 'r', encoding='utf-8') as f:
            result_data = json.load(f)

        # Track tokens and costs
        if self.get_config("token_tracking"):
            self._track_usage(result_data)

        # Add to analysis history
        self.analysis_history.append({
            "timestamp": datetime.now().isoformat(),
            "analysis_file": str(analysis_file),
            "system_prompt": str(system_prompt),
            "result_file": result_file,
            "tokens_used": result_data.get("usage", {}).get("total_tokens", 0),
            "model": result_data.get("model", "unknown")
        })

        # Prepare response
        analysis_result = {
            "analysis_file": str(analysis_file),
            "system_prompt": str(system_prompt),
            "result_file": result_file,
            "model": result_data.get("model"),
            "provider": result_data.get("provider"),
            "usage": result_data.get("usage"),
            "response": result_data.get("response"),
            "timestamp": result_data.get("timestamp"),
            "cost_estimate": self._estimate_cost(result_data) if self.get_config("cost_estimation") else None
        }

        self.logger.info(f"Analysis completed. Tokens used: {result_data.get('usage', {}).get('total_tokens', 0)}")

        return analysis_result

    def _preview_analysis(self, analysis_file: Union[str, Path],
                         system_prompt: Union[str, Path], **kwargs) -> Dict[str, Any]:
        """
        Preview what would be sent to AI without making actual API call.

        Args:
            analysis_file (Union[str, Path]): Path to content file
            system_prompt (Union[str, Path]): System prompt
            **kwargs: Additional parameters

        Returns:
            Dict[str, Any]: Preview information
        """
        self._load_ai_analyzer()

        analysis_file = Path(analysis_file)

        if not analysis_file.exists():
            raise FileNotFoundError(f"Analysis file not found: {analysis_file}")

        self.logger.info(f"Previewing analysis for: {analysis_file}")

        # Load content
        user_content = self.ai_analyzer.load_analysis_file(str(analysis_file))
        system_prompt_content = self.ai_analyzer.load_system_prompt(str(system_prompt))

        # Estimate tokens (rough estimation)
        estimated_prompt_tokens = len(system_prompt_content) // 4
        estimated_content_tokens = len(user_content) // 4
        estimated_total_tokens = estimated_prompt_tokens + estimated_content_tokens

        # Prepare preview
        preview = {
            "analysis_file": str(analysis_file),
            "system_prompt": str(system_prompt),
            "content_length": len(user_content),
            "system_prompt_length": len(system_prompt_content),
            "estimated_tokens": {
                "prompt": estimated_prompt_tokens,
                "content": estimated_content_tokens,
                "total": estimated_total_tokens
            },
            "model": self.get_config("model"),
            "provider": self.get_config("provider"),
            "temperature": kwargs.get("temperature", self.get_config("temperature")),
            "max_tokens": kwargs.get("max_tokens", self.get_config("max_tokens")),
            "estimated_cost": self._estimate_cost_from_tokens(estimated_total_tokens),
            "content_preview": user_content[:500] + "..." if len(user_content) > 500 else user_content,
            "system_prompt_preview": system_prompt_content[:300] + "..." if len(system_prompt_content) > 300 else system_prompt_content
        }

        self.logger.info(f"Preview completed. Estimated tokens: {estimated_total_tokens}")

        return preview

    def _batch_analyze(self, analysis_files: List[Union[str, Path]],
                      system_prompt: Union[str, Path], **kwargs) -> Dict[str, Any]:
        """
        Perform batch analysis of multiple files.

        Args:
            analysis_files (List[Union[str, Path]]): List of content files
            system_prompt (Union[str, Path]): System prompt
            **kwargs: Additional parameters

        Returns:
            Dict[str, Any]: Batch analysis results
        """
        self.logger.info(f"Starting batch analysis of {len(analysis_files)} files")

        batch_results = []
        total_tokens = 0
        failed_analyses = []

        for i, analysis_file in enumerate(analysis_files, 1):
            try:
                self.logger.info(
                    f"Processing file {i}/{len(analysis_files)}: {analysis_file}"
                )

                # Individual analysis
                result = self._analyze_content(analysis_file, system_prompt, **kwargs)
                batch_results.append(result)

                # Track total tokens
                tokens_used = result.get("usage", {}).get("total_tokens", 0)
                total_tokens += tokens_used

            except Exception as e:
                self.logger.error(f"Failed to analyze {analysis_file}: {e}")
                failed_analyses.append({
                    "file": str(analysis_file),
                    "error": str(e)
                })

        return {
            "batch_summary": {
                "total_files": len(analysis_files),
                "successful": len(batch_results),
                "failed": len(failed_analyses),
                "total_tokens": total_tokens,
                "total_cost_estimate": self._estimate_cost_from_tokens(total_tokens)
            },
            "results": batch_results,
            "failed_analyses": failed_analyses
        }

    def _track_usage(self, result_data: Dict[str, Any]):
        """Track token usage and costs."""
        usage = result_data.get("usage", {})
        total_tokens = usage.get("total_tokens", 0)

        self.total_tokens_used += total_tokens

        if self.get_config("cost_estimation"):
            cost = self._estimate_cost(result_data)
            if cost:
                self.total_cost_estimate += cost

    def _estimate_cost(self, result_data: Dict[str, Any]) -> Optional[float]:
        """
        Estimate cost based on usage data.

        Args:
            result_data (Dict[str, Any]): API result data

        Returns:
            Optional[float]: Estimated cost in USD
        """
        provider = result_data.get("provider", "").lower()

        if provider == "openai":
            return self._estimate_openai_cost(result_data)
        elif provider == "ollama":
            # Ollama is typically free (local)
            return 0.0

        return None

    def _estimate_openai_cost(self, result_data: Dict[str, Any]) -> float:
        """
        Estimate OpenAI API cost.

        Args:
            result_data (Dict[str, Any]): OpenAI result data

        Returns:
            float: Estimated cost in USD
        """
        model = result_data.get("model", "").lower()
        usage = result_data.get("usage", {})

        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)

        # Pricing as of 2024 (per 1K tokens)
        pricing = {
            "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
            "gpt-4o": {"input": 0.005, "output": 0.015},
            "gpt-4": {"input": 0.03, "output": 0.06},
            "gpt-3.5-turbo": {"input": 0.001, "output": 0.002}
        }

        # Find matching pricing
        model_pricing = None
        for price_model, prices in pricing.items():
            if price_model in model:
                model_pricing = prices
                break

        if not model_pricing:
            # Default to gpt-4o-mini pricing
            model_pricing = pricing["gpt-4o-mini"]

        input_cost = (prompt_tokens / 1000) * model_pricing["input"]
        output_cost = (completion_tokens / 1000) * model_pricing["output"]

        return input_cost + output_cost

    def _estimate_cost_from_tokens(self, total_tokens: int) -> float:
        """
        Estimate cost from total tokens using current model.

        Args:
            total_tokens (int): Total token count

        Returns:
            float: Estimated cost
        """
        model = self.get_config("model", "").lower()

        # Rough estimation assuming 70% prompt, 30% completion
        prompt_tokens = int(total_tokens * 0.7)
        completion_tokens = int(total_tokens * 0.3)

        # Mock result data for cost estimation
        mock_result = {
            "provider": self.get_config("provider"),
            "model": model,
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens
            }
        }

        return self._estimate_cost(mock_result) or 0.0

    def get_usage_statistics(self) -> Dict[str, Any]:
        """
        Get usage statistics for this agent.

        Returns:
            Dict[str, Any]: Usage statistics
        """
        return {
            "total_analyses": len(self.analysis_history),
            "total_tokens_used": self.total_tokens_used,
            "total_cost_estimate": self.total_cost_estimate,
            "current_model": self.get_config("model"),
            "current_provider": self.get_config("provider"),
            "analysis_history": self.analysis_history[-10:]  # Last 10 analyses
        }

    def save_system_prompt(self, prompt: str, file_path: Union[str, Path]) -> bool:
        """
        Save a system prompt to file.

        Args:
            prompt (str): System prompt content
            file_path (Union[str, Path]): Path to save prompt

        Returns:
            bool: True if successful
        """
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(prompt)

            self.logger.info(f"System prompt saved to: {file_path}")
            return True

        except Exception as e:
            self.handle_error(e, "save_system_prompt", reraise=False)
            return False

    def get_agent_state(self) -> Dict[str, Any]:
        """Get AI analysis agent specific state."""
        return {
            "ai_analyzer_loaded": self.ai_analyzer is not None,
            "total_tokens_used": self.total_tokens_used,
            "total_cost_estimate": self.total_cost_estimate,
            "analysis_count": len(self.analysis_history),
            "current_model": self.get_config("model"),
            "current_provider": self.get_config("provider")
        }

    def set_agent_state(self, state: Dict[str, Any]):
        """Set AI analysis agent specific state."""
        self.total_tokens_used = state.get("total_tokens_used", 0)
        self.total_cost_estimate = state.get("total_cost_estimate", 0.0)
        analysis_count = state.get("analysis_count", 0)

        # Reset analysis history to match count (we don't save full history in state)
        self.analysis_history = [{"restored": True}] * analysis_count

        if not state.get("ai_analyzer_loaded", False):
            self.ai_analyzer = None
