# TARA
### *Tabletop AI Recorder & Analyzer*

An intelligent system for transcribing and analyzing tabletop RPG sessions using AI. Perfect for Dungeon Masters and players who want to keep detailed records of their campaigns and improve their gameplay experience.

## 🎯 Overview

TARA is a comprehensive Python toolkit that transforms your recorded RPG sessions into detailed, structured session reports. The system includes:

1. **Audio Transcription**: Convert OGG audio recordings to text using Whisper AI
2. **Content Processing**: Clean, deduplicate, and merge multiple participant recordings
3. **AI Analysis**: Generate detailed session summaries and insights using OpenAI or Ollama
4. **Structured Output**: Produce formatted reports with session summaries, character analysis, and recommendations

## ✨ Features

### 🎤 Audio Transcription (`transcription.py`)
- **Multi-file Processing**: Batch transcribe all OGG files in a directory
- **Whisper Integration**: Uses faster-whisper for high-quality speech-to-text
- **Multiple Model Sizes**: Support for tiny, base, small, medium, and large-v3 models
- **GPU Acceleration**: CUDA support for faster processing
- **Language Detection**: Automatic language detection with manual override
- **Deduplication**: Remove duplicate segments from transcriptions
- **Timestamp Merging**: Merge multiple participant recordings by timeline

### 📝 Content Preparation (`prepare_prompt.py`)
- **Token Counting**: Accurate token estimation using OpenAI's tiktoken
- **Flexible Formatting**: Multiple timestamp formats (seconds, minutes, HMS)
- **Content Filtering**: Filter by username, time range, or other criteria
- **Export Options**: Text-only output or full analysis reports
- **Preview Mode**: Quick content preview before processing

### 🤖 AI Analysis (`ai_analyzer.py`)
- **Multiple AI Providers**: Support for OpenAI GPT models and Ollama local models
- **Structured Analysis**: Generate comprehensive session reports including:
  - Session summaries (800-1200 words)
  - Key events and discoveries
  - Character participation analysis
  - DM performance evaluation
  - Memorable moments
  - Actionable recommendations
- **Flexible Configuration**: Customizable system prompts and analysis parameters
- **Cost Tracking**: Token usage monitoring and cost estimation

## 🎲 Who Is TARA For?

**TARA** is perfect for:
- 🎭 **Dungeon Masters** who want detailed session records
- 📚 **Players** who want to remember campaign highlights
- 🎪 **Content Creators** making RPG podcasts/videos
- 📖 **Writers** developing RPG-inspired stories
- 🎯 **Game Researchers** studying player interactions

## 🚀 Quick Start

### Prerequisites

```bash
# Python 3.8+ required
pip install faster-whisper openai python-dotenv requests tiktoken
```

### Basic Usage

1. **Record your session** with multiple participants (OGG format recommended)

2. **Transcribe audio files**:
```bash
python transcription.py "path/to/audio/folder" --model large-v3 --language fr
```

3. **Prepare content for AI analysis**:
```bash
python prepare_prompt.py "cleaned/merged_transcription.json" --output analysis_results
```

4. **Generate AI analysis**:
```bash
python ai_analyzer.py analysis_results.txt system_prompt.txt --provider openai --model gpt-4o-mini
```

## 📁 Project Structure

```
rpg_session_minutes/
├── README.md                 # This file
├── LICENSE                   # MIT License
├── transcription.py          # Audio transcription toolkit
├── prepare_prompt.py         # Content preparation and token analysis
├── ai_analyzer.py           # AI analysis engine
├── requirements.txt          # Python dependencies
├── examples/
│   ├── system_prompt.txt    # Example AI prompt template
│   └── sample_workflow.md   # Complete workflow example
└── docs/
    ├── installation.md      # Detailed installation guide
    ├── configuration.md     # Configuration options
    └── api_reference.md     # API documentation
```

## 🔧 Configuration

### Environment Setup

Create a `.env` file for API keys:

```env
OPENAI_API_KEY=your_openai_api_key_here
```

### Audio Transcription Options

```bash
# Basic transcription with GPU acceleration
python transcription.py "audio_folder" --model large-v3 --device cuda

# Clean existing transcriptions only
python transcription.py "audio_folder" --clean-only --clean-suffix "_deduped"

# Merge transcriptions by timestamp
python transcription.py "audio_folder" --merge-only --merge-output "session_complete.json"
```

### AI Analysis Configuration

```bash
# OpenAI analysis with custom parameters
python ai_analyzer.py transcript.txt prompt.txt \
  --provider openai \
  --model gpt-4o-mini \
  --temperature 0.7 \
  --max-tokens 4000

# Local Ollama analysis
python ai_analyzer.py transcript.txt prompt.txt \
  --provider ollama \
  --model llama3.1:8b \
  --ollama-url http://localhost:11434
```

