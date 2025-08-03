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
    NODE = "js"

    @classmethod
    def _javascript_aliases(cls) -> set[str]:
        return {"ts", "jsx", "cjs", "mjs"}

    @classmethod
    def from_suffix(cls, file: Path) -> "ScriptType":
        suffix = file.suffix.lstrip(".")
        if suffix in cls._javascript_aliases():
            return cls.NODE
        try:
            return cls(suffix)
        except ValueError as e:
            raise ValueError(f"Unsupported script type: {file.suffix}") from e


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

    def __init__(self, directory: Path, sandbox: bool) -> None:
        """Initialize the MCP server.
        Methods are registered as tools with the @register_tool decorator
        and added to FastMCP Tools on initialization.

        Args:
            directory: Directory containing scripts to execute.
            sandbox: Whether to run scripts in a sandbox by default.
        """
        self._directory = directory
        self._sandbox = sandbox

        self._sandbox_image_name = "script-runner-sandbox"
        self._sandbox_image_checked = False

        self._win32 = sys.platform == "win32"
        self._mcp = FastMCP(
            "Script Runner",
            instructions="Use the 'call_help' tool to learn how to use an available script.",
        )
        self._add_tools(self._tools)

    def _add_tools(self, tools: list[Callable]) -> None:
        for tool in tools:
            self._mcp.add_tool(tool)

    async def _build_sandbox_image(self) -> None:
        """Check and build the sandbox docker image if it doesn't exist."""
        dockerfile_dir = str(Path(__file__).parent.expanduser().resolve())
        build_command = self._build_docker_build_command(dockerfile_dir)
        inspect_output = await self._execute_command(
            build_command,
        )
        self._sandbox_image_checked = True

        if not inspect_output.startswith("Error"):
            return

        build_command = [
            "docker",
            "build",
            "-t",
            self._sandbox_image_name,
            dockerfile_dir,
        ]
        build_response = await self._execute_command(build_command)
        if build_response.startswith("Error"):
            print(
                f"Failed to build sandbox image: {build_response}",
                file=sys.stderr,
            )

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

    def _get_executor(self, script_file: Path, sandbox: bool) -> list[str]:
        """
        Determine the appropriate executor command for a script based on its extension.

        Returns:
            list[str] The shell execution command
        """
        script_type = ScriptType.from_suffix(script_file)

        match script_type:
            case ScriptType.POWERSHELL:
                return (
                    [
                        "powershell",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                    ]
                    if self._win32 and not sandbox
                    else ["pwsh", "-File"]
                )
            case ScriptType.PYTHON:
                return ["uv", "run"]
            case ScriptType.BASH:
                if self._win32 and not sandbox:
                    raise ValueError(
                        "Bash scripts can only be run in a sandbox on Windows."
                    )
                return ["bash"]
            case ScriptType.NODE:
                return ["npm", "run"]
            case _:
                raise ValueError(f"Unsupported script type: {script_type}")

    def _is_pwsh_help(self, args: list[str], script_file: Path) -> bool:
        """Determine if the command is for PowerShell help."""
        return (
            args == ["-h"]
            and ScriptType.from_suffix(script_file) == ScriptType.POWERSHELL
        )

    def _build_pwsh_help_command(
        self, script_path: Path, sandbox: bool
    ) -> list[str]:
        """Build the PowerShell help command."""
        shell = "powershell" if self._win32 and not sandbox else "pwsh"
        return [shell, "help", str(script_path)]

    def _build_docker_build_command(self, dockerfile_dir: str) -> list[str]:
        return [
            "docker",
            "build",
            "-t",
            self._sandbox_image_name,
            dockerfile_dir,
        ]

    async def _build_docker_run_command(self, script_dir: Path) -> list[str]:
        """Build the Docker run command."""
        if not self._sandbox_image_checked:
            await self._build_sandbox_image()

        return [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{script_dir}:app",
            "-w",
            "app",
            self._sandbox_image_name,
        ]

    async def _build_command(
        self,
        script_name: str,
        args: list[str],
        directory: str | None,
        sandbox: bool,
    ) -> list[str]:
        use_sandbox = sandbox or self._sandbox

        script_path: Path = self._find_script(script_name, directory)

        command_base: list[str] = (
            self._build_pwsh_help_command(script_path, use_sandbox)
            if self._is_pwsh_help(args, script_path)
            else self._get_executor(script_path, use_sandbox)
        )
        command = (
            await self._build_docker_run_command(script_path) if use_sandbox else []
        )
        command.extend(command_base)
        return command + [str(script_path)] + args

    def _resolve_directory(self, directory: str | None) -> Path:
        """Resolve the directory path to use."""
        return resolve_path(directory) if directory else self._directory

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
            command: list[str] = await self._build_command(
                script_name, ["-h"], directory, sandbox
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
            script_type = ScriptType.from_suffix(file)
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
        "--dir",
        "-d",
        type=str,
        help="Default directory containing scripts to execute",
        default=".",
    )

    parser.add_argument(
        "--sandbox",
        "-s",
        action="store_true",
        help="Run script in sandbox mode",
    )

    args = parser.parse_args()

    _script_dir = resolve_path(args.dir)
    if not _script_dir.exists() or not _script_dir.is_dir():
        print(
            f"❌ Error: Directory '{_script_dir}' does not exist.",
            file=sys.stderr,
        )
        sys.exit(1)

    runner = ScriptRunner(directory=_script_dir, sandbox=args.sandbox)
    runner.run()


if __name__ == "__main__":
    main()
