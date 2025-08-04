"""
Refactored Script Runner MCP Server.
Supports Python, Bash, PowerShell, and Node.js scripts.
"""

import argparse
import asyncio
import sys
from collections.abc import Callable
from enum import Enum
from pathlib import Path

from mcp.server.fastmcp import FastMCP


def register_tool(func: Callable) -> Callable:
    """Decorator for registering tools to be added to the MCP server."""
    ScriptRunner._tools.append(func)
    return func


def resolve_path(path: str) -> Path:
    """Expand and resolve a path."""
    return Path(path).expanduser().resolve()


class ScriptType(Enum):
    PYTHON = "py"
    BASH = "sh"
    POWERSHELL = "ps1"
    NODE = "js"
    UNKNOWN = ""

    def __str__(self) -> str:
        return self.name.title()

    @staticmethod
    def _read_first_line(script_file: Path) -> str:
        """Read the first line of the file."""
        with script_file.open("r") as file:
            return file.readline().strip()

    @classmethod
    def _supported_shebangs(cls) -> dict[str, "ScriptType"]:
        return {
            "#!/usr/bin/env python": ScriptType.PYTHON,
            "#!/usr/bin/python3": ScriptType.PYTHON,
            "#!/usr/bin/python": ScriptType.PYTHON,
            "#!/usr/bin/env -S uv run --script": ScriptType.PYTHON,
            "#!/bin/bash": ScriptType.BASH,
            "#!/bin/sh": ScriptType.BASH,
            "#!/usr/bin/env pwsh": ScriptType.POWERSHELL,
            "#!/usr/bin/env node": ScriptType.NODE,
        }

    @classmethod
    def _javascript_aliases(cls) -> set[str]:
        return {"ts", "jsx", "cjs", "mjs"}

    @classmethod
    def detect(cls, script_file: Path) -> "ScriptType":
        """Detect script type from file extension or shebang.

        Args:
            script_file (Path): The Path to the script.
        Returns:
            ScriptType: The type of script.
        """
        try:
            return ScriptType.from_suffix(script_file)
        except ValueError:
            return cls._detect_from_shebang(script_file)

    @classmethod
    def from_suffix(cls, file: Path) -> "ScriptType":
        suffix = file.suffix.lstrip(".")
        if suffix in cls._javascript_aliases():
            return cls.NODE
        try:
            return cls(suffix)
        except ValueError as e:
            raise ValueError(f"Unsupported script type: {file.suffix}") from e

    @classmethod
    def _detect_from_shebang(cls, script_file: Path) -> "ScriptType":
        """Check the script type based on the shebang line.

        Args:
            script_file (Path): The Path to the script.
        Returns:
            ScriptType: The type of script.
        """
        first_line = ""
        try:
            first_line = cls._read_first_line(script_file)
            return cls._supported_shebangs()[first_line]
        except (KeyError, OSError, UnicodeDecodeError) as e:
            if not first_line.startswith("#!"):
                return ScriptType.UNKNOWN
            script_type = first_line.strip().split(" ")[-1].split("/")[-1]
            raise ValueError(f"Unsupported script type: {script_type}") from e


class SandboxManager:
    """Handles Docker sandbox operations."""

    def __init__(
        self,
        image_name: str = "script-runner-sandbox",
        dockerfile_dir: str | None = None,
    ) -> None:
        """Initializes the Docker sandbox manager.

        Attributes:
            image_name (str): The name of the container image
        """
        self.image_name = image_name
        self.image_checked = False
        self.dockerfile_dir = self._resolve_dockerfile_dir(dockerfile_dir)

    @staticmethod
    def _check_build_result(build_result: str) -> None:
        if build_result.startswith("Error"):
            print(
                f"Failed to build sandbox image: {build_result}",
                file=sys.stderr,
            )
            sys.exit(1)
        return

    def _resolve_dockerfile_dir(self, dockerfile_dir: str | None) -> str:
        dockerfile_dir_path = (
            resolve_path(dockerfile_dir)
            if dockerfile_dir
            else Path(__file__).parent
        )
        return str(dockerfile_dir_path)

    async def _check_for_image(self) -> str:
        """Check if the sandbox Docker image exists."""
        inspect_command = ["docker", "inspect", self.image_name]
        return await ScriptRunner.execute_command(inspect_command)

    async def _build_image(self) -> None:
        build_command = [
            "docker",
            "build",
            "-t",
            self.image_name,
            self.dockerfile_dir,
        ]
        build_result = await ScriptRunner.execute_command(build_command)
        return self._check_build_result(build_result)

    async def ensure_image_exists(self) -> None:
        """Ensure the sandbox Docker image exists, build if necessary."""
        if self.image_checked:
            return

        inspect_result = await self._check_for_image()
        self.image_checked = True

        if inspect_result.startswith("Error"):
            await self._build_image()
        return

    def wrap_command_for_docker(self, script_dir: Path) -> list[str]:
        """Wrap a command to run in Docker sandbox.

        Args:
            script_dir: Path: The directory containing the script.

        Returns:
            list[str]: A list of commands to run the Docker container"""
        return [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{str(script_dir)}:/app",
            "-w",
            "/app",
            self.image_name,
        ]


