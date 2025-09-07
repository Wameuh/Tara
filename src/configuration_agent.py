#!/usr/bin/env python3
"""
Configuration Agent

This agent handles centralized configuration management for all agents in the
system. It loads configuration from JSON files and provides structured access
to different agent configurations.
"""

import json
from pathlib import Path
from typing import Dict, Any, Optional, Union, List
from dataclasses import dataclass

try:
    from .agent import Agent
except ImportError:
    from agent import Agent


@dataclass
class AgentConfig:
    """Configuration container for a specific agent."""
    name: str
    config: Dict[str, Any]
    enabled: bool = True


class ConfigurationAgent(Agent):
    """
    Agent responsible for managing configurations for all agents in the system.

    This agent provides:
    - Centralized configuration loading from JSON files
    - Structured access to agent-specific configurations
    - Configuration validation and defaults
    - Environment-specific configuration support
    - Configuration hot-reloading capabilities
    """

    def __init__(self, config_path: Optional[Union[str, Path]] = None,
                 log_level: Optional[str] = None,
                 log_file: Optional[str] = None):
        """
        Initialize the Configuration Agent.

        Args:
            config_path (Union[str, Path], optional): Path to main
                configuration file. If None, looks for configuration.json in
                the same directory as this file.
            log_level (str, optional): Logging level. If None, uses default.
            log_file (str, optional): Path to log file. If None, uses
                default.
        """
        super().__init__(log_level or "INFO", log_file)

        # Set default config path
        if config_path is None:
            config_path = Path(__file__).parent / "configuration.json"
        else:
            config_path = Path(config_path)

        self.config_path = config_path
        self._config_cache = {}
        self._agent_configs = {}

        # Load initial configuration
        self._load_configuration()

    def _load_configuration(self) -> None:
        """Load configuration from the specified file."""
        try:
            if not self.config_path.exists():
                raise FileNotFoundError(
                    f"Configuration file not found: {self.config_path}"
                )

            with open(self.config_path, 'r', encoding='utf-8') as f:
                self._config_cache = json.load(f)

            self.logger.info(f"Configuration loaded from: {self.config_path}")
            self._process_agent_configs()

        except json.JSONDecodeError as e:
            self.logger.error(f"Invalid JSON in configuration file: {e}")
            raise ValueError(f"Invalid JSON in configuration file: {e}")
        except Exception as e:
            self.logger.error(f"Error loading configuration: {e}")
            raise RuntimeError(f"Error loading configuration: {e}")

    def _process_agent_configs(self) -> None:
        """Process and organize agent-specific configurations."""
        # Define agent configuration sections
        agent_sections = {
            'transcription': 'transcription',
            'analysis': 'analysis',
            'interface': 'interface',
            'logging': 'logging',
            'processing': 'processing',
            'output': 'output',
            'auto_processing': 'auto_processing'
        }

        for agent_name, section_key in agent_sections.items():
            if section_key in self._config_cache:
                self._agent_configs[agent_name] = AgentConfig(
                    name=agent_name,
                    config=self._config_cache[section_key],
                    enabled=self._config_cache[section_key].get(
                        'enabled', True
                    )
                )
                self.logger.debug(
                    f"Loaded configuration for {agent_name} agent"
                )
            else:
                self.logger.warning(
                    f"No configuration section found for {agent_name}"
                )

    def get_agent_config(self, agent_name: str) -> AgentConfig:
        """
        Get configuration for a specific agent.

        Args:
            agent_name (str): Name of the agent (e.g., 'transcription',
                'analysis')

        Returns:
            AgentConfig: Configuration object for the agent

        Raises:
            KeyError: If agent configuration not found
        """
        if agent_name not in self._agent_configs:
            raise KeyError(
                f"Configuration not found for agent: {agent_name}"
            )

        return self._agent_configs[agent_name]

    def get_agent_config_dict(self, agent_name: str) -> Dict[str, Any]:
        """
        Get configuration dictionary for a specific agent.

        Args:
            agent_name (str): Name of the agent

        Returns:
            Dict[str, Any]: Configuration dictionary for the agent
        """
        agent_config = self.get_agent_config(agent_name)
        return agent_config.config.copy()

    def get_transcription_config(self) -> Dict[str, Any]:
        """Get transcription agent configuration."""
        return self.get_agent_config_dict('transcription')

    def get_analysis_config(self) -> Dict[str, Any]:
        """Get analysis agent configuration."""
        return self.get_agent_config_dict('analysis')

    def get_interface_config(self) -> Dict[str, Any]:
        """Get interface agent configuration."""
        return self.get_agent_config_dict('interface')

    def get_logging_config(self) -> Dict[str, Any]:
        """Get logging configuration."""
        return self.get_agent_config_dict('logging')

    def get_processing_config(self) -> Dict[str, Any]:
        """Get processing configuration."""
        return self.get_agent_config_dict('processing')

    def get_output_config(self) -> Dict[str, Any]:
        """Get output configuration."""
        return self.get_agent_config_dict('output')

    def get_auto_processing_config(self) -> Dict[str, Any]:
        """Get auto-processing configuration."""
        return self.get_agent_config_dict('auto_processing')

    def get_global_config(self) -> Dict[str, Any]:
        """Get the complete global configuration."""
        return self._config_cache.copy()

    def is_agent_enabled(self, agent_name: str) -> bool:
        """
        Check if an agent is enabled.

        Args:
            agent_name (str): Name of the agent

        Returns:
            bool: True if agent is enabled, False otherwise
        """
        try:
            agent_config = self.get_agent_config(agent_name)
            return agent_config.enabled
        except KeyError:
            return False

    def get_available_agents(self) -> List[str]:
        """
        Get list of available agents.

        Returns:
            List[str]: List of agent names
        """
        return list(self._agent_configs.keys())

    def get_enabled_agents(self) -> List[str]:
        """
        Get list of enabled agents.

        Returns:
            List[str]: List of enabled agent names
        """
        return [name for name, config in self._agent_configs.items()
                if config.enabled]

    def reload_configuration(self) -> None:
        """Reload configuration from file."""
        self.logger.info("Reloading configuration...")
        self._load_configuration()
        self.logger.info("Configuration reloaded successfully")

    def update_agent_config(self, agent_name: str,
                            config_updates: Dict[str, Any]) -> None:
        """
        Update configuration for a specific agent.

        Args:
            agent_name (str): Name of the agent
            config_updates (Dict[str, Any]): Configuration updates to apply
        """
        if agent_name not in self._agent_configs:
            raise KeyError(
                f"Agent configuration not found: {agent_name}"
            )

        # Update the configuration
        current_config = self._agent_configs[agent_name].config
        current_config.update(config_updates)

        self.logger.info(
            f"Updated configuration for {agent_name} agent"
        )

    def save_configuration(
            self, output_path: Optional[Union[str, Path]] = None
    ) -> None:
        """
        Save current configuration to file.

        Args:
            output_path (Union[str, Path], optional): Path to save
                configuration. If None, saves to the original config file.
        """
        if output_path is None:
            output_path = self.config_path
        else:
            output_path = Path(output_path)

        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(self._config_cache, f, ensure_ascii=False, indent=2)

            self.logger.info(
                f"Configuration saved to: {output_path}"
            )
        except Exception as e:
            self.logger.error(f"Error saving configuration: {e}")
            raise

    def get_config_value(self, agent_name: str, key: str,
                         default: Any = None) -> Any:
        """
        Get a specific configuration value for an agent.

        Args:
            agent_name (str): Name of the agent
            key (str): Configuration key
            default (Any, optional): Default value if key not found

        Returns:
            Any: Configuration value or default
        """
        try:
            agent_config = self.get_agent_config(agent_name)
            return agent_config.config.get(key, default)
        except KeyError:
            return default

    def validate_configuration(self) -> Dict[str, List[str]]:
        """
        Validate the current configuration.

        Returns:
            Dict[str, List[str]]: Validation results with errors by section
        """
        errors = {}

        # Validate required sections
        required_sections = ['transcription', 'logging']
        for section in required_sections:
            if section not in self._agent_configs:
                errors.setdefault(section, []).append(
                    f"Missing required section: {section}"
                )

        # Validate transcription configuration
        if 'transcription' in self._agent_configs:
            trans_config = self._agent_configs['transcription'].config
            required_trans_keys = ['model_size', 'device', 'compute_type']
            for key in required_trans_keys:
                if key not in trans_config:
                    errors.setdefault('transcription', []).append(
                        f"Missing required key: {key}"
                    )

        # Validate logging configuration
        if 'logging' in self._agent_configs:
            log_config = self._agent_configs['logging'].config
            if 'level' not in log_config:
                errors.setdefault('logging', []).append(
                    "Missing required key: level"
                )

        return errors

    def get_agent_state(self) -> Dict[str, Any]:
        """Get configuration agent state."""
        return {
            "config_path": str(self.config_path),
            "available_agents": self.get_available_agents(),
            "enabled_agents": self.get_enabled_agents(),
            "config_loaded": bool(self._config_cache)
        }

    def set_agent_state(self, state: Dict[str, Any]):
        """Set configuration agent state."""
        # Configuration agent state is primarily read-only
        # This method is here for compatibility
        pass


