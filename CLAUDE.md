# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Axis-tool is a Python application with a GUI for controlling and monitoring motor axes in a beamline control system. It allows users to:

1. Move axes to absolute positions or relative distances
2. Monitor current axis positions in pulse or mm units
3. Save and load axis configurations
4. Create favorite groups of frequently used axes
5. Log axis positions with timestamps and comments

## Code Architecture

### Key Components

1. **Axis Class**: Represents a motor axis with properties like:
   - `axis_name`: Identifier for the axis
   - `display`: Human-readable label 
   - `val2pulse`: Conversion factor between mm and pulse
   - `sense`: Direction sense (1 or -1)
   - `unit`: "pulse" or "mm"

2. **Communication Functions**:
   - `fetch_state_and_position`: Gets current state and position from axis controller
   - `put_position`: Sends position command to axis controller
   - `put_stop`: Sends stop command to axis controller

3. **Configuration Management**:
   - `load_config`: Reads axis group configurations from YAML files
   - `parse_bss_config`: Parses axis data from beamline configuration
   - `load_all_configs`: Merges default and user configurations

4. **GUI Application (AxisToolApp)**:
   - Displays axes grouped by function
   - Provides controls for movement and monitoring
   - Supports saving favorites and logging positions

### Data Flow

1. Configuration files define groups of axes
2. BSS config provides hardware-specific parameters
3. GUI loads and displays these configurations
4. User interactions trigger socket communications to control hardware
5. Responses update UI and can be saved to log files

## Configuration Files

- `default_axis.yaml`: Default axis groups configuration
- `user_axis.yaml`: User-defined axis groups
- `/blconfig/bss/bss.config`: Hardware configuration (parsed for axis parameters)

## Running the Application

To run the application:

```bash
# Run with default configuration
python axis-tool.py

# Run with specific configuration file
python axis-tool.py your_config.yaml
```

## Development Notes

- The application uses TCP/IP socket communication to a controller at a fixed IP:PORT
- Log files are created with the naming pattern: `YYYYMMDD_groupname.yaml`
- When a favorite group is saved, it's written to `user_axis.yaml`
- The GUI dynamically updates for axes in motion with a yellow highlight

## Version Information

There are multiple versions of the tool in the repository:
- `axis-tool.py`: Latest version with full functionality
- `axis-tool-v0.1.py`: Earlier version with basic functionality
- `axis-tool-release.py` and `axis-tool-release_0.1.py`: Release versions

When making changes, ensure compatibility with the communication protocol and configuration file format.