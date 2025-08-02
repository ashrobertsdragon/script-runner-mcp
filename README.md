# Script Runner MCP Server

This MCP server runs local Bash, PowerShell, or Python scripts and returns the output. It also allows the LLM to explore the script's help if there is help text supported (pro-tip, document your scripts and create a print_help function that future you can call with a help flag when future you forgets what the script does exactly or what the arguments are), or to simply provide the full text of the script for the LLM context.  

## Features

*   **Execute Local Scripts:** Run scripts with `.py`, `.sh`, and `.ps1` extensions.
*   **Dynamic Script Discovery:** List all available scripts within a designated directory.
*   **Inspect Scripts:** Read the full source code of any script.
*   **Get Help:** Retrieve help text from scripts that support a `-h` flag.

## Installation

1. First, if you haven't already, install `uv`:
```curl -LsSf https://astral.sh/uv/install.sh | sh```
2. Clone the Github repo:
```git clone http://github.com/ashrobertsdragon/script-runner-mcp.git```

## Configuration

To use this server, you need to add its configuration to your MCP client (e.g., Claude Desktop, Cursor). The client will manage starting the server process.

Here is an example configuration for `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "ScriptRunner": {
      "command": "uv", # if uv is not in your PATH, you will need to provide the full path the the uv executabe
      "args": [
        "run",
        "--directory",
        "<repo cloned directory> # The full path to the cloned repo
        "script-runner-mcp",
        "-d",
        "/path/to/your/scripts"
      ]
    }
  }
}
```

Alternatively, you can have uv pull the MCP server directly from GitHub and avoid cloning:

```json
{
  "mcpServers": {
    "ScriptRunner": {
      "command": "uvx", # if uv is not in your PATH, you will need to provide the full path the the uvx executabe
      "args": [
        "--from",
        "http://github.com/ashrobertsdragon/script-runner-mcp",
        "-d",
        "/path/to/your/scripts"
      ]
    }
  }
}
```

If you do not provide a path to a scripts directory with the `-d` argument, the server will use the current working directory as a default.

## Available Tools

The server exposes the following tools:

*   `list_scripts(directory: str | None = None) -> str`: Lists available scripts in the configured or passed in directory.
*   `read_script(script_name: str, directory: str | None = None) -> str`: Reads the content of a specified script.
*   `call_help(script_name: str, directory: str | None = None) -> str`: Calls a script with the `-h` flag to get its help text.
*   `call_script(script_name: str, args: list[str] | None = None, directory: str | None = None) -> str`: Executes a script with the provided arguments.
*   `verify_script(script_name: str, directory: str | None = None) -> str`: Verifies that a script exists in the given or configured directory.


## License

This project is licensed under the MIT License.