def create_configuration_agent(
        config_path: Optional[Union[str, Path]] = None
) -> ConfigurationAgent:
    """
    Factory function to create a ConfigurationAgent instance.

    Args:
        config_path (Union[str, Path], optional): Path to configuration file

    Returns:
        ConfigurationAgent: Configured instance
    """
    return ConfigurationAgent(config_path=config_path)


def main():
    """Main function for testing the ConfigurationAgent."""
    import argparse

    parser = argparse.ArgumentParser(description="Configuration Agent Test")
    parser.add_argument("--config", help="Path to configuration file")
    parser.add_argument("--agent", help="Get config for specific agent")
    parser.add_argument("--validate", action="store_true",
                        help="Validate configuration")
    parser.add_argument("--list-agents", action="store_true",
                        help="List available agents")

    args = parser.parse_args()

    try:
        # Create configuration agent
        config_agent = ConfigurationAgent(config_path=args.config)

        if args.list_agents:
            print("Available agents:")
            for agent in config_agent.get_available_agents():
                enabled = "✓" if config_agent.is_agent_enabled(agent) else "✗"
                print(f"  {enabled} {agent}")

        elif args.agent:
            try:
                config = config_agent.get_agent_config_dict(args.agent)
                print(f"Configuration for {args.agent}:")
                print(json.dumps(config, indent=2))
            except KeyError:
                print(f"Agent '{args.agent}' not found")

        elif args.validate:
            errors = config_agent.validate_configuration()
            if errors:
                print("Configuration validation errors:")
                for section, section_errors in errors.items():
                    print(f"  {section}:")
                    for error in section_errors:
                        print(f"    - {error}")
            else:
                print("Configuration is valid")

        else:
            print("Configuration Agent loaded successfully")
            print(f"Available agents: {config_agent.get_available_agents()}")
            print(f"Enabled agents: {config_agent.get_enabled_agents()}")

    except Exception as e:
        print(f"Error: {e}")
        return 1

    return 0


if __name__ == "__main__":  # pragma: no cover
    exit(main())