## 📋 Typical Workflow

### For D&D Sessions

1. **Setup Recording**: Each participant records their audio (Discord, OBS, etc.)
2. **File Organization**: Place all OGG files in a session folder
3. **Transcription**: Run batch transcription with French language detection
4. **Cleaning**: Remove duplicate segments from multiple recordings
5. **Merging**: Combine all participant transcripts by timestamp
6. **Analysis**: Generate structured session report using AI
7. **Review**: Share formatted markdown report with players

### Example Command Sequence

```bash
# Step 1: Transcribe all audio files
python transcription.py "Record_session/Session_01" --language fr --model large-v3

# Step 2: Clean and merge (interactive prompts will guide you)
# The script will ask if you want to clean and merge after transcription

# Step 3: Prepare for AI analysis
python prepare_prompt.py "Record_session/Session_01/merged_transcription.json" \
  --model gpt-4o-mini \
  --output session_01_analysis

# Step 4: Generate AI analysis
python ai_analyzer.py session_01_analysis.txt system_prompt.txt \
  --provider openai \
  --model gpt-4o-mini \
  --output session_01_report
```

## 🎮 RPG-Specific Features

### Supported Game Systems
- **Primary**: Dungeons & Dragons 5th Edition
- **Adaptable**: Any tabletop RPG with spoken sessions

### Analysis Categories
- **Session Summary**: Comprehensive narrative of events
- **Key Events**: Combat, discoveries, plot developments
- **Character Analysis**: Individual player contributions and roleplay
- **DM Performance**: Pacing, rule management, NPC portrayal
- **Memorable Moments**: Highlights and dramatic scenes
- **Recommendations**: Improvement suggestions and future hooks

### Campaign Continuity
- **Context Awareness**: AI considers previous session summaries
- **Character Tracking**: Monitors individual character development
- **Plot Thread Management**: Identifies ongoing storylines
- **NPC Relationship Tracking**: Notes important character interactions

## 🔍 Technical Details

### Audio Processing
- **Supported Formats**: OGG (primary), WAV, MP3, M4A
- **Model Options**: Whisper tiny (39MB) to large-v3 (1550MB)
- **Performance**: Large-v3 provides best accuracy for French D&D sessions
- **Hardware**: CUDA GPU recommended for large models

### AI Integration
- **OpenAI Models**: GPT-4o-mini (recommended), GPT-4, GPT-3.5-turbo
- **Local Models**: Ollama support for privacy-conscious users
- **Token Management**: Automatic counting and cost estimation
- **Error Handling**: Robust retry logic and fallback options

### Output Formats
- **JSON**: Machine-readable analysis data
- **Markdown**: Human-readable formatted reports
- **Text**: Plain text for further processing

## 🤝 Contributing

We welcome contributions! Areas where help is especially needed:

- **Language Support**: Additional language models and prompts
- **Game System Templates**: Prompts for other RPG systems
- **Audio Format Support**: Additional codec support
- **UI Development**: Enhanced TARA interface or desktop application
- **Documentation**: Examples and tutorials

## 🧪 Testing & Quality

### **Test Coverage: 89%** 🎯
- 62+ comprehensive tests covering all core functionality
- Automated test suite with pytest
- Coverage reporting with detailed analysis
- All linting errors resolved (PEP 8 compliant)

### **Run Tests**
```bash
# Install test dependencies
pip install -r requirements-test.txt

# Run all tests with coverage
python -m pytest tests/ -v --cov=interface_agent --cov-report=html

# View coverage report
open htmlcov/index.html
```

### **Test Structure**
```
tests/
├── test_interface_agent.py      # Core functionality tests
├── test_complete_coverage.py    # Edge case coverage
├── test_coverage_complete.py    # Additional scenarios
├── test_missing_lines.py        # Specific line coverage
├── conftest.py                   # Shared test configuration
└── __init__.py
```

See [TEST_REPORT.md](TEST_REPORT.md) for detailed testing analysis.

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- **OpenAI Whisper**: For excellent speech recognition capabilities
- **faster-whisper**: For optimized Whisper implementation
- **OpenAI API**: For powerful language model integration
- **Ollama**: For local AI model support
- **RPG Community**: For inspiration and feedback

## 📞 Support

- **Issues**: Report bugs and request features via GitHub Issues
- **Discussions**: Join community discussions for usage questions
- **Documentation**: Check the docs/ folder for detailed guides

---

*Transform your RPG sessions into lasting memories with automated transcription and AI-powered analysis.*