class ScriptRunner:
    """Script Runner MCP Server."""

    _tools: list[Callable] = []
    SUPPORTED_EXTENSIONS = [
        ".py",
        ".sh",
        ".ps1",
        ".js",
        ".ts",
        ".jsx",
        ".cjs",
        ".mjs",
    ]

    def __init__(
        self, directory: Path, sandbox: bool, help_flag: str = "-h"
    ) -> None:
        """Initialize the MCP server.
        Methods are registered as tools with the @register_tool decorator
        and added to FastMCP Tools on initialization.

        Args:
            directory: Directory containing scripts to execute.
            sandbox: Whether to run scripts in a sandbox by default.
            help_flag: Flag to use for script help.
        """
        self._directory = directory
        self._sandbox = sandbox
        self._help_flag = help_flag

        self._win32 = sys.platform == "win32"

        self._mcp = FastMCP(
            "Script Runner",
            instructions=(
                "Use the 'call_help' tool to learn how to use an available "
                "script."
            ),
        )
        self._add_tools(self._tools)

    @staticmethod
    async def execute_command(command: list[str]) -> str:
        """Execute a command and return the result.

        Args:
            command (list): List of arguments sent as command to run.

        Returns:
            str: The output of the executed command.
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
                    f"\nScript exited with error code {process.returncode}"
                )

            return response
        except Exception as e:
            return f"Error executing script: {e}"

    def _add_tools(self, tools: list[Callable]) -> None:
        for tool in tools:
            self._mcp.add_tool(tool)

    def _resolve_directory(self, directory: str | None = None) -> Path:
        """Resolve the directory path to use."""
        return resolve_path(directory) if directory else self._directory

    def _find_script(
        self, script_name: str, directory: str | None = None
    ) -> Path:
        """Find a script file, trying different extensions if needed."""
        script_dir = self._resolve_directory(directory)
        exact_path = script_dir / script_name

        if exact_path.exists():
            return exact_path

        for ext in self.SUPPORTED_EXTENSIONS:
            script_path = script_dir / f"{script_name}{ext}"
            if script_path.exists():
                return script_path

        available = self.list_scripts(directory)
        raise FileNotFoundError(
            f"Script '{script_name}' not found in {script_dir}. "
            f"Available: {available}"
        )

    @staticmethod
    def _is_powershell(script_type: ScriptType) -> bool:
        return script_type == ScriptType.POWERSHELL

    def _is_powershell_help_request(
        self, args: list[str], script_type: ScriptType
    ) -> bool:
        """Check if this is a PowerShell help request."""
        return args == [self._help_flag] and self._is_powershell(script_type)

    def _build_powershell_help_command(
        self, script_path: Path, sandbox: bool
    ) -> list[str]:
        """Build PowerShell help command."""
        shell = "powershell" if self._win32 and not sandbox else "pwsh"
        return [shell, "help", str(script_path)]

    async def _build_command(
        self,
        script_name: str,
        args: list[str],
        directory: str | None,
        sandbox: bool,
    ) -> list[str]:
        """Build the complete execution command."""
        script_path = self._find_script(script_name, directory)
        script_type = ScriptType.detect(script_path)
        use_sandbox = sandbox or self._sandbox

        if self._is_powershell_help_request(args, script_type):
            command = self._build_powershell_help_command(
                script_path, use_sandbox
            )
        else:
            executor = self._get_executor(script_type, use_sandbox)
            command = executor + [str(script_path)] + args

        if not use_sandbox:
            return command

        manager = SandboxManager()
        await manager.ensure_image_exists()
        return manager.wrap_command_for_docker(script_path.parent) + command

    def _get_executor(
        self, script_type: ScriptType, sandbox: bool
    ) -> list[str]:
        """Get the base executor command for a script type."""
        match script_type:
            case ScriptType.POWERSHELL:
                return (
                    ["powershell", "-ExecutionPolicy", "Bypass", "-File"]
                    if self._win32 and not sandbox
                    else ["pwsh", "-File"]
                )
            case ScriptType.PYTHON:
                return ["uv", "run"]
            case ScriptType.BASH:
                if self._win32 and not sandbox:
                    raise ValueError(
                        "Bash scripts require sandbox mode on Windows."
                    )
                return ["bash"]
            case ScriptType.NODE:
                return ["npm", "run"]
            case _:
                raise ValueError(f"Unsupported script type: {script_type}")

    def _has_help_flag(self, script_name: str, directory: str | None) -> bool:
        script_path = self._find_script(script_name, directory)
        content = self.read_script(script_name, directory)
        return (
            self._help_flag in content
            or "-help" in content
            or self._is_powershell(ScriptType.detect(script_path))
            and ".PARAMETER" in content
        )

    @register_tool
    async def call_help(
        self,
        script_name: str,
        directory: str | None = None,
        sandbox: bool = False,
    ) -> str:
        """
        Call a script with the -h flag to get help information.
        This tool should be called first to understand how to use the script.

        Args:
            script_name: Name of the script to call.
            directory: Optional directory path. If not provided, uses the
                server's configured directory or the current directory.
            sandbox: Optional flag to determine if the script should be
                executed in a sandbox.

        Returns:
            Help output from the script (simulated)
        """
        try:
            if not self._has_help_flag(script_name, directory):
                return (
                    f"Error: Script {script_name} does not have help command."
                )
            command: list[str] = await self._build_command(
                script_name, [self._help_flag], directory, sandbox
            )
            return await self.execute_command(command)

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
        sandbox: bool = False,
    ) -> str:
        """
        Execute a script with the provided arguments.
        Call call_help first to understand the script's usage.

        Args:
            script_name: Name of the script to call.
            args: List of arguments to pass to the script.
            directory: Optional directory path. If not provided, uses the
                server's configured directory or the current directory.
            sandbox: Optional flag to determine if the script should be
                executed in a sandbox.

        Returns:
            Output from the script execution (simulated)
        """
        try:
            if not args:
                args = []
            command: list[str] = await self._build_command(
                script_name, args, directory, sandbox
            )
            return await self.execute_command(command)

        except (FileNotFoundError, ValueError) as e:
            return f"Error: {str(e)}"
        except Exception as e:
            return f"Error executing script: {str(e)}"

    @register_tool
    def list_scripts(self, directory: str | None = None) -> str:
        """
        List available scripts in the specified directory.

        Args:
            directory: Optional directory path. If not provided, uses the
                server's configured directory.

        Returns:
            List of available scripts in the directory.
        """
        dir_path: Path = self._resolve_directory(directory)
        scripts: set[str] = {
            f.name
            for f in dir_path.iterdir()
            if f.is_file() and f.suffix in self.SUPPORTED_EXTENSIONS
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
            script_path = self._find_script(script_name, directory)
            script_type = ScriptType.detect(script_path)
            return (
                f"Script '{script_name}' found in {directory}. "
                f"Script type: {str(script_type)}"
            )
        except (FileNotFoundError, ValueError) as e:
            return str(e)

    def run(self) -> None:
        """Run the MCP server."""
        self._mcp.run()


def main():
    """Main function to run the MCP server."""
    parser = argparse.ArgumentParser(description="Script Runner MCP Server")
    parser.add_argument(
        "--dir", "-d", help="Default directory containing scripts", default="."
    )
    parser.add_argument(
        "--sandbox",
        "-s",
        action="store_true",
        help="Run scripts in sandbox mode",
    )
    parser.add_argument(
        "--flag", "-f", default="-h", help="Flag to use for script help"
    )

    args = parser.parse_args()
    script_dir = resolve_path(args.dir)

    if not script_dir.exists() or not script_dir.is_dir():
        print(
            f"❌ Error: Directory '{script_dir}' does not exist.",
            file=sys.stderr,
        )
        sys.exit(1)

    runner = ScriptRunner(
        directory=script_dir, sandbox=args.sandbox, help_flag=args.flag
    )
    runner.run()


if __name__ == "__main__":
    main()
