#!/usr/bin/env python3
"""
Unit tests for ConfigurationAgent.

This module contains comprehensive unit tests for the ConfigurationAgent class,
aiming for 90%+ code coverage.
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Dict, Any
from unittest.mock import MagicMock, mock_open, patch

import pytest

# Import the modules to test
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from configuration_agent import (  # noqa: E402
    AgentConfig,
    ConfigurationAgent,
    create_configuration_agent
)


class TestAgentConfig:
    """Test cases for AgentConfig dataclass."""

    def test_agent_config_creation(self):
        """Test AgentConfig creation with all parameters."""
        config = {"key": "value"}
        agent_config = AgentConfig(name="test", config=config, enabled=True)

        assert agent_config.name == "test"
        assert agent_config.config == config
        assert agent_config.enabled is True

    def test_agent_config_default_enabled(self):
        """Test AgentConfig creation with default enabled value."""
        config = {"key": "value"}
        agent_config = AgentConfig(name="test", config=config)

        assert agent_config.name == "test"
        assert agent_config.config == config
        assert agent_config.enabled is True  # Default value

    def test_agent_config_disabled(self):
        """Test AgentConfig creation with disabled state."""
        config = {"key": "value"}
        agent_config = AgentConfig(name="test", config=config, enabled=False)

        assert agent_config.name == "test"
        assert agent_config.config == config
        assert agent_config.enabled is False


class TestConfigurationAgent:
    """Test cases for ConfigurationAgent class."""

    @pytest.fixture
    def sample_config(self) -> Dict[str, Any]:
        """Sample configuration for testing."""
        return {
            "transcription": {
                "model_size": "large-v3",
                "device": "cuda",
                "compute_type": "float16",
                "language": "fr",
                "beam_size": 20,
                "load_model_on_init": True,
                "audio_formats": [".ogg", ".wav", ".mp3", ".m4a"],
                "output_format": "json",
                "clean_suffix": "_deduped",
                "merge_pattern": "*_transcription*.json"
            },
            "logging": {
                "level": "INFO",
                "format":
                    "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                "file": None
            },
            "processing": {
                "max_workers": 4,
                "chunk_size": 30,
                "overlap": 5
            },
            "output": {
                "create_timestamps": True,
                "include_confidence": True,
                "save_segments": True,
                "save_full_text": True
            },
            "auto_processing": {
                "create_output_folder": True,
                "folder_suffix": "_transcriptions",
                "auto_clean": True,
                "auto_merge": True,
                "merge_filename": "merged_transcription.json"
            }
        }

    @pytest.fixture
    def temp_config_file(self, sample_config: Dict[str, Any]) -> str:
        """Create a temporary configuration file for testing."""
        with tempfile.NamedTemporaryFile(
                mode='w', suffix='.json', delete=False
        ) as f:
            json.dump(sample_config, f, indent=2)
            return f.name

    @pytest.fixture
    def config_agent(self, temp_config_file: str) -> ConfigurationAgent:
        """Create a ConfigurationAgent instance for testing."""
        return ConfigurationAgent(config_path=temp_config_file)

    def test_init_with_default_config_path(self):
        """Test ConfigurationAgent initialization with default config path."""
        with patch('configuration_agent.Path') as mock_path:
            mock_path_instance = MagicMock()
            mock_path_instance.exists.return_value = True
            mock_path_instance.parent = Path("/test")
            mock_path_instance.__truediv__ = lambda self, other: Path(
                f"/test/{other}"
            )
            mock_path.return_value = mock_path_instance

            with patch('builtins.open', mock_open(
                    read_data='{"transcription": {}}'
            )):
                with patch.object(
                        ConfigurationAgent, '_process_agent_configs'
                ):
                    with patch.object(
                            ConfigurationAgent, '_load_configuration'
                    ):
                        agent = ConfigurationAgent()
                        # Test that the path is set correctly
                        assert agent.config_path == Path(
                            "/test/configuration.json"
                        )

    def test_init_with_custom_config_path(self, temp_config_file: str):
        """Test ConfigurationAgent initialization with custom config path."""
        agent = ConfigurationAgent(config_path=temp_config_file)
        assert agent.config_path == Path(temp_config_file)

    def test_init_with_log_level_and_file(
            self, temp_config_file: str
    ):
        """
        Test ConfigurationAgent initialization with custom log level
        and file.
        """
        agent = ConfigurationAgent(
            config_path=temp_config_file,
            log_level="DEBUG",
            log_file="/tmp/test.log"
        )
        assert agent.config_path == Path(temp_config_file)

    def test_load_configuration_success(self, temp_config_file: str):
        """Test successful configuration loading."""
        agent = ConfigurationAgent(config_path=temp_config_file)
        assert agent._config_cache is not None
        assert "transcription" in agent._config_cache
        assert "logging" in agent._config_cache

    def test_load_configuration_file_not_found(self):
        """Test configuration loading when file doesn't exist."""
        with pytest.raises(RuntimeError, match="Error loading configuration"):
            ConfigurationAgent(config_path="/nonexistent/file.json")

    def test_load_configuration_invalid_json(self):
        """Test configuration loading with invalid JSON."""
        with tempfile.NamedTemporaryFile(
                mode='w', suffix='.json', delete=False
        ) as f:
            f.write("invalid json content")
            f.flush()

            with pytest.raises(ValueError, match="Invalid JSON"):
                ConfigurationAgent(config_path=f.name)

            os.unlink(f.name)

    def test_load_configuration_general_error(self):
        """Test configuration loading with general error."""
        with patch('builtins.open', side_effect=PermissionError(
                "Permission denied"
        )):
            with pytest.raises(
                    RuntimeError, match="Error loading configuration"
            ):
                ConfigurationAgent(config_path="/some/path")

    def test_process_agent_configs(self, config_agent: ConfigurationAgent):
        """Test processing of agent configurations."""
        assert "transcription" in config_agent._agent_configs
        assert "logging" in config_agent._agent_configs
        assert "processing" in config_agent._agent_configs
        assert "output" in config_agent._agent_configs
        assert "auto_processing" in config_agent._agent_configs

    def test_get_agent_config_success(
            self, config_agent: ConfigurationAgent
    ):
        """Test getting agent configuration successfully."""
        agent_config = config_agent.get_agent_config("transcription")
        assert isinstance(agent_config, AgentConfig)
        assert agent_config.name == "transcription"
        assert "model_size" in agent_config.config

    def test_get_agent_config_not_found(
            self, config_agent: ConfigurationAgent
    ):
        """Test getting agent configuration when not found."""
        with pytest.raises(
                KeyError,
                match="Configuration not found for agent: nonexistent"
        ):
            config_agent.get_agent_config("nonexistent")

    def test_get_agent_config_dict(
            self, config_agent: ConfigurationAgent
    ):
        """Test getting agent configuration as dictionary."""
        config_dict = config_agent.get_agent_config_dict(
                "transcription"
        )
        assert isinstance(config_dict, dict)
        assert "model_size" in config_dict
        assert config_dict["model_size"] == "large-v3"

    def test_get_transcription_config(self, config_agent: ConfigurationAgent):
        """Test getting transcription configuration."""
        config = config_agent.get_transcription_config()
        assert "model_size" in config
        assert config["model_size"] == "large-v3"

    def test_get_analysis_config(self, config_agent: ConfigurationAgent):
        """Test getting analysis configuration when not present."""
        with pytest.raises(KeyError):
            config_agent.get_analysis_config()

    def test_get_interface_config(self, config_agent: ConfigurationAgent):
        """Test getting interface configuration when not present."""
        with pytest.raises(KeyError):
            config_agent.get_interface_config()

    def test_get_logging_config(self, config_agent: ConfigurationAgent):
        """Test getting logging configuration."""
        config = config_agent.get_logging_config()
        assert "level" in config
        assert config["level"] == "INFO"

    def test_get_processing_config(self, config_agent: ConfigurationAgent):
        """Test getting processing configuration."""
        config = config_agent.get_processing_config()
        assert "max_workers" in config
        assert config["max_workers"] == 4

    def test_get_output_config(self, config_agent: ConfigurationAgent):
        """Test getting output configuration."""
        config = config_agent.get_output_config()
        assert "create_timestamps" in config
        assert config["create_timestamps"] is True

    def test_get_auto_processing_config(
            self, config_agent: ConfigurationAgent
    ):
        """Test getting auto-processing configuration."""
        config = config_agent.get_auto_processing_config()
        assert "create_output_folder" in config
        assert config["create_output_folder"] is True

    def test_get_global_config(self, config_agent: ConfigurationAgent):
        """Test getting global configuration."""
        global_config = config_agent.get_global_config()
        assert isinstance(global_config, dict)
        assert "transcription" in global_config
        assert "logging" in global_config

    def test_is_agent_enabled_true(
            self, config_agent: ConfigurationAgent
    ):
        """Test checking if agent is enabled (True)."""
        assert config_agent.is_agent_enabled("transcription") is True

    def test_is_agent_enabled_false(self, config_agent: ConfigurationAgent):
        """Test checking if agent is enabled (False)."""
        # Add a disabled agent to the config
        config_agent._agent_configs["test_agent"] = AgentConfig(
                name="test_agent", config={}, enabled=False
        )
        assert config_agent.is_agent_enabled("test_agent") is False

    def test_is_agent_enabled_not_found(
            self, config_agent: ConfigurationAgent
    ):
        """Test checking if agent is enabled when agent not found."""
        assert config_agent.is_agent_enabled("nonexistent") is False

    def test_get_available_agents(self, config_agent: ConfigurationAgent):
        """Test getting list of available agents."""
        agents = config_agent.get_available_agents()
        assert isinstance(agents, list)
        assert "transcription" in agents
        assert "logging" in agents

    def test_get_enabled_agents(self, config_agent: ConfigurationAgent):
        """Test getting list of enabled agents."""
        enabled_agents = config_agent.get_enabled_agents()
        assert isinstance(enabled_agents, list)
        assert "transcription" in enabled_agents
        assert "logging" in enabled_agents

    def test_reload_configuration(self, config_agent: ConfigurationAgent):
        """Test reloading configuration."""
        original_config = config_agent._config_cache.copy()
        config_agent.reload_configuration()
        # Should still have the same config after reload
        assert config_agent._config_cache == original_config

    def test_update_agent_config_success(
            self, config_agent: ConfigurationAgent
    ):
        """Test updating agent configuration successfully."""
        original_beam_size = config_agent.get_config_value(
                "transcription", "beam_size"
        )
        config_agent.update_agent_config(
                "transcription", {"beam_size": 25}
        )
        new_beam_size = config_agent.get_config_value(
                "transcription", "beam_size"
        )
        assert new_beam_size == 25
        assert new_beam_size != original_beam_size

    def test_update_agent_config_not_found(
            self, config_agent: ConfigurationAgent
    ):
        """Test updating agent configuration when agent not found."""
        with pytest.raises(
                KeyError, match="Agent configuration not found: nonexistent"
        ):
            config_agent.update_agent_config("nonexistent", {"key": "value"})

    def test_save_configuration_default_path(
            self, config_agent: ConfigurationAgent
    ):
        """Test saving configuration to default path."""
        with patch('builtins.open', mock_open()) as mock_file:
            config_agent.save_configuration()
            mock_file.assert_called_once()

    def test_save_configuration_custom_path(
            self, config_agent: ConfigurationAgent
    ):
        """Test saving configuration to custom path."""
        custom_path = "/tmp/custom_config.json"
        with patch('builtins.open', mock_open()) as mock_file:
            config_agent.save_configuration(output_path=custom_path)
            mock_file.assert_called_once_with(
                    Path(custom_path), 'w', encoding='utf-8'
            )

    def test_save_configuration_error(self, config_agent: ConfigurationAgent):
        """Test saving configuration with error."""
        with patch('builtins.open', side_effect=PermissionError(
                "Permission denied"
        )):
            with pytest.raises(PermissionError):
                config_agent.save_configuration()

    def test_get_config_value_success(self, config_agent: ConfigurationAgent):
        """Test getting specific configuration value successfully."""
        model_size = config_agent.get_config_value(
                "transcription", "model_size"
        )
        assert model_size == "large-v3"

    def test_get_config_value_with_default(
            self, config_agent: ConfigurationAgent
    ):
        """Test getting specific configuration value with default."""
        value = config_agent.get_config_value(
                "transcription", "nonexistent_key", "default"
        )
        assert value == "default"

    def test_get_config_value_agent_not_found(
            self, config_agent: ConfigurationAgent
    ):
        """Test getting specific configuration value when agent not found."""
        value = config_agent.get_config_value("nonexistent", "key", "default")
        assert value == "default"

    def test_validate_configuration_valid(
            self, config_agent: ConfigurationAgent
    ):
        """Test configuration validation with valid configuration."""
        errors = config_agent.validate_configuration()
        assert errors == {}

    def test_validate_configuration_missing_required_section(self):
        """Test configuration validation with missing required section."""
        incomplete_config = {
            "logging": {"level": "INFO"}
            # Missing transcription section
        }

        with tempfile.NamedTemporaryFile(
                mode='w', suffix='.json', delete=False
        ) as f:
            json.dump(incomplete_config, f, indent=2)
            f.flush()

            agent = ConfigurationAgent(config_path=f.name)
            errors = agent.validate_configuration()

            assert "transcription" in errors
            assert "Missing required section: transcription" in errors[
                    "transcription"
            ]

            os.unlink(f.name)

    def test_validate_configuration_missing_required_keys(self):
        """Test configuration validation with missing required keys."""
        incomplete_config = {
            # Missing model_size, device, compute_type
            "transcription": {"language": "fr"},
            "logging": {"level": "INFO"}
        }

        with tempfile.NamedTemporaryFile(
                mode='w', suffix='.json', delete=False
        ) as f:
            json.dump(incomplete_config, f, indent=2)
            f.flush()

            agent = ConfigurationAgent(config_path=f.name)
            errors = agent.validate_configuration()

            assert "transcription" in errors
            assert "Missing required key: model_size" in errors[
                    "transcription"
            ]
            assert "Missing required key: device" in errors[
                    "transcription"
            ]
            assert "Missing required key: compute_type" in errors[
                    "transcription"
            ]

            os.unlink(f.name)

    def test_validate_configuration_missing_logging_level(self):
        """Test configuration validation with missing logging level."""
        incomplete_config = {
            "transcription": {
                "model_size": "large-v3",
                "device": "cuda",
                "compute_type": "float16"
            },
            "logging": {"format": "%(asctime)s"}  # Missing level
        }

        with tempfile.NamedTemporaryFile(
                mode='w', suffix='.json', delete=False
        ) as f:
            json.dump(incomplete_config, f, indent=2)
            f.flush()

            agent = ConfigurationAgent(config_path=f.name)
            errors = agent.validate_configuration()

            assert "logging" in errors
            assert "Missing required key: level" in errors["logging"]

            os.unlink(f.name)

    def test_get_agent_state(self, config_agent: ConfigurationAgent):
        """Test getting agent state."""
        state = config_agent.get_agent_state()
        assert isinstance(state, dict)
        assert "config_path" in state
        assert "available_agents" in state
        assert "enabled_agents" in state
        assert "config_loaded" in state
        assert state["config_loaded"] is True

    def test_set_agent_state(self, config_agent: ConfigurationAgent):
        """Test setting agent state (no-op for compatibility)."""
        # This method is a no-op for compatibility
        config_agent.set_agent_state({"test": "value"})
        # Should not raise any exceptions

    def test_agent_config_with_enabled_false(self):
        """Test agent configuration with enabled=False in config."""
        config_with_disabled = {
            "transcription": {
                "model_size": "large-v3",
                "device": "cuda",
                "compute_type": "float16",
                "enabled": False
            },
            "logging": {"level": "INFO"}
        }

        with tempfile.NamedTemporaryFile(
                mode='w', suffix='.json', delete=False
        ) as f:
            json.dump(config_with_disabled, f, indent=2)
            f.flush()

            agent = ConfigurationAgent(config_path=f.name)
            test_agent_config = agent.get_agent_config("transcription")
            assert test_agent_config.enabled is False

            os.unlink(f.name)


