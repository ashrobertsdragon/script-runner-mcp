"""
Script Runner MCP Server.
Supports Python , Bash, and PowerShell scripts.
"""

import argparse
import asyncio
import sys
from collections.abc import Callable
from enum import Enum
from pathlib import Path

from mcp.server.fastmcp import FastMCP


def register_tool(func: Callable) -> Callable:
    """Decorator for registering tools to be added to the MCP server.
    To be used only by the ScriptRunner class.

    Args:
        func: Function to register as a tool.

    Returns:
        The registered function.
    """
    ScriptRunner._tools.append(func)
    return func


def resolve_path(path: str) -> Path:
    """Expand and resolve a path."""
    return Path(path).expanduser().resolve()


class ScriptType(Enum):
    PYTHON = "py"
    BASH = "sh"
    POWERSHELL = "ps1"

    @classmethod
    def from_suffix(cls, suffix: str) -> "ScriptType":
        try:
            return cls(suffix.lstrip("."))
        except ValueError as e:
            raise ValueError(f"Unsupported script type: {suffix}") from e

    def __str__(self) -> str:
        return str(self.name).title()


class ScriptRunner:
    """Script Runner MCP Server.

    Initializes a FastMCP server and registers tools for executing scripts. If
    a directory is not provided at runtime, one can be passed during a tool
    call, or the server will default to the current directory. If a script is
    not found, the server will return an error message. Supports Python, Bash,
    and PowerShell scripts.
    """

    _tools: list[Callable] = []

    extensions = [".py", ".sh", ".ps1"]

    def __init__(self, directory: Path) -> None:
        """Initialize the MCP server.
        Methods are registered as tools with the @register_tool decorator
        and added to FastMCP Tools on initialization.

        Args:
            directory: Directory containing scripts to execute.
        """
        self._directory = directory
        self._win32 = sys.platform == "win32"
        self._mcp = FastMCP(
            "Script Runner",
            instructions="Use the 'call_help' tool to learn how to use an available script.",
        )
        self._add_tools(self._tools)

    def _add_tools(self, tools: list[Callable]) -> None:
        for tool in tools:
            self._mcp.add_tool(tool)

    def _find_script(self, script_name: str, directory: str | None) -> Path:
        """
        Find a script file in the given directory, trying different extensions.

        Args:
            script_name: Name of the script file to find.
            directory: Directory to search for the script file.

        Returns:
            Path to the found script file.

        Raises:
            FileNotFoundError: If the script file is not found.
        """
        script_dir: Path = self._resolve_directory(directory)
        exact_path: Path = script_dir / script_name
        if exact_path.exists():
            return exact_path

        for ext in self.extensions:
            if self._win32 and ext == ".sh":
                continue

            script_path: Path = script_dir / f"{script_name}{ext}"
            if script_path.exists():
                return script_path

        raise FileNotFoundError(
            f"Script '{script_name}' not found in {directory}. "
            f"Available scripts: {self.list_scripts(directory)}"
        )

    async def _execute_command(self, command: list[str]) -> str:
        """
        Execute the script command and return the results.
        """

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await process.communicate()

            response = (
                (stdout or stderr).decode("utf-8", errors="replace").strip()
            )

            if process.returncode != 0:
                response += (
                    f"\n Script exited with error code {process.returncode}"
                )

            return response

        except Exception as e:
            return f"Error executing script: {e}"

    def _get_executor(self, suffix: str) -> list[str]:
        """
        Determine the appropriate executor command for a script based on its extension.

        Returns:
            list[str] The shell execution command
        """
        script_type = ScriptType.from_suffix(suffix)

        match script_type:
            case ScriptType.POWERSHELL:
                return (
                    [
                        "powershell",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                    ]
                    if self._win32
                    else ["pwsh", "-File"]
                )
            case ScriptType.PYTHON:
                return ["uv", "run"]
            case ScriptType.BASH:
                return ["bash"]
            case _:
                raise ValueError(f"Unsupported script type: {script_type}")

    def _is_pwsh_help(self, args: list[str], suffix: str) -> bool:
        """Determine if the command is for PowerShell help."""
        return (
            args == ["-h"]
            and ScriptType.from_suffix(suffix) == ScriptType.POWERSHELL
        )

    def _build_pwsh_help_command(self, script_path: Path) -> list[str]:
        """Build the PowerShell help command."""
        shell = "powershell" if self._win32 else "pwsh"
        return [shell, "help", str(script_path)]

    def _build_command(
        self, script_name: str, args: list[str], directory: str | None
    ) -> list[str]:
        script_path: Path = self._find_script(script_name, directory)
        suffix = script_path.suffix

        if self._is_pwsh_help(args, suffix):
            return self._build_pwsh_help_command(script_path)

        command_base: list[str] = self._get_executor(suffix)
        return command_base + [str(script_path)] + args

    def _resolve_directory(self, directory: str | None) -> Path:
        """Resolve the directory path to use."""
        return resolve_path(directory) if directory else self._directory

    @register_tool
    async def call_help(
        self, script_name: str, directory: str | None = None
    ) -> str:
        """
        Call a script with the -h flag to get help information.
        This tool should be called first to understand how to use the script.

        Args:
            script_name: Name of the script to call (with or without extension)
            directory: Optional directory path. If not provided, uses the server's configured directory.

        Returns:
            Help output from the script (simulated)
        """
        try:
            command: list[str] = self._build_command(
                script_name, ["-h"], directory
            )
            return await self._execute_command(command)

        except (FileNotFoundError, ValueError) as e:
            return f"Error: {str(e)}"
        except Exception as e:
            return f"Error calling script help: {str(e)}"

    @register_tool
    async def call_script(
        self,
        script_name: str,
        args: list[str] | None = None,
        directory: str | None = None,
    ) -> str:
        """
        Execute a script with the provided arguments.
        Call call_help first to understand the script's usage.

        Args:
            script_name: Name of the script to call (with or without extension)
            args: List of arguments to pass to the script (excluding -h)
            directory: Optional directory path. If not provided, uses the server's configured directory.

        Returns:
            Output from the script execution (simulated)
        """
        try:
            if not args:
                args = []
            command: list[str] = self._build_command(
                script_name, args, directory
            )
            return await self._execute_command(command)

        except (FileNotFoundError, ValueError) as e:
            return f"Error: {str(e)}"
        except Exception as e:
            return f"Error executing script: {str(e)}"

    @register_tool
    def list_scripts(self, directory: str | None = None) -> str:
        """
        List available scripts in the specified directory.

        Args:
            directory: Optional directory path. If not provided, uses the server's configured directory.

        Returns:
            List of available scripts in the directory.
        """
        dir_path: Path = self._resolve_directory(directory)
        scripts: set[str] = {
            f.name
            for f in dir_path.iterdir()
            if f.is_file() and f.suffix in self.extensions
        }
        return "\n".join(scripts)

    @register_tool
    def read_script(
        self, script_name: str, directory: str | None = None
    ) -> str:
        """Read the content of a script file.

        Args:
            script_name: Name of the script file to read.
            directory: Directory to search for the script file. Optional. If
                not provided, uses the server's configured directory or
                the current directory if no directory is configured.

        Returns:
            Content of the script file.
        """
        try:
            file = self._find_script(script_name, directory)
            return file.read_text()
        except (FileNotFoundError, ValueError) as e:
            return str(e)

    @register_tool
    def verify_script(
        self, script_name: str, directory: str | None = None
    ) -> str:
        """Verify a script exists in the given directory.

        Args:
            script_name: Name of the script file to find.
            directory: Directory to search for the script file. Optional. If
                not provided, uses the server's configured directory or
                the current directory if no directory is configured.

        Returns:
            str: Path to the found script file.
        """
        try:
            file = self._find_script(script_name, directory)
            script_type = ScriptType(file.suffix)
            return f"Script '{script_name}' found in {directory}. Script type: {str(script_type).title()}"
        except (FileNotFoundError, ValueError) as e:
            return str(e)

    def run(self) -> None:
        """Run the MCP server."""
        self._mcp.run()


def main():
    """Main function to run the MCP server."""

    parser = argparse.ArgumentParser(description="Script Runner MCP Server")
    parser.add_argument(
        "--directory",
        "-d",
        type=str,
        help="Default directory containing scripts to execute",
        default=".",
    )

    args = parser.parse_args()

    _script_dir = resolve_path(args.directory)
    if not _script_dir.exists() or not _script_dir.is_dir():
        print(
            f"❌ Error: Directory '{_script_dir}' does not exist.",
            file=sys.stderr,
        )
        sys.exit(1)

    runner = ScriptRunner(_script_dir)
    runner.run()


if __name__ == "__main__":
    main()