class TestCreateConfigurationAgent:
    """Test cases for create_configuration_agent factory function."""

    def test_create_configuration_agent_default(self):
        """Test creating configuration agent with default parameters."""
        with patch('configuration_agent.ConfigurationAgent') as mock_class:
            mock_instance = MagicMock()
            mock_class.return_value = mock_instance

            result = create_configuration_agent()

            mock_class.assert_called_once_with(config_path=None)
            assert result == mock_instance

    def test_create_configuration_agent_with_path(self):
        """Test creating configuration agent with custom path."""
        with patch('configuration_agent.ConfigurationAgent') as mock_class:
            mock_instance = MagicMock()
            mock_class.return_value = mock_instance

            result = create_configuration_agent(
                    config_path="/custom/path.json"
            )

            mock_class.assert_called_once_with(
                    config_path="/custom/path.json"
            )
            assert result == mock_instance


class TestConfigurationAgentMain:
    """Test cases for ConfigurationAgent main function."""

    def test_main_list_agents(self):
        """Test main function with --list-agents argument."""
        with patch('sys.argv', [
                'configuration_agent.py', '--list-agents'
        ]):
            with patch('configuration_agent.ConfigurationAgent') as mock_class:
                mock_instance = MagicMock()
                mock_instance.get_available_agents.return_value = [
                        'transcription', 'logging'
                ]
                mock_instance.is_agent_enabled.return_value = True
                mock_class.return_value = mock_instance

                from configuration_agent import main
                result = main()
                assert result == 0

    def test_main_get_agent_config(self):
        """Test main function with --agent argument."""
        with patch('sys.argv', [
                'configuration_agent.py', '--agent', 'transcription'
        ]):
            with patch('configuration_agent.ConfigurationAgent') as mock_class:
                mock_instance = MagicMock()
                mock_instance.get_agent_config_dict.return_value = {
                        "key": "value"
                }
                mock_class.return_value = mock_instance

                from configuration_agent import main
                result = main()
                assert result == 0

    def test_main_get_agent_config_not_found(self):
        """Test main function with --agent argument for non-existent agent."""
        with patch('sys.argv', [
                'configuration_agent.py', '--agent', 'nonexistent'
        ]):
            with patch('configuration_agent.ConfigurationAgent') as mock_class:
                mock_instance = MagicMock()
                mock_instance.get_agent_config_dict.side_effect = KeyError(
                        "Not found"
                )
                mock_class.return_value = mock_instance

                from configuration_agent import main
                result = main()
                assert result == 0

    def test_main_validate_config(self):
        """Test main function with --validate argument."""
        with patch('sys.argv', [
                'configuration_agent.py', '--validate'
        ]):
            with patch('configuration_agent.ConfigurationAgent') as mock_class:
                mock_instance = MagicMock()
                mock_instance.validate_configuration.return_value = {}
                mock_class.return_value = mock_instance

                from configuration_agent import main
                result = main()
                assert result == 0

    def test_main_validate_config_with_errors(self):
        """Test main function with --validate argument showing errors."""
        with patch('sys.argv', [
                'configuration_agent.py', '--validate'
        ]):
            with patch('configuration_agent.ConfigurationAgent') as mock_class:
                mock_instance = MagicMock()
                mock_instance.validate_configuration.return_value = {
                    "transcription": ["Missing required key: model_size"]
                }
                mock_class.return_value = mock_instance

                from configuration_agent import main
                result = main()
                assert result == 0

    def test_main_default_behavior(self):
        """Test main function with no arguments."""
        with patch('sys.argv', ['configuration_agent.py']):
            with patch('configuration_agent.ConfigurationAgent') as mock_class:
                mock_instance = MagicMock()
                mock_instance.get_available_agents.return_value = [
                        'transcription'
                ]
                mock_instance.get_enabled_agents.return_value = [
                        'transcription'
                ]
                mock_class.return_value = mock_instance

                from configuration_agent import main
                result = main()
                assert result == 0

    def test_main_with_config_file(self):
        """Test main function with --config argument."""
        with patch('sys.argv', [
                'configuration_agent.py', '--config', '/tmp/test.json'
        ]):
            with patch('configuration_agent.ConfigurationAgent') as mock_class:
                mock_instance = MagicMock()
                mock_instance.get_available_agents.return_value = [
                        'transcription'
                ]
                mock_instance.get_enabled_agents.return_value = [
                        'transcription'
                ]
                mock_class.return_value = mock_instance

                from configuration_agent import main
                result = main()
                assert result == 0

    def test_main_exception_handling(self):
        """Test main function exception handling."""
        with patch('sys.argv', ['configuration_agent.py']):
            with patch(
                    'configuration_agent.ConfigurationAgent',
                    side_effect=Exception("Test error")
            ):
                from configuration_agent import main
                result = main()
                assert result == 1


class TestConfigurationAgentEdgeCases:
    """Test cases for edge cases and error conditions."""

    def test_empty_configuration_file(self):
        """Test handling of empty configuration file."""
        with tempfile.NamedTemporaryFile(
                mode='w', suffix='.json', delete=False
        ) as f:
            json.dump({}, f, indent=2)
            f.flush()

            agent = ConfigurationAgent(config_path=f.name)
            # Should handle empty config gracefully
            assert agent._config_cache == {}
            assert agent.get_available_agents() == []

            os.unlink(f.name)

    def test_configuration_with_unicode(self):
        """Test configuration with unicode characters."""
        unicode_config = {
            "transcription": {
                "language": "français",
                "description":
                    "Transcription en français avec caractères spéciaux"
            }
        }

        with tempfile.NamedTemporaryFile(
                mode='w', suffix='.json', delete=False, encoding='utf-8'
        ) as f:
            json.dump(
                    unicode_config, f, ensure_ascii=False, indent=2
            )
            f.flush()

            agent = ConfigurationAgent(config_path=f.name)
            assert agent._config_cache[
                                    "transcription"]["language"] == "français"

            os.unlink(f.name)

    def test_large_configuration_file(self):
        """Test handling of large configuration file."""
        large_config = {
            "transcription": {
                "model_size": "large-v3",
                "device": "cuda",
                "compute_type": "float16"
            },
            "logging": {"level": "INFO"},
            "processing": {"max_workers": 4},
            "output": {"create_timestamps": True},
            "auto_processing": {"create_output_folder": True}
        }

        # Add many additional sections that will be processed
        for i in range(10):
            large_config[f"section_{i}"] = {
                "key1": f"value1_{i}",
                "key2": f"value2_{i}",
                "enabled": True
            }

        with tempfile.NamedTemporaryFile(
                mode='w', suffix='.json', delete=False
        ) as f:
            json.dump(large_config, f, indent=2)
            f.flush()

            agent = ConfigurationAgent(config_path=f.name)
            # Only the predefined sections are processed, not the custom ones

            assert len(agent.get_available_agents()) == 5
            # transcription, logging, processing, output, auto_processing

            os.unlink(f.name)

    def test_configuration_with_nested_structures(self):
        """Test configuration with deeply nested structures."""
        nested_config = {
            "transcription": {
                "model_size": "large-v3",
                "advanced_settings": {
                    "beam_search": {
                        "beam_size": 20,
                        "length_penalty": 1.0,
                        "early_stopping": True
                    },
                    "decoding": {
                        "temperature": 0.0,
                        "top_p": 1.0,
                        "top_k": 50
                    }
                }
            },
            "logging": {"level": "INFO"}
        }

        with tempfile.NamedTemporaryFile(
                mode='w', suffix='.json', delete=False
        ) as f:
            json.dump(nested_config, f, indent=2)
            f.flush()

            agent = ConfigurationAgent(config_path=f.name)
            trans_config = agent.get_transcription_config()
            assert "advanced_settings" in trans_config
            assert "beam_search" in trans_config["advanced_settings"]
            assert trans_config["advanced_settings"]["beam_search"][
                    "beam_size"
            ] == 20

            os.unlink(f.name)


if __name__ == "__main__":
    pytest.main([
        __file__, "-v", "--cov=configuration_agent",
        "--cov-report=term-missing"
    ])
