"""Built-in shell commands"""

import re
import os
import datetime
from typing import List
from .process import Process
from .command_decorators import command
from .exit_codes import EXIT_CODE_BREAK, EXIT_CODE_CONTINUE, EXIT_CODE_RETURN



def _validate_local_path(local_path: str, base_dir: str = None) -> str:
    """Validate that a local path does not traverse outside expected boundaries.

    If base_dir is provided, ensures the resolved path stays within base_dir.

    Returns the resolved (absolute) path, or raises ValueError if unsafe.
    """
    resolved = os.path.realpath(local_path)
    if base_dir is not None:
        resolved_base = os.path.realpath(base_dir)
        if not (resolved == resolved_base or resolved.startswith(resolved_base + os.sep)):
            raise ValueError(f"Path traversal detected: '{local_path}' resolves outside '{base_dir}'")
    return resolved


def _validate_entry_name(name: str) -> str:
    """Validate that a directory entry name from an AGFS listing is safe.

    Rejects names containing path separators or '..' to prevent zip-slip
    style path traversal attacks.

    Returns the name if valid, or raises ValueError if unsafe.
    """
    if not name or name == '.' or name == '..':
        raise ValueError(f"Unsafe directory entry name: '{name}'")
    if '/' in name or '\\' in name or os.sep in name:
        raise ValueError(f"Directory entry name contains path separator: '{name}'")
    if '..' in name.split(os.sep):
        raise ValueError(f"Directory entry name contains '..': '{name}'")
    return name


def _mode_to_rwx(mode: int) -> str:
    """Convert octal file mode to rwx string format"""
    # Handle both full mode (e.g., 0o100644) and just permissions (e.g., 0o644 or 420 decimal)
    # Extract last 9 bits for user/group/other permissions
    perms = mode & 0o777

    def _triple(val):
        """Convert 3-bit value to rwx"""
        r = 'r' if val & 4 else '-'
        w = 'w' if val & 2 else '-'
        x = 'x' if val & 1 else '-'
        return r + w + x

    # Split into user, group, other (3 bits each)
    user = (perms >> 6) & 7
    group = (perms >> 3) & 7
    other = perms & 7

    return _triple(user) + _triple(group) + _triple(other)


@command()
def cmd_echo(process: Process) -> int:
    """Echo arguments to stdout"""
    if process.args:
        output = ' '.join(process.args) + '\n'
        process.stdout.write(output)
    else:
        process.stdout.write('\n')
    return 0


@command(needs_path_resolution=True, supports_streaming=True)
def cmd_cat(process: Process) -> int:
    """
    Concatenate and print files or stdin (streaming mode)

    Usage: cat [file...]
    """
    import sys

    if not process.args:
        # Read from stdin in chunks
        # Use read() instead of get_value() to properly support streaming pipelines
        stdin_value = process.stdin.read()

        if stdin_value:
            # Data from stdin (from pipeline or buffer)
            process.stdout.write(stdin_value)
            process.stdout.flush()
        else:
            # No data in stdin, read from real stdin (interactive mode)
            try:
                while True:
                    chunk = sys.stdin.buffer.read(8192)
                    if not chunk:
                        break
                    process.stdout.write(chunk)
                    process.stdout.flush()
            except KeyboardInterrupt:
                process.stderr.write(b"\ncat: interrupted\n")
                return 130
    else:
        # Read from files in streaming mode
        for filename in process.args:
            try:
                if process.filesystem:
                    # Stream file in chunks
                    stream = process.filesystem.read_file(filename, stream=True)
                    try:
                        for chunk in stream:
                            if chunk:
                                process.stdout.write(chunk)
                                process.stdout.flush()
                    except KeyboardInterrupt:
                        process.stderr.write(b"\ncat: interrupted\n")
                        return 130
                else:
                    # No filesystem configured - cannot read files
                    process.stderr.write(f"cat: {filename}: filesystem not available\n")
                    return 1
            except Exception as e:
                # Extract meaningful error message
                error_msg = str(e)
                if "No such file or directory" in error_msg or "not found" in error_msg.lower():
                    process.stderr.write(f"cat: {filename}: No such file or directory\n")
                else:
                    process.stderr.write(f"cat: {filename}: {error_msg}\n")
                return 1
    return 0


@command(supports_streaming=True)
def cmd_grep(process: Process) -> int:
    """
    Search for pattern in files or stdin

    Usage: grep [OPTIONS] PATTERN [FILE...]

    Options:
        -i          Ignore case
        -v          Invert match (select non-matching lines)
        -n          Print line numbers
        -c          Count matching lines
        -l          Print only filenames with matches
        -h          Suppress filename prefix (default for single file)
        -H          Print filename prefix (default for multiple files)

    Examples:
        echo 'hello world' | grep hello
        grep 'pattern' file.txt
        grep -i 'error' *.log
        grep -n 'function' code.py
        grep -v 'debug' app.log
        grep -c 'TODO' *.py
    """
    import re

    # Parse options
    ignore_case = False
    invert_match = False
    show_line_numbers = False
    count_only = False
    files_only = False
    show_filename = None  # None = auto, True = force, False = suppress

    args = process.args[:]
    options = []

    while args and args[0].startswith('-') and args[0] != '-':
        opt = args.pop(0)
        if opt == '--':
            break

        for char in opt[1:]:
            if char == 'i':
                ignore_case = True
            elif char == 'v':
                invert_match = True
            elif char == 'n':
                show_line_numbers = True
            elif char == 'c':
                count_only = True
            elif char == 'l':
                files_only = True
            elif char == 'h':
                show_filename = False
            elif char == 'H':
                show_filename = True
            else:
                process.stderr.write(f"grep: invalid option -- '{char}'\n")
                return 2

    # Get pattern
    if not args:
        process.stderr.write("grep: missing pattern\n")
        process.stderr.write("Usage: grep [OPTIONS] PATTERN [FILE...]\n")
        return 2

    pattern = args.pop(0)
    files = args

    # Compile regex
    try:
        flags = re.IGNORECASE if ignore_case else 0
        regex = re.compile(pattern, flags)
    except re.error as e:
        process.stderr.write(f"grep: invalid pattern: {e}\n")
        return 2

    # Determine if we should show filenames
    if show_filename is None:
        show_filename = len(files) > 1

    # Process files or stdin
    total_matched = False

    if not files:
        # Read from stdin
        total_matched = _grep_search(
            process, regex, None, invert_match, show_line_numbers,
            count_only, files_only, False
        )
    else:
        # Read from files
        for filepath in files:
            try:
                # Read file content
                content = process.filesystem.read_file(filepath)
                if isinstance(content, bytes):
                    content = content.decode('utf-8')

                # Create a file-like object for the content
                from io import StringIO
                file_obj = StringIO(content)

                matched = _grep_search(
                    process, regex, filepath, invert_match, show_line_numbers,
                    count_only, files_only, show_filename, file_obj
                )

                if matched:
                    total_matched = True
                    if files_only:
                        # Already printed filename, move to next file
                        continue

            except FileNotFoundError:
                process.stderr.write(f"grep: {filepath}: No such file or directory\n")
            except Exception as e:
                process.stderr.write(f"grep: {filepath}: {e}\n")

    return 0 if total_matched else 1


def _grep_search(process, regex, filename, invert_match, show_line_numbers,
                 count_only, files_only, show_filename, file_obj=None):
    """
    Helper function to search for pattern in a file or stdin

    Returns True if any matches found, False otherwise
    """
    if file_obj is None:
        # Read from stdin
        lines = process.stdin.readlines()
    else:
        # Read from file object
        lines = file_obj.readlines()

    match_count = 0
    line_number = 0

    for line in lines:
        line_number += 1

        # Handle both str and bytes
        if isinstance(line, bytes):
            line_str = line.decode('utf-8', errors='replace')
        else:
            line_str = line

        # Remove trailing newline for matching
        line_clean = line_str.rstrip('\n\r')

        # Check if line matches
        matches = bool(regex.search(line_clean))
        if invert_match:
            matches = not matches

        if matches:
            match_count += 1

            if files_only:
                # Just print filename and stop processing this file
                if filename:
                    process.stdout.write(f"{filename}\n")
                return True

            if not count_only:
                # Build output line
                output_parts = []

                if show_filename and filename:
                    output_parts.append(filename)

                if show_line_numbers:
                    output_parts.append(str(line_number))

                # Format: filename:linenum:line or just line
                if output_parts:
                    prefix = ':'.join(output_parts) + ':'
                    process.stdout.write(prefix + line_clean + '\n')
                else:
                    process.stdout.write(line_str if line_str.endswith('\n') else line_clean + '\n')

    # If count_only, print the count
    if count_only:
        if show_filename and filename:
            process.stdout.write(f"{filename}:{match_count}\n")
        else:
            process.stdout.write(f"{match_count}\n")

    return match_count > 0


@command()
def cmd_wc(process: Process) -> int:
    """
    Count lines, words, and bytes

    Usage: wc [-l] [-w] [-c]
    """
    count_lines = False
    count_words = False
    count_bytes = False

    # Parse flags
    flags = [arg for arg in process.args if arg.startswith('-')]
    if not flags:
        # Default: count all
        count_lines = count_words = count_bytes = True
    else:
        for flag in flags:
            if 'l' in flag:
                count_lines = True
            if 'w' in flag:
                count_words = True
            if 'c' in flag:
                count_bytes = True

    # Read all data from stdin
    data = process.stdin.read()

    lines = data.count(b'\n')
    words = len(data.split())
    bytes_count = len(data)

    result = []
    if count_lines:
        result.append(str(lines))
    if count_words:
        result.append(str(words))
    if count_bytes:
        result.append(str(bytes_count))

    output = ' '.join(result) + '\n'
    process.stdout.write(output)

    return 0


@command()
def cmd_head(process: Process) -> int:
    """
    Output the first part of files

    Usage: head [-n count]
    """
    n = 10  # default

    # Parse -n flag
    args = process.args[:]
    i = 0
    while i < len(args):
        if args[i] == '-n' and i + 1 < len(args):
            try:
                n = int(args[i + 1])
                i += 2
                continue
            except ValueError:
                process.stderr.write(f"head: invalid number: {args[i + 1]}\n")
                return 1
        i += 1

    # Read lines from stdin
    lines = process.stdin.readlines()
    for line in lines[:n]:
        process.stdout.write(line)

    return 0


@command(needs_path_resolution=True, supports_streaming=True)
def cmd_tail(process: Process) -> int:
    """
    Output the last part of files

    Usage: tail [-n count] [-f] [-F] [file...]

    Options:
        -n count    Output the last count lines (default: 10)
        -f          Follow mode: show last n lines, then continuously follow
        -F          Stream mode: for streamfs/streamrotatefs only
                    Continuously reads from the stream without loading history
                    Ideal for infinite streams like /streamfs/* or /streamrotate/*
    """
    import time

    n = 10  # default
    follow = False
    stream_only = False  # -F flag: skip reading history
    files = []

    # Parse flags
    args = process.args[:]
    i = 0
    while i < len(args):
        if args[i] == '-n' and i + 1 < len(args):
            try:
                n = int(args[i + 1])
                i += 2
                continue
            except ValueError:
                process.stderr.write(f"tail: invalid number: {args[i + 1]}\n")
                return 1
        elif args[i] == '-f':
            follow = True
            i += 1
        elif args[i] == '-F':
            follow = True
            stream_only = True
            i += 1
        else:
            # This is a file argument
            files.append(args[i])
            i += 1

    # Handle stdin or files
    if not files:
        # Read from stdin
        lines = process.stdin.readlines()
        for line in lines[-n:]:
            process.stdout.write(line)

        if follow:
            process.stderr.write(b"tail: warning: following stdin is not supported\n")

        return 0

    # Read from files
    if not follow:
        # Normal tail mode - read last n lines from each file
        for filename in files:
            try:
                if not process.filesystem:
                    process.stderr.write(b"tail: filesystem not available\n")
                    return 1

                # Use streaming mode to read entire file
                stream = process.filesystem.read_file(filename, stream=True)
                chunks = []
                for chunk in stream:
                    if chunk:
                        chunks.append(chunk)
                content = b''.join(chunks)
                lines = content.decode('utf-8', errors='replace').splitlines(keepends=True)
                for line in lines[-n:]:
                    process.stdout.write(line)
            except Exception as e:
                process.stderr.write(f"tail: {filename}: {str(e)}\n")
                return 1
    else:
        # Follow mode - continuously read new content
        if len(files) > 1:
            process.stderr.write(b"tail: warning: following multiple files not yet supported, using first file\n")

        filename = files[0]

        try:
            if process.filesystem:
                if stream_only:
                    # -F mode: Stream-only mode for filesystems that support streaming
                    # This mode uses continuous streaming read without loading history
                    process.stderr.write(b"==> Continuously reading from stream <==\n")
                    process.stdout.flush()

                    # Use continuous streaming read
                    try:
                        stream = process.filesystem.read_file(filename, stream=True)
                        for chunk in stream:
                            if chunk:
                                process.stdout.write(chunk)
                                process.stdout.flush()
                    except KeyboardInterrupt:
                        process.stderr.write(b"\n")
                        return 0
                    except Exception as e:
                        error_msg = str(e)
                        # Check if it's a streaming-related error
                        if "stream mode" in error_msg.lower() or "use stream" in error_msg.lower():
                            process.stderr.write(f"tail: {filename}: {error_msg}\n".encode())
                            process.stderr.write(b"      Note: -F requires a filesystem that supports streaming\n")
                        else:
                            process.stderr.write(f"tail: {filename}: {error_msg}\n".encode())
                        return 1
                else:
                    # -f mode: Traditional follow mode
                    # First, output the last n lines
                    stream = process.filesystem.read_file(filename, stream=True)
                    chunks = []
                    for chunk in stream:
                        if chunk:
                            chunks.append(chunk)
                    content = b''.join(chunks)
                    lines = content.decode('utf-8', errors='replace').splitlines(keepends=True)
                    for line in lines[-n:]:
                        process.stdout.write(line)
                    process.stdout.flush()

                    # Get current file size
                    file_info = process.filesystem.get_file_info(filename)
                    current_size = file_info.get('size', 0)

                    # Now continuously poll for new content
                    try:
                        while True:
                            time.sleep(0.1)  # Poll every 100ms

                            # Check file size
                            try:
                                file_info = process.filesystem.get_file_info(filename)
                                new_size = file_info.get('size', 0)
                            except Exception:
                                # File might not exist yet, keep waiting
                                continue

                            if new_size > current_size:
                                # Read new content from offset using streaming
                                stream = process.filesystem.read_file(
                                    filename,
                                    offset=current_size,
                                    size=new_size - current_size,
                                    stream=True
                                )
                                for chunk in stream:
                                    if chunk:
                                        process.stdout.write(chunk)
                                process.stdout.flush()
                                current_size = new_size
                    except KeyboardInterrupt:
                        # Clean exit on Ctrl+C
                        process.stderr.write(b"\n")
                        return 0
            else:
                # No filesystem - should not happen in normal usage
                process.stderr.write(b"tail: filesystem not available\n")
                return 1

        except Exception as e:
            process.stderr.write(f"tail: {filename}: {str(e)}\n")
            return 1

    return 0


@command(needs_path_resolution=True)
def cmd_tee(process: Process) -> int:
    """
    Read from stdin and write to both stdout and files (streaming mode)

    Usage: tee [-a] [file...]

    Options:
        -a    Append to files instead of overwriting
    """
    append = False
    files = []

    # Parse arguments
    for arg in process.args:
        if arg == '-a':
            append = True
        else:
            files.append(arg)

    if files and not process.filesystem:
        process.stderr.write(b"tee: filesystem not available\n")
        return 1

    # Read input lines
    lines = process.stdin.readlines()

    # Write to stdout (streaming: flush after each line)
    for line in lines:
        process.stdout.write(line)
        process.stdout.flush()

    # Write to files
    if files:
        if append:
            # Append mode: must collect all data
            content = b''.join(lines)
            for filename in files:
                try:
                    process.filesystem.write_file(filename, content, append=True)
                except Exception as e:
                    process.stderr.write(f"tee: {filename}: {str(e)}\n".encode())
                    return 1
        else:
            # Non-append mode: use streaming write via iterator
            # Create an iterator from lines
            def line_iterator():
                for line in lines:
                    yield line

            for filename in files:
                try:
                    # Pass iterator to write_file for streaming
                    process.filesystem.write_file(filename, line_iterator(), append=False)
                except Exception as e:
                    process.stderr.write(f"tee: {filename}: {str(e)}\n".encode())
                    return 1

    return 0


@command()
def cmd_sort(process: Process) -> int:
    """
    Sort lines of text

    Usage: sort [-r]
    """
    reverse = '-r' in process.args

    # Read lines from stdin
    lines = process.stdin.readlines()
    lines.sort(reverse=reverse)

    for line in lines:
        process.stdout.write(line)

    return 0


@command()
def cmd_uniq(process: Process) -> int:
    """
    Report or omit repeated lines

    Usage: uniq
    """
    lines = process.stdin.readlines()
    if not lines:
        return 0

    prev_line = lines[0]
    process.stdout.write(prev_line)

    for line in lines[1:]:
        if line != prev_line:
            process.stdout.write(line)
            prev_line = line

    return 0


@command()
def cmd_tr(process: Process) -> int:
    """
    Translate characters

    Usage: tr set1 set2
    """
    if len(process.args) < 2:
        process.stderr.write("tr: missing operand\n")
        return 1

    set1 = process.args[0].encode('utf-8')
    set2 = process.args[1].encode('utf-8')

    if len(set1) != len(set2):
        process.stderr.write("tr: sets must be same length\n")
        return 1

    # Create translation table
    trans = bytes.maketrans(set1, set2)

    # Read and translate
    data = process.stdin.read()
    translated = data.translate(trans)
    process.stdout.write(translated)

    return 0


def _human_readable_size(size: int) -> str:
    """Convert size in bytes to human-readable format"""
    units = ['B', 'K', 'M', 'G', 'T', 'P']
    unit_index = 0
    size_float = float(size)

    while size_float >= 1024.0 and unit_index < len(units) - 1:
        size_float /= 1024.0
        unit_index += 1

    if unit_index == 0:
        # Bytes - no decimal
        return f"{int(size_float)}{units[unit_index]}"
    elif size_float >= 10:
        # >= 10 - no decimal places
        return f"{int(size_float)}{units[unit_index]}"
    else:
        # < 10 - one decimal place
        return f"{size_float:.1f}{units[unit_index]}"


@command(needs_path_resolution=True)
def cmd_ls(process: Process) -> int:
    """
    List directory contents

    Usage: ls [-l] [-h] [path...]

    Options:
        -l    Use long listing format
        -h    Print human-readable sizes (e.g., 1K, 234M, 2G)
    """
    # Parse arguments
    long_format = False
    human_readable = False
    paths = []

    for arg in process.args:
        if arg.startswith('-') and arg != '-':
            # Handle combined flags like -lh
            if 'l' in arg:
                long_format = True
            if 'h' in arg:
                human_readable = True
        else:
            paths.append(arg)

    # Default to current working directory if no paths specified
    if not paths:
        cwd = getattr(process, 'cwd', '/')
        paths = [cwd]

    if not process.filesystem:
        process.stderr.write("ls: filesystem not available\n")
        return 1

    # Helper function to format file info
    def format_file_info(file_info, display_name=None):
        """Format a single file info dict for output"""
        name = display_name if display_name else file_info.get('name', '')
        is_dir = file_info.get('isDir', False) or file_info.get('type') == 'directory'
        size = file_info.get('size', 0)

        if long_format:
            # Long format output similar to ls -l
            file_type = 'd' if is_dir else '-'

            # Get mode/permissions
            mode_str = file_info.get('mode', '')
            if mode_str and isinstance(mode_str, str) and len(mode_str) >= 9:
                # Already in rwxr-xr-x format
                perms = mode_str[:9]
            elif mode_str and isinstance(mode_str, int):
                # Convert octal mode to rwx format
                perms = _mode_to_rwx(mode_str)
            else:
                # Default permissions
                perms = 'rwxr-xr-x' if is_dir else 'rw-r--r--'

            # Get modification time
            mtime = file_info.get('modTime', file_info.get('mtime', ''))
            if mtime:
                # Format timestamp (YYYY-MM-DD HH:MM:SS)
                if 'T' in mtime:
                    # ISO format: 2025-11-18T22:00:25Z
                    mtime = mtime.replace('T', ' ').replace('Z', '').split('.')[0]
                elif len(mtime) > 19:
                    # Truncate to 19 chars if too long
                    mtime = mtime[:19]
            else:
                mtime = '0000-00-00 00:00:00'

            # Format: permissions size date time name
            # Add color for directories (blue)
            if is_dir:
                # Blue color for directories
                colored_name = f"\033[1;34m{name}/\033[0m"
            else:
                colored_name = name

            # Format size based on human_readable flag
            if human_readable:
                size_str = f"{_human_readable_size(size):>8}"
            else:
                size_str = f"{size:>8}"

            return f"{file_type}{perms} {size_str} {mtime} {colored_name}\n"
        else:
            # Simple formatting
            if is_dir:
                # Blue color for directories
                return f"\033[1;34m{name}/\033[0m\n"
            else:
                return f"{name}\n"

    exit_code = 0

    try:
        # Process each path argument
        for path in paths:
            try:
                # First, get info about the path to determine if it's a file or directory
                path_info = process.filesystem.get_file_info(path)
                is_directory = path_info.get('isDir', False) or path_info.get('type') == 'directory'

                if is_directory:
                    # It's a directory - list its contents
                    files = process.filesystem.list_directory(path)

                    # Show directory name if multiple paths
                    if len(paths) > 1:
                        process.stdout.write(f"{path}:\n".encode('utf-8'))

                    for file_info in files:
                        output = format_file_info(file_info)
                        process.stdout.write(output.encode('utf-8'))

                    # Add blank line between directories if multiple paths
                    if len(paths) > 1:
                        process.stdout.write(b"\n")
                else:
                    # It's a file - display info about the file itself
                    import os
                    basename = os.path.basename(path)
                    output = format_file_info(path_info, display_name=basename)
                    process.stdout.write(output.encode('utf-8'))

            except Exception as e:
                error_msg = str(e)
                if "No such file or directory" in error_msg or "not found" in error_msg.lower():
                    process.stderr.write(f"ls: {path}: No such file or directory\n")
                else:
                    process.stderr.write(f"ls: {path}: {error_msg}\n")
                exit_code = 1

        return exit_code
    except Exception as e:
        error_msg = str(e)
        process.stderr.write(f"ls: {error_msg}\n")
        return 1


@command()
def cmd_pwd(process: Process) -> int:
    """
    Print working directory

    Usage: pwd
    """
    # Get cwd from process metadata if available
    cwd = getattr(process, 'cwd', '/')
    process.stdout.write(f"{cwd}\n".encode('utf-8'))
    return 0


@command(no_pipeline=True, changes_cwd=True, needs_path_resolution=True)
def cmd_cd(process: Process) -> int:
    """
    Change directory

    Usage: cd [path]

    Note: This is a special builtin that needs to be handled by the shell
    """
    if not process.args:
        # cd with no args goes to root
        target_path = '/'
    else:
        target_path = process.args[0]

    if not process.filesystem:
        process.stderr.write("cd: filesystem not available\n")
        return 1

    # Store the target path in process metadata for shell to handle
    # The shell will resolve the path and verify it exists
    process.cd_target = target_path

    # Return special exit code to indicate cd operation
    # Shell will check for this and update cwd
    return 0


@command(needs_path_resolution=True)
def cmd_mkdir(process: Process) -> int:
    """
    Create directory

    Usage: mkdir path
    """
    if not process.args:
        process.stderr.write("mkdir: missing operand\n")
        return 1

    if not process.filesystem:
        process.stderr.write("mkdir: filesystem not available\n")
        return 1

    path = process.args[0]

    try:
        # Use AGFS client to create directory
        process.filesystem.client.mkdir(path)
        return 0
    except Exception as e:
        error_msg = str(e)
        process.stderr.write(f"mkdir: {path}: {error_msg}\n")
        return 1


@command(needs_path_resolution=True)
def cmd_touch(process: Process) -> int:
    """
    Touch file (update timestamp)

    Usage: touch file...
    """
    if not process.args:
        process.stderr.write("touch: missing file operand\n")
        return 1

    if not process.filesystem:
        process.stderr.write("touch: filesystem not available\n")
        return 1

    for path in process.args:
        try:
            process.filesystem.touch_file(path)
        except Exception as e:
            error_msg = str(e)
            process.stderr.write(f"touch: {path}: {error_msg}\n")
            return 1

    return 0


@command(needs_path_resolution=True)
def cmd_rm(process: Process) -> int:
    """
    Remove file or directory

    Usage: rm [-r] path...
    """
    if not process.args:
        process.stderr.write("rm: missing operand\n")
        return 1

    if not process.filesystem:
        process.stderr.write("rm: filesystem not available\n")
        return 1

    recursive = False
    paths = []

    for arg in process.args:
        if arg == '-r' or arg == '-rf':
            recursive = True
        else:
            paths.append(arg)

    if not paths:
        process.stderr.write("rm: missing file operand\n")
        return 1

    exit_code = 0

    for path in paths:
        try:
            # Use AGFS client to remove file/directory
            process.filesystem.client.rm(path, recursive=recursive)
        except Exception as e:
            error_msg = str(e)
            process.stderr.write(f"rm: {path}: {error_msg}\n")
            exit_code = 1

    return exit_code


@command()
def cmd_export(process: Process) -> int:
    """
    Set or display environment variables

    Usage: export [VAR=value ...]
    """
    if not process.args:
        # Display all environment variables (like 'env')
        if hasattr(process, 'env'):
            for key, value in sorted(process.env.items()):
                process.stdout.write(f"{key}={value}\n".encode('utf-8'))
        return 0

    # Set environment variables
    for arg in process.args:
        if '=' in arg:
            var_name, var_value = arg.split('=', 1)
            var_name = var_name.strip()
            var_value = var_value.strip()

            # Validate variable name
            if var_name and var_name.replace('_', '').replace('-', '').isalnum():
                if hasattr(process, 'env'):
                    process.env[var_name] = var_value
            else:
                process.stderr.write(f"export: invalid variable name: {var_name}\n")
                return 1
        else:
            process.stderr.write(f"export: usage: export VAR=value\n")
            return 1

    return 0


@command()
def cmd_env(process: Process) -> int:
    """
    Display all environment variables

    Usage: env
    """
    if hasattr(process, 'env'):
        for key, value in sorted(process.env.items()):
            process.stdout.write(f"{key}={value}\n".encode('utf-8'))
    return 0


@command()
def cmd_unset(process: Process) -> int:
    """
    Unset environment variables

    Usage: unset VAR [VAR ...]
    """
    if not process.args:
        process.stderr.write("unset: missing variable name\n")
        return 1

    if not hasattr(process, 'env'):
        return 0

    for var_name in process.args:
        if var_name in process.env:
            del process.env[var_name]

    return 0


@command()
def cmd_test(process: Process) -> int:
    """
    Evaluate conditional expressions (similar to bash test/[)

    Usage: test EXPRESSION
           [ EXPRESSION ]

    File operators:
      -f FILE    True if file exists and is a regular file
      -d FILE    True if file exists and is a directory
      -e FILE    True if file exists

    String operators:
      -z STRING  True if string is empty
      -n STRING  True if string is not empty
      STRING1 = STRING2   True if strings are equal
      STRING1 != STRING2  True if strings are not equal

    Integer operators:
      INT1 -eq INT2  True if integers are equal
      INT1 -ne INT2  True if integers are not equal
      INT1 -gt INT2  True if INT1 is greater than INT2
      INT1 -lt INT2  True if INT1 is less than INT2
      INT1 -ge INT2  True if INT1 is greater than or equal to INT2
      INT1 -le INT2  True if INT1 is less than or equal to INT2

    Logical operators:
      ! EXPR     True if expr is false
      EXPR -a EXPR  True if both expressions are true (AND)
      EXPR -o EXPR  True if either expression is true (OR)
    """
    # Handle [ command - last arg should be ]
    if process.command == '[':
        if not process.args or process.args[-1] != ']':
            process.stderr.write("[: missing ']'\n")
            return 2
        # Remove the closing ]
        process.args = process.args[:-1]

    if not process.args:
        # Empty test is false
        return 1

    # Evaluate the expression
    try:
        result = _evaluate_test_expression(process.args, process)
        return 0 if result else 1
    except Exception as e:
        process.stderr.write(f"test: {e}\n")
        return 2


def _evaluate_test_expression(args: List[str], process: Process) -> bool:
    """Evaluate a test expression"""
    if not args:
        return False

    # Single argument - test if non-empty string
    if len(args) == 1:
        return bool(args[0])

    # Negation operator
    if args[0] == '!':
        return not _evaluate_test_expression(args[1:], process)

    # File test operators
    if args[0] == '-f':
        if len(args) < 2:
            raise ValueError("-f requires an argument")
        path = args[1]
        if process.filesystem:
            try:
                info = process.filesystem.get_file_info(path)
                is_dir = info.get('isDir', False) or info.get('type') == 'directory'
                return not is_dir
            except:
                return False
        return False

    if args[0] == '-d':
        if len(args) < 2:
            raise ValueError("-d requires an argument")
        path = args[1]
        if process.filesystem:
            return process.filesystem.is_directory(path)
        return False

    if args[0] == '-e':
        if len(args) < 2:
            raise ValueError("-e requires an argument")
        path = args[1]
        if process.filesystem:
            return process.filesystem.file_exists(path)
        return False

    # String test operators
    if args[0] == '-z':
        if len(args) < 2:
            raise ValueError("-z requires an argument")
        return len(args[1]) == 0

    if args[0] == '-n':
        if len(args) < 2:
            raise ValueError("-n requires an argument")
        return len(args[1]) > 0

    # Binary operators
    if len(args) >= 3:
        # Logical AND
        if '-a' in args:
            idx = args.index('-a')
            left = _evaluate_test_expression(args[:idx], process)
            right = _evaluate_test_expression(args[idx+1:], process)
            return left and right

        # Logical OR
        if '-o' in args:
            idx = args.index('-o')
            left = _evaluate_test_expression(args[:idx], process)
            right = _evaluate_test_expression(args[idx+1:], process)
            return left or right

        # String comparison
        if args[1] == '=':
            return args[0] == args[2]

        if args[1] == '!=':
            return args[0] != args[2]

        # Integer comparison
        if args[1] in ['-eq', '-ne', '-gt', '-lt', '-ge', '-le']:
            try:
                left = int(args[0])
                right = int(args[2])
                if args[1] == '-eq':
                    return left == right
                elif args[1] == '-ne':
                    return left != right
                elif args[1] == '-gt':
                    return left > right
                elif args[1] == '-lt':
                    return left < right
                elif args[1] == '-ge':
                    return left >= right
                elif args[1] == '-le':
                    return left <= right
            except ValueError:
                raise ValueError(f"integer expression expected: {args[0]} or {args[2]}")

    # Default: non-empty first argument
    return bool(args[0])


@command(supports_streaming=True)
def cmd_jq(process: Process) -> int:
    """
    Process JSON using jq-like syntax

    Usage:
        jq FILTER [file...]
        cat file.json | jq FILTER

    Examples:
        echo '{"name":"test"}' | jq .
        cat data.json | jq '.name'
        jq '.items[]' data.json
    """
    try:
        import jq as jq_lib
        import json
    except ImportError:
        process.stderr.write("jq: jq library not installed (run: uv pip install jq)\n")
        return 1

    # First argument is the filter
    if not process.args:
        process.stderr.write("jq: missing filter expression\n")
        process.stderr.write("Usage: jq FILTER [file...]\n")
        return 1

    filter_expr = process.args[0]
    input_files = process.args[1:] if len(process.args) > 1 else []

    try:
        # Compile the jq filter
        compiled_filter = jq_lib.compile(filter_expr)
    except Exception as e:
        process.stderr.write(f"jq: compile error: {e}\n")
        return 1

    # Read JSON input
    json_data = []

    if input_files:
        # Read from files
        for filepath in input_files:
            try:
                # Read file content
                content = process.filesystem.read_file(filepath)
                if isinstance(content, bytes):
                    content = content.decode('utf-8')

                # Parse JSON
                data = json.loads(content)
                json_data.append(data)
            except FileNotFoundError:
                process.stderr.write(f"jq: {filepath}: No such file or directory\n")
                return 1
            except json.JSONDecodeError as e:
                process.stderr.write(f"jq: {filepath}: parse error: {e}\n")
                return 1
            except Exception as e:
                process.stderr.write(f"jq: {filepath}: {e}\n")
                return 1
    else:
        # Read from stdin
        stdin_data = process.stdin.read()
        if isinstance(stdin_data, bytes):
            stdin_data = stdin_data.decode('utf-8')

        if not stdin_data.strip():
            process.stderr.write("jq: no input\n")
            return 1

        try:
            data = json.loads(stdin_data)
            json_data.append(data)
        except json.JSONDecodeError as e:
            process.stderr.write(f"jq: parse error: {e}\n")
            return 1

    # Apply filter to each JSON input
    try:
        for data in json_data:
            # Run the filter
            results = compiled_filter.input(data)

            # Output results
            for result in results:
                # Pretty print JSON output
                output = json.dumps(result, indent=2, ensure_ascii=False)
                process.stdout.write(output + '\n')

        return 0
    except Exception as e:
        process.stderr.write(f"jq: filter error: {e}\n")
        return 1


@command(needs_path_resolution=True)
def cmd_stat(process: Process) -> int:
    """
    Display file status and check if file exists

    Usage: stat path
    """
    if not process.args:
        process.stderr.write("stat: missing operand\n")
        return 1

    if not process.filesystem:
        process.stderr.write("stat: filesystem not available\n")
        return 1

    path = process.args[0]

    try:
        # Get file info from the filesystem
        file_info = process.filesystem.get_file_info(path)

        # File exists, display information
        name = file_info.get('name', path.split('/')[-1] if '/' in path else path)
        is_dir = file_info.get('isDir', False) or file_info.get('type') == 'directory'
        size = file_info.get('size', 0)

        # Get mode/permissions
        mode_str = file_info.get('mode', '')
        if mode_str and isinstance(mode_str, str) and len(mode_str) >= 9:
            perms = mode_str[:9]
        elif mode_str and isinstance(mode_str, int):
            perms = _mode_to_rwx(mode_str)
        else:
            perms = 'rwxr-xr-x' if is_dir else 'rw-r--r--'

        # Get modification time
        mtime = file_info.get('modTime', file_info.get('mtime', ''))
        if mtime:
            if 'T' in mtime:
                mtime = mtime.replace('T', ' ').replace('Z', '').split('.')[0]
            elif len(mtime) > 19:
                mtime = mtime[:19]
        else:
            mtime = 'unknown'

        # Build output
        file_type = 'directory' if is_dir else 'regular file'
        output = f"  File: {name}\n"
        output += f"  Type: {file_type}\n"
        output += f"  Size: {size} bytes\n"
        output += f"  Mode: {perms}\n"
        output += f"  Modified: {mtime}\n"

        process.stdout.write(output.encode('utf-8'))
        return 0

    except Exception as e:
        error_msg = str(e)
        if "No such file or directory" in error_msg or "not found" in error_msg.lower():
            process.stderr.write("stat: No such file or directory\n")
        else:
            process.stderr.write(f"stat: {path}: {error_msg}\n")
        return 1


@command()
def cmd_upload(process: Process) -> int:
    """
    Upload a local file or directory to AGFS

    Usage: upload [-r] <local_path> <agfs_path>
    """
    # Parse arguments
    recursive = False
    args = process.args[:]

    if args and args[0] == '-r':
        recursive = True
        args = args[1:]

    if len(args) != 2:
        process.stderr.write("upload: usage: upload [-r] <local_path> <agfs_path>\n")
        return 1

    local_path = args[0]
    agfs_path = args[1]

    # Resolve agfs_path relative to current working directory
    if not agfs_path.startswith('/'):
        agfs_path = os.path.join(process.cwd, agfs_path)
        agfs_path = os.path.normpath(agfs_path)

    try:
        # Check if local path exists
        if not os.path.exists(local_path):
            process.stderr.write(f"upload: {local_path}: No such file or directory\n")
            return 1

        # Check if destination is a directory
        try:
            dest_info = process.filesystem.get_file_info(agfs_path)
            if dest_info.get('isDir', False):
                # Destination is a directory, append source filename
                source_basename = os.path.basename(local_path)
                agfs_path = os.path.join(agfs_path, source_basename)
                agfs_path = os.path.normpath(agfs_path)
        except Exception:
            # Destination doesn't exist, use as-is
            pass

        if os.path.isfile(local_path):
            # Upload single file
            return _upload_file(process, local_path, agfs_path)
        elif os.path.isdir(local_path):
            if not recursive:
                process.stderr.write(f"upload: {local_path}: Is a directory (use -r to upload recursively)\n")
                return 1
            # Upload directory recursively
            return _upload_dir(process, local_path, agfs_path)
        else:
            process.stderr.write(f"upload: {local_path}: Not a file or directory\n")
            return 1

    except Exception as e:
        error_msg = str(e)
        process.stderr.write(f"upload: {error_msg}\n")
        return 1


def _upload_file(process: Process, local_path: str, agfs_path: str, show_progress: bool = True) -> int:
    """Helper: Upload a single file to AGFS"""
    try:
        # Validate local path to prevent path traversal
        try:
            local_path = _validate_local_path(local_path)
        except ValueError as e:
            process.stderr.write(f"upload: {e}\n")
            return 1

        with open(local_path, 'rb') as f:
            data = f.read()
            process.filesystem.write_file(agfs_path, data, append=False)

        if show_progress:
            process.stdout.write(f"Uploaded {len(data)} bytes to {agfs_path}\n")
            process.stdout.flush()
        return 0

    except Exception as e:
        process.stderr.write(f"upload: {local_path}: {str(e)}\n")
        return 1


def _upload_dir(process: Process, local_path: str, agfs_path: str) -> int:
    """Helper: Upload a directory recursively to AGFS"""
    import stat as stat_module

    try:
        # Create target directory in AGFS if it doesn't exist
        try:
            info = process.filesystem.get_file_info(agfs_path)
            if not info.get('isDir', False):
                process.stderr.write(f"upload: {agfs_path}: Not a directory\n")
                return 1
        except Exception:
            # Directory doesn't exist, create it
            try:
                # Use mkdir command to create directory
                from pyagfs import AGFSClient
                process.filesystem.client.mkdir(agfs_path)
            except Exception as e:
                process.stderr.write(f"upload: cannot create directory {agfs_path}: {str(e)}\n")
                return 1

        # Walk through local directory
        for root, dirs, files in os.walk(local_path):
            # Calculate relative path
            rel_path = os.path.relpath(root, local_path)
            if rel_path == '.':
                current_agfs_dir = agfs_path
            else:
                current_agfs_dir = os.path.join(agfs_path, rel_path)
                current_agfs_dir = os.path.normpath(current_agfs_dir)

            # Create subdirectories in AGFS
            for dirname in dirs:
                dir_agfs_path = os.path.join(current_agfs_dir, dirname)
                dir_agfs_path = os.path.normpath(dir_agfs_path)
                try:
                    process.filesystem.client.mkdir(dir_agfs_path)
                except Exception:
                    # Directory might already exist, ignore
                    pass

            # Upload files
            for filename in files:
                local_file = os.path.join(root, filename)
                agfs_file = os.path.join(current_agfs_dir, filename)
                agfs_file = os.path.normpath(agfs_file)

                result = _upload_file(process, local_file, agfs_file)
                if result != 0:
                    return result

        return 0

    except Exception as e:
        process.stderr.write(f"upload: {str(e)}\n")
        return 1


@command()
def cmd_download(process: Process) -> int:
    """
    Download an AGFS file or directory to local filesystem

    Usage: download [-r] <agfs_path> <local_path>
    """
    # Parse arguments
    recursive = False
    args = process.args[:]

    if args and args[0] == '-r':
        recursive = True
        args = args[1:]

    if len(args) != 2:
        process.stderr.write("download: usage: download [-r] <agfs_path> <local_path>\n")
        return 1

    agfs_path = args[0]
    local_path = args[1]

    # Resolve agfs_path relative to current working directory
    if not agfs_path.startswith('/'):
        agfs_path = os.path.join(process.cwd, agfs_path)
        agfs_path = os.path.normpath(agfs_path)

    try:
        # Check if source path is a directory
        info = process.filesystem.get_file_info(agfs_path)

        # Check if destination is a local directory
        if os.path.isdir(local_path):
            # Destination is a directory, append source filename
            source_basename = os.path.basename(agfs_path)
            local_path = os.path.join(local_path, source_basename)

        if info.get('isDir', False):
            if not recursive:
                process.stderr.write(f"download: {agfs_path}: Is a directory (use -r to download recursively)\n")
                return 1
            # Download directory recursively
            return _download_dir(process, agfs_path, local_path)
        else:
            # Download single file
            return _download_file(process, agfs_path, local_path)

    except FileNotFoundError:
        process.stderr.write(f"download: {local_path}: Cannot create file\n")
        return 1
    except PermissionError:
        process.stderr.write(f"download: {local_path}: Permission denied\n")
        return 1
    except Exception as e:
        error_msg = str(e)
        if "No such file or directory" in error_msg or "not found" in error_msg.lower():
            process.stderr.write(f"download: {agfs_path}: No such file or directory\n")
        else:
            process.stderr.write(f"download: {error_msg}\n")
        return 1


def _download_file(process: Process, agfs_path: str, local_path: str, show_progress: bool = True) -> int:
    """Helper: Download a single file from AGFS"""
    try:
        # Validate local path to prevent path traversal
        try:
            local_path = _validate_local_path(local_path)
        except ValueError as e:
            process.stderr.write(f"download: {e}\n")
            return 1

        stream = process.filesystem.read_file(agfs_path, stream=True)
        bytes_written = 0

        with open(local_path, 'wb') as f:
            for chunk in stream:
                if chunk:
                    f.write(chunk)
                    bytes_written += len(chunk)

        if show_progress:
            process.stdout.write(f"Downloaded {bytes_written} bytes to {local_path}\n")
            process.stdout.flush()
        return 0

    except Exception as e:
        process.stderr.write(f"download: {agfs_path}: {str(e)}\n")
        return 1


def _download_dir(process: Process, agfs_path: str, local_path: str) -> int:
    """Helper: Download a directory recursively from AGFS"""
    try:
        # Validate local path to prevent path traversal
        try:
            local_path = _validate_local_path(local_path)
        except ValueError as e:
            process.stderr.write(f"download: {e}\n")
            return 1

        # Create local directory if it doesn't exist
        os.makedirs(local_path, exist_ok=True)

        # List AGFS directory
        entries = process.filesystem.list_directory(agfs_path)

        for entry in entries:
            name = entry['name']
            is_dir = entry.get('isDir', False)

            # Validate entry name to prevent zip-slip style attacks
            try:
                _validate_entry_name(name)
            except ValueError as e:
                process.stderr.write(f"download: skipping unsafe entry: {e}\n")
                continue

            agfs_item = os.path.join(agfs_path, name)
            agfs_item = os.path.normpath(agfs_item)
            local_item = os.path.join(local_path, name)

            # Verify the resolved local item is still within local_path
            resolved_item = os.path.realpath(local_item)
            resolved_base = os.path.realpath(local_path)
            if not (resolved_item == resolved_base or resolved_item.startswith(resolved_base + os.sep)):
                process.stderr.write(f"download: skipping entry that escapes target directory: \'{name}\'\n")
                continue

            if is_dir:
                # Recursively download subdirectory
                result = _download_dir(process, agfs_item, local_item)
                if result != 0:
                    return result
            else:
                # Download file
                result = _download_file(process, agfs_item, local_item)
                if result != 0:
                    return result

        return 0

    except Exception as e:
        process.stderr.write(f"download: {str(e)}\n")
        return 1


@command()
def cmd_cp(process: Process) -> int:
    """
    Copy files between local filesystem and AGFS

    Usage:
        cp [-r] <source>... <dest>
        cp [-r] local:<path> <agfs_path>   # Upload from local to AGFS
        cp [-r] <agfs_path> local:<path>   # Download from AGFS to local
        cp [-r] <agfs_path1> <agfs_path2>  # Copy within AGFS
    """
    import os

    # Parse arguments
    recursive = False
    args = process.args[:]

    if args and args[0] == '-r':
        recursive = True
        args = args[1:]

    if len(args) < 2:
        process.stderr.write("cp: usage: cp [-r] <source>... <dest>\n")
        return 1

    # Last argument is destination, all others are sources
    sources = args[:-1]
    dest = args[-1]

    # Parse dest to determine if it's local
    dest_is_local = dest.startswith('local:')
    if dest_is_local:
        dest = dest[6:]  # Remove 'local:' prefix
    else:
        # Resolve AGFS path relative to current working directory
        if not dest.startswith('/'):
            dest = os.path.join(process.cwd, dest)
            dest = os.path.normpath(dest)

    exit_code = 0

    # Process each source file
    for source in sources:
        # Parse source to determine operation type
        source_is_local = source.startswith('local:')

        if source_is_local:
            source = source[6:]  # Remove 'local:' prefix
        else:
            # Resolve AGFS path relative to current working directory
            if not source.startswith('/'):
                source = os.path.join(process.cwd, source)
                source = os.path.normpath(source)

        # Determine operation type
        if source_is_local and not dest_is_local:
            # Upload: local -> AGFS
            result = _cp_upload(process, source, dest, recursive)
        elif not source_is_local and dest_is_local:
            # Download: AGFS -> local
            result = _cp_download(process, source, dest, recursive)
        elif not source_is_local and not dest_is_local:
            # Copy within AGFS
            result = _cp_agfs(process, source, dest, recursive)
        else:
            # local -> local (not supported, use system cp)
            process.stderr.write("cp: local to local copy not supported, use system cp command\n")
            result = 1

        if result != 0:
            exit_code = result

    return exit_code


def _cp_upload(process: Process, local_path: str, agfs_path: str, recursive: bool = False) -> int:
    """Helper: Upload local file or directory to AGFS

    Note: agfs_path should already be resolved to absolute path by caller
    """
    try:
        if not os.path.exists(local_path):
            process.stderr.write(f"cp: {local_path}: No such file or directory\n")
            return 1

        # Check if destination is a directory
        try:
            dest_info = process.filesystem.get_file_info(agfs_path)
            if dest_info.get('isDir', False):
                # Destination is a directory, append source filename
                source_basename = os.path.basename(local_path)
                agfs_path = os.path.join(agfs_path, source_basename)
                agfs_path = os.path.normpath(agfs_path)
        except Exception:
            # Destination doesn't exist, use as-is
            pass

        # Validate local path to prevent path traversal
        try:
            local_path = _validate_local_path(local_path)
        except ValueError as e:
            process.stderr.write(f"cp: {e}\n")
            return 1

        if os.path.isfile(local_path):
            # Show progress
            process.stdout.write(f"local:{local_path} -> {agfs_path}\n")
            process.stdout.flush()

            # Upload file
            with open(local_path, 'rb') as f:
                process.filesystem.write_file(agfs_path, f.read(), append=False)
            return 0

        elif os.path.isdir(local_path):
            if not recursive:
                process.stderr.write(f"cp: {local_path}: Is a directory (use -r to copy recursively)\n")
                return 1
            # Upload directory recursively
            return _upload_dir(process, local_path, agfs_path)

        else:
            process.stderr.write(f"cp: {local_path}: Not a file or directory\n")
            return 1

    except Exception as e:
        process.stderr.write(f"cp: {str(e)}\n")
        return 1


def _cp_download(process: Process, agfs_path: str, local_path: str, recursive: bool = False) -> int:
    """Helper: Download AGFS file or directory to local

    Note: agfs_path should already be resolved to absolute path by caller
    """
    try:
        # Check if source is a directory
        info = process.filesystem.get_file_info(agfs_path)

        # Check if destination is a local directory
        if os.path.isdir(local_path):
            # Destination is a directory, append source filename
            source_basename = os.path.basename(agfs_path)
            local_path = os.path.join(local_path, source_basename)

        if info.get('isDir', False):
            if not recursive:
                process.stderr.write(f"cp: {agfs_path}: Is a directory (use -r to copy recursively)\n")
                return 1
            # Download directory recursively
            return _download_dir(process, agfs_path, local_path)
        else:
            # Show progress
            process.stdout.write(f"{agfs_path} -> local:{local_path}\n")
            process.stdout.flush()

            # Download single file - validate local path first
            try:
                local_path = _validate_local_path(local_path)
            except ValueError as e:
                process.stderr.write(f"cp: {e}\n")
                return 1

            stream = process.filesystem.read_file(agfs_path, stream=True)
            with open(local_path, 'wb') as f:
                for chunk in stream:
                    if chunk:
                        f.write(chunk)
            return 0

    except FileNotFoundError:
        process.stderr.write(f"cp: {local_path}: Cannot create file\n")
        return 1
    except PermissionError:
        process.stderr.write(f"cp: {local_path}: Permission denied\n")
        return 1
    except Exception as e:
        error_msg = str(e)
        if "No such file or directory" in error_msg or "not found" in error_msg.lower():
            process.stderr.write(f"cp: {agfs_path}: No such file or directory\n")
        else:
            process.stderr.write(f"cp: {str(e)}\n")
        return 1


def _cp_agfs(process: Process, source_path: str, dest_path: str, recursive: bool = False) -> int:
    """Helper: Copy within AGFS

    Note: source_path and dest_path should already be resolved to absolute paths by caller
    """
    try:
        # Check if source is a directory
        info = process.filesystem.get_file_info(source_path)

        # Check if destination is a directory
        try:
            dest_info = process.filesystem.get_file_info(dest_path)
            if dest_info.get('isDir', False):
                # Destination is a directory, append source filename
                source_basename = os.path.basename(source_path)
                dest_path = os.path.join(dest_path, source_basename)
                dest_path = os.path.normpath(dest_path)
        except Exception:
            # Destination doesn't exist, use as-is
            pass

        if info.get('isDir', False):
            if not recursive:
                process.stderr.write(f"cp: {source_path}: Is a directory (use -r to copy recursively)\n")
                return 1
            # Copy directory recursively
            return _cp_agfs_dir(process, source_path, dest_path)
        else:
            # Show progress
            process.stdout.write(f"{source_path} -> {dest_path}\n")
            process.stdout.flush()

            # Copy single file - read all at once to avoid append overhead
            data = process.filesystem.read_file(source_path, stream=False)
            process.filesystem.write_file(dest_path, data, append=False)

            return 0

    except Exception as e:
        error_msg = str(e)
        if "No such file or directory" in error_msg or "not found" in error_msg.lower():
            process.stderr.write(f"cp: {source_path}: No such file or directory\n")
        else:
            process.stderr.write(f"cp: {str(e)}\n")
        return 1


def _cp_agfs_dir(process: Process, source_path: str, dest_path: str) -> int:
    """Helper: Recursively copy directory within AGFS"""
    try:
        # Create destination directory if it doesn't exist
        try:
            info = process.filesystem.get_file_info(dest_path)
            if not info.get('isDir', False):
                process.stderr.write(f"cp: {dest_path}: Not a directory\n")
                return 1
        except Exception:
            # Directory doesn't exist, create it
            try:
                process.filesystem.client.mkdir(dest_path)
            except Exception as e:
                process.stderr.write(f"cp: cannot create directory {dest_path}: {str(e)}\n")
                return 1

        # List source directory
        entries = process.filesystem.list_directory(source_path)

        for entry in entries:
            name = entry['name']
            is_dir = entry.get('isDir', False)

            src_item = os.path.join(source_path, name)
            src_item = os.path.normpath(src_item)
            dst_item = os.path.join(dest_path, name)
            dst_item = os.path.normpath(dst_item)

            if is_dir:
                # Recursively copy subdirectory
                result = _cp_agfs_dir(process, src_item, dst_item)
                if result != 0:
                    return result
            else:
                # Show progress
                process.stdout.write(f"{src_item} -> {dst_item}\n")
                process.stdout.flush()

                # Copy file - read all at once to avoid append overhead
                data = process.filesystem.read_file(src_item, stream=False)
                process.filesystem.write_file(dst_item, data, append=False)

        return 0

    except Exception as e:
        process.stderr.write(f"cp: {str(e)}\n")
        return 1


@command()
def cmd_sleep(process: Process) -> int:
    """
    Pause execution for specified seconds

    Usage: sleep SECONDS

    Examples:
        sleep 1      # Sleep for 1 second
        sleep 0.5    # Sleep for 0.5 seconds
        sleep 5      # Sleep for 5 seconds
    """
    import time

    if not process.args:
        process.stderr.write("sleep: missing operand\n")
        process.stderr.write("Usage: sleep SECONDS\n")
        return 1

    try:
        seconds = float(process.args[0])
        if seconds < 0:
            process.stderr.write("sleep: invalid time interval\n")
            return 1

        time.sleep(seconds)
        return 0
    except ValueError:
        process.stderr.write(f"sleep: invalid time interval '{process.args[0]}'\n")
        return 1
    except KeyboardInterrupt:
        process.stderr.write("\nsleep: interrupted\n")
        return 130


@command()
def cmd_plugins(process: Process) -> int:
    """
    Manage AGFS plugins

    Usage: plugins <subcommand> [arguments]

    Subcommands:
        list [-v]         List all plugins (builtin and external)
        load <path>       Load external plugin from AGFS or HTTP(S)
        unload <path>     Unload external plugin

    Options:
        -v                Show detailed configuration parameters

    Path formats for load:
        <relative_path>    - Load from AGFS (relative to current directory)
        <absolute_path>    - Load from AGFS (absolute path)
        http(s)://<url>    - Load from HTTP(S) URL

    Examples:
        plugins list                                  # List all plugins
        plugins list -v                               # List with config details
        plugins load /mnt/plugins/myplugin.so         # Load from AGFS (absolute)
        plugins load myplugin.so                      # Load from current directory
        plugins load ../plugins/myplugin.so           # Load from relative path
        plugins load https://example.com/myplugin.so  # Load from HTTP(S)
        plugins unload /mnt/plugins/myplugin.so       # Unload plugin
    """
    if not process.filesystem:
        process.stderr.write("plugins: filesystem not available\n")
        return 1

    # No arguments - show usage
    if len(process.args) == 0:
        process.stderr.write("Usage: plugins <subcommand> [arguments]\n")
        process.stderr.write("\nSubcommands:\n")
        process.stderr.write("  list           - List all plugins (builtin and external)\n")
        process.stderr.write("  load <path>    - Load external plugin\n")
        process.stderr.write("  unload <path>  - Unload external plugin\n")
        process.stderr.write("\nPath formats for load:\n")
        process.stderr.write("  <relative_path>  - Load from AGFS (relative to current directory)\n")
        process.stderr.write("  <absolute_path>  - Load from AGFS (absolute path)\n")
        process.stderr.write("  http(s)://<url>  - Load from HTTP(S) URL\n")
        process.stderr.write("\nExamples:\n")
        process.stderr.write("  plugins list\n")
        process.stderr.write("  plugins load /mnt/plugins/myplugin.so         # Absolute path\n")
        process.stderr.write("  plugins load myplugin.so                      # Current directory\n")
        process.stderr.write("  plugins load ../plugins/myplugin.so           # Relative path\n")
        process.stderr.write("  plugins load https://example.com/myplugin.so  # HTTP(S) URL\n")
        return 1

    # Handle plugin subcommands
    subcommand = process.args[0].lower()

    if subcommand == "load":
        if len(process.args) < 2:
            process.stderr.write("Usage: plugins load <path>\n")
            process.stderr.write("\nPath formats:\n")
            process.stderr.write("  <relative_path>  - Load from AGFS (relative to current directory)\n")
            process.stderr.write("  <absolute_path>  - Load from AGFS (absolute path)\n")
            process.stderr.write("  http(s)://<url>  - Load from HTTP(S) URL\n")
            process.stderr.write("\nExamples:\n")
            process.stderr.write("  plugins load /mnt/plugins/myplugin.so        # Absolute path\n")
            process.stderr.write("  plugins load myplugin.so                     # Current directory\n")
            process.stderr.write("  plugins load ../plugins/myplugin.so          # Relative path\n")
            process.stderr.write("  plugins load https://example.com/myplugin.so # HTTP(S) URL\n")
            return 1

        path = process.args[1]

        # Determine path type
        is_http = path.startswith('http://') or path.startswith('https://')

        # Process path based on type
        if is_http:
            # HTTP(S) URL: use as-is, server will download it
            library_path = path
        else:
            # AGFS path: resolve relative paths and add agfs:// prefix
            # Resolve relative paths to absolute paths
            if not path.startswith('/'):
                # Relative path - resolve based on current working directory
                cwd = getattr(process, 'cwd', '/')
                path = os.path.normpath(os.path.join(cwd, path))
            library_path = f"agfs://{path}"

        try:
            # Load the plugin
            result = process.filesystem.client.load_plugin(library_path)
            plugin_name = result.get("plugin_name", "unknown")
            process.stdout.write(f"Loaded external plugin: {plugin_name}\n")
            process.stdout.write(f"  Source: {path}\n")
            return 0
        except Exception as e:
            error_msg = str(e)
            process.stderr.write(f"plugins load: {error_msg}\n")
            return 1

    elif subcommand == "unload":
        if len(process.args) < 2:
            process.stderr.write("Usage: plugins unload <library_path>\n")
            return 1

        library_path = process.args[1]

        try:
            process.filesystem.client.unload_plugin(library_path)
            process.stdout.write(f"Unloaded external plugin: {library_path}\n")
            return 0
        except Exception as e:
            error_msg = str(e)
            process.stderr.write(f"plugins unload: {error_msg}\n")
            return 1

    elif subcommand == "list":
        try:
            # Check for verbose flag
            verbose = '-v' in process.args[1:] or '--verbose' in process.args[1:]

            # Use new API to get detailed plugin information
            plugins_info = process.filesystem.client.get_plugins_info()

            # Separate builtin and external plugins
            builtin_plugins = [p for p in plugins_info if not p.get('is_external', False)]
            external_plugins = [p for p in plugins_info if p.get('is_external', False)]

            # Display builtin plugins
            if builtin_plugins:
                process.stdout.write(f"Builtin Plugins: ({len(builtin_plugins)})\n")
                for plugin in sorted(builtin_plugins, key=lambda x: x.get('name', '')):
                    plugin_name = plugin.get('name', 'unknown')
                    mounted_paths = plugin.get('mounted_paths', [])
                    config_params = plugin.get('config_params', [])

                    if mounted_paths:
                        mount_list = []
                        for mount in mounted_paths:
                            path = mount.get('path', '')
                            config = mount.get('config', {})
                            if config:
                                mount_list.append(f"{path} (with config)")
                            else:
                                mount_list.append(path)
                        process.stdout.write(f"  {plugin_name:20} -> {', '.join(mount_list)}\n")
                    else:
                        process.stdout.write(f"  {plugin_name:20} (not mounted)\n")

                    # Show config params if verbose and available
                    if verbose and config_params:
                        process.stdout.write(f"    Config parameters:\n")
                        for param in config_params:
                            req = "*" if param.get('required', False) else " "
                            name = param.get('name', '')
                            ptype = param.get('type', '')
                            default = param.get('default', '')
                            desc = param.get('description', '')
                            default_str = f" (default: {default})" if default else ""
                            process.stdout.write(f"      {req} {name:20} {ptype:10} {desc}{default_str}\n")

                process.stdout.write("\n")

            # Display external plugins
            if external_plugins:
                process.stdout.write(f"External Plugins: ({len(external_plugins)})\n")
                for plugin in sorted(external_plugins, key=lambda x: x.get('name', '')):
                    plugin_name = plugin.get('name', 'unknown')
                    library_path = plugin.get('library_path', '')
                    mounted_paths = plugin.get('mounted_paths', [])
                    config_params = plugin.get('config_params', [])

                    # Extract just the filename for display
                    filename = os.path.basename(library_path) if library_path else plugin_name
                    process.stdout.write(f"  {filename}\n")
                    process.stdout.write(f"    Plugin name: {plugin_name}\n")

                    if mounted_paths:
                        mount_list = []
                        for mount in mounted_paths:
                            path = mount.get('path', '')
                            config = mount.get('config', {})
                            if config:
                                mount_list.append(f"{path} (with config)")
                            else:
                                mount_list.append(path)
                        process.stdout.write(f"    Mounted at: {', '.join(mount_list)}\n")
                    else:
                        process.stdout.write(f"    (Not currently mounted)\n")

                    # Show config params if verbose and available
                    if verbose and config_params:
                        process.stdout.write(f"    Config parameters:\n")
                        for param in config_params:
                            req = "*" if param.get('required', False) else " "
                            name = param.get('name', '')
                            ptype = param.get('type', '')
                            default = param.get('default', '')
                            desc = param.get('description', '')
                            default_str = f" (default: {default})" if default else ""
                            process.stdout.write(f"      {req} {name:20} {ptype:10} {desc}{default_str}\n")
            else:
                process.stdout.write("No external plugins loaded\n")

            return 0
        except Exception as e:
            error_msg = str(e)
            process.stderr.write(f"plugins list: {error_msg}\n")
            return 1

    else:
        process.stderr.write(f"plugins: unknown subcommand: {subcommand}\n")
        process.stderr.write("\nUsage:\n")
        process.stderr.write("  plugins list                             - List all plugins\n")
        process.stderr.write("  plugins load <library_path|url>          - Load external plugin\n")
        process.stderr.write("  plugins unload <library_path>            - Unload external plugin\n")
        return 1


@command()
def cmd_rev(process: Process) -> int:
    """
    Reverse lines character-wise

    Usage: rev

    Examples:
        echo 'hello' | rev              # Output: olleh
        echo 'abc:def' | rev            # Output: fed:cba
        ls -l | rev | cut -d' ' -f1 | rev  # Extract filenames from ls -l
    """
    lines = process.stdin.readlines()

    for line in lines:
        # Handle both str and bytes
        if isinstance(line, bytes):
            line_str = line.decode('utf-8', errors='replace')
        else:
            line_str = line

        # Remove trailing newline, reverse, add newline back
        line_clean = line_str.rstrip('\n\r')
        reversed_line = line_clean[::-1]
        process.stdout.write(reversed_line + '\n')

    return 0


@command()
def cmd_cut(process: Process) -> int:
    """
    Cut out selected portions of each line

    Usage: cut [OPTIONS]

    Options:
        -f LIST     Select only these fields (comma-separated or range)
        -d DELIM    Use DELIM as field delimiter (default: TAB)
        -c LIST     Select only these characters (comma-separated or range)

    LIST can be:
        N       N'th field/character, counted from 1
        N-M     From N'th to M'th (inclusive)
        N-      From N'th to end of line
        -M      From first to M'th (inclusive)

    Examples:
        echo 'a:b:c:d' | cut -d: -f1        # Output: a
        echo 'a:b:c:d' | cut -d: -f2-3      # Output: b:c
        echo 'a:b:c:d' | cut -d: -f1,3      # Output: a:c
        echo 'hello world' | cut -c1-5      # Output: hello
        cat /etc/passwd | cut -d: -f1,3     # Get username and UID
    """
    # Parse options
    fields_str = None
    delimiter = '\t'
    chars_str = None

    args = process.args[:]

    i = 0
    while i < len(args):
        if args[i] == '-f' and i + 1 < len(args):
            fields_str = args[i + 1]
            i += 2
        elif args[i] == '-d' and i + 1 < len(args):
            delimiter = args[i + 1]
            i += 2
        elif args[i] == '-c' and i + 1 < len(args):
            chars_str = args[i + 1]
            i += 2
        elif args[i].startswith('-f'):
            # Handle -f1 format
            fields_str = args[i][2:]
            i += 1
        elif args[i].startswith('-d'):
            # Handle -d: format
            delimiter = args[i][2:]
            i += 1
        elif args[i].startswith('-c'):
            # Handle -c1-5 format
            chars_str = args[i][2:]
            i += 1
        else:
            process.stderr.write(f"cut: invalid option -- '{args[i]}'\n")
            return 1

    # Check that either -f or -c is specified (but not both)
    if fields_str and chars_str:
        process.stderr.write("cut: only one type of list may be specified\n")
        return 1

    if not fields_str and not chars_str:
        process.stderr.write("cut: you must specify a list of bytes, characters, or fields\n")
        process.stderr.write("Usage: cut -f LIST [-d DELIM] or cut -c LIST\n")
        return 1

    try:
        if fields_str:
            # Parse field list
            field_indices = _parse_cut_list(fields_str)
            return _cut_fields(process, field_indices, delimiter)
        else:
            # Parse character list
            char_indices = _parse_cut_list(chars_str)
            return _cut_chars(process, char_indices)

    except ValueError as e:
        process.stderr.write(f"cut: {e}\n")
        return 1


def _parse_cut_list(list_str: str) -> List:
    """
    Parse a cut list specification (e.g., "1,3,5-7,10-")
    Returns a list of (start, end) tuples representing ranges (1-indexed)
    """
    ranges = []

    for part in list_str.split(','):
        part = part.strip()

        if '-' in part and not part.startswith('-'):
            # Range like "5-7" or "5-"
            parts = part.split('-', 1)
            start_str = parts[0].strip()
            end_str = parts[1].strip() if parts[1] else None

            if not start_str:
                raise ValueError(f"invalid range: {part}")

            start = int(start_str)
            end = int(end_str) if end_str else None

            if start < 1:
                raise ValueError(f"fields and positions are numbered from 1")

            if end is not None and end < start:
                raise ValueError(f"invalid range: {part}")

            ranges.append((start, end))

        elif part.startswith('-'):
            # Range like "-5" (from 1 to 5)
            end_str = part[1:].strip()
            if not end_str:
                raise ValueError(f"invalid range: {part}")

            end = int(end_str)
            if end < 1:
                raise ValueError(f"fields and positions are numbered from 1")

            ranges.append((1, end))

        else:
            # Single number like "3"
            num = int(part)
            if num < 1:
                raise ValueError(f"fields and positions are numbered from 1")

            ranges.append((num, num))

    return ranges


def _cut_fields(process: Process, field_ranges: List, delimiter: str) -> int:
    """
    Cut fields from input lines based on field ranges
    """
    lines = process.stdin.readlines()

    for line in lines:
        # Handle both str and bytes
        if isinstance(line, bytes):
            line_str = line.decode('utf-8', errors='replace').rstrip('\n\r')
        else:
            line_str = line.rstrip('\n\r')

        # Split line by delimiter
        fields = line_str.split(delimiter)

        # Extract selected fields
        output_fields = []
        for start, end in field_ranges:
            if end is None:
                # Range like "3-" (from 3 to end)
                for i in range(start - 1, len(fields)):
                    if i < len(fields) and fields[i] not in output_fields:
                        output_fields.append((i, fields[i]))
            else:
                # Range like "3-5" or single field "3"
                for i in range(start - 1, end):
                    if i < len(fields) and fields[i] not in [f[1] for f in output_fields if f[0] == i]:
                        output_fields.append((i, fields[i]))

        # Sort by original field index to maintain order
        output_fields.sort(key=lambda x: x[0])

        # Output the selected fields
        if output_fields:
            output = delimiter.join([f[1] for f in output_fields]) + '\n'
            process.stdout.write(output)

    return 0


def _cut_chars(process: Process, char_ranges: List) -> int:
    """
    Cut characters from input lines based on character ranges
    """
    lines = process.stdin.readlines()

    for line in lines:
        # Handle both str and bytes
        if isinstance(line, bytes):
            line_str = line.decode('utf-8', errors='replace').rstrip('\n\r')
        else:
            line_str = line.rstrip('\n\r')

        # Extract selected characters
        output_chars = []
        for start, end in char_ranges:
            if end is None:
                # Range like "3-" (from 3 to end)
                for i in range(start - 1, len(line_str)):
                    if i < len(line_str):
                        output_chars.append((i, line_str[i]))
            else:
                # Range like "3-5" or single character "3"
                for i in range(start - 1, end):
                    if i < len(line_str):
                        output_chars.append((i, line_str[i]))

        # Sort by original character index to maintain order
        output_chars.sort(key=lambda x: x[0])

        # Remove duplicates while preserving order
        seen = set()
        unique_chars = []
        for idx, char in output_chars:
            if idx not in seen:
                seen.add(idx)
                unique_chars.append(char)

        # Output the selected characters
        if unique_chars:
            output = ''.join(unique_chars) + '\n'
            process.stdout.write(output)

    return 0


@command(needs_path_resolution=True)
def cmd_tree(process: Process) -> int:
    """
    List contents of directories in a tree-like format

    Usage: tree [OPTIONS] [path]

    Options:
        -L level    Descend only level directories deep
        -d          List directories only
        -a          Show all files (including hidden files starting with .)
        --noreport  Don't print file and directory count at the end

    Examples:
        tree                # Show tree of current directory
        tree /path/to/dir   # Show tree of specific directory
        tree -L 2           # Show tree with max depth of 2
        tree -d             # Show only directories
        tree -a             # Show all files including hidden ones
    """
    # Parse arguments
    max_depth = None
    dirs_only = False
    show_hidden = False
    show_report = True
    path = None

    args = process.args[:]
    i = 0
    while i < len(args):
        if args[i] == '-L' and i + 1 < len(args):
            try:
                max_depth = int(args[i + 1])
                if max_depth < 0:
                    process.stderr.write("tree: invalid level, must be >= 0\n")
                    return 1
                i += 2
                continue
            except ValueError:
                process.stderr.write(f"tree: invalid level '{args[i + 1]}'\n")
                return 1
        elif args[i] == '-d':
            dirs_only = True
            i += 1
        elif args[i] == '-a':
            show_hidden = True
            i += 1
        elif args[i] == '--noreport':
            show_report = False
            i += 1
        elif args[i].startswith('-'):
            # Handle combined flags
            if args[i] == '-L':
                process.stderr.write("tree: option requires an argument -- 'L'\n")
                return 1
            # Unknown option
            process.stderr.write(f"tree: invalid option -- '{args[i]}'\n")
            return 1
        else:
            # This is the path argument
            if path is not None:
                process.stderr.write("tree: too many arguments\n")
                return 1
            path = args[i]
            i += 1

    # Default to current working directory
    if path is None:
        path = getattr(process, 'cwd', '/')

    if not process.filesystem:
        process.stderr.write("tree: filesystem not available\n")
        return 1

    # Check if path exists
    try:
        info = process.filesystem.get_file_info(path)
        is_dir = info.get('isDir', False) or info.get('type') == 'directory'

        if not is_dir:
            process.stderr.write(f"tree: {path}: Not a directory\n")
            return 1
    except Exception as e:
        error_msg = str(e)
        if "No such file or directory" in error_msg or "not found" in error_msg.lower():
            process.stderr.write(f"tree: {path}: No such file or directory\n")
        else:
            process.stderr.write(f"tree: {path}: {error_msg}\n")
        return 1

    # Print the root path
    process.stdout.write(f"{path}\n".encode('utf-8'))

    # Track statistics
    stats = {'dirs': 0, 'files': 0}

    # Build and print the tree
    try:
        _print_tree(process, path, "", True, max_depth, 0, dirs_only, show_hidden, stats)
    except Exception as e:
        process.stderr.write(f"tree: error traversing {path}: {e}\n")
        return 1

    # Print report
    if show_report:
        if dirs_only:
            report = f"\n{stats['dirs']} directories\n"
        else:
            report = f"\n{stats['dirs']} directories, {stats['files']} files\n"
        process.stdout.write(report.encode('utf-8'))

    return 0


def _print_tree(process, path, prefix, is_last, max_depth, current_depth, dirs_only, show_hidden, stats):
    """
    Recursively print directory tree

    Args:
        process: Process object
        path: Current directory path
        prefix: Prefix string for tree drawing
        is_last: Whether this is the last item in the parent directory
        max_depth: Maximum depth to traverse (None for unlimited)
        current_depth: Current depth level
        dirs_only: Only show directories
        show_hidden: Show hidden files
        stats: Dictionary to track file/dir counts
    """
    # Check depth limit
    if max_depth is not None and current_depth >= max_depth:
        return

    try:
        # List directory contents
        entries = process.filesystem.list_directory(path)

        # Filter entries
        filtered_entries = []
        for entry in entries:
            name = entry.get('name', '')

            # Skip hidden files unless show_hidden is True
            if not show_hidden and name.startswith('.'):
                continue

            is_dir = entry.get('isDir', False) or entry.get('type') == 'directory'

            # Skip files if dirs_only is True
            if dirs_only and not is_dir:
                continue

            filtered_entries.append(entry)

        # Sort entries: directories first, then by name
        filtered_entries.sort(key=lambda e: (not (e.get('isDir', False) or e.get('type') == 'directory'), e.get('name', '')))

        # Process each entry
        for idx, entry in enumerate(filtered_entries):
            name = entry.get('name', '')
            is_dir = entry.get('isDir', False) or entry.get('type') == 'directory'
            is_last_entry = (idx == len(filtered_entries) - 1)

            # Update statistics
            if is_dir:
                stats['dirs'] += 1
            else:
                stats['files'] += 1

            # Determine the tree characters to use
            if is_last_entry:
                connector = "└── "
                extension = "    "
            else:
                connector = "├── "
                extension = "│   "

            # Format name with color
            if is_dir:
                # Blue color for directories
                display_name = f"\033[1;34m{name}/\033[0m"
            else:
                display_name = name

            # Print the entry
            line = f"{prefix}{connector}{display_name}\n"
            process.stdout.write(line.encode('utf-8'))

            # Recursively process subdirectories
            if is_dir:
                subdir_path = os.path.join(path, name)
                subdir_path = os.path.normpath(subdir_path)
                new_prefix = prefix + extension

                _print_tree(
                    process,
                    subdir_path,
                    new_prefix,
                    is_last_entry,
                    max_depth,
                    current_depth + 1,
                    dirs_only,
                    show_hidden,
                    stats
                )

    except Exception as e:
        # If we can't read a directory, print an error but continue
        error_msg = str(e)
        if "Permission denied" in error_msg:
            error_line = f"{prefix}[error opening dir]\n"
        else:
            error_line = f"{prefix}[error: {error_msg}]\n"
        process.stdout.write(error_line.encode('utf-8'))


@command(needs_path_resolution=True)
def cmd_mv(process: Process) -> int:
    """
    Move (rename) files and directories

    Usage: mv [OPTIONS] SOURCE DEST
           mv [OPTIONS] SOURCE... DIRECTORY

    Options:
        -i          Prompt before overwrite (interactive mode)
        -n          Do not overwrite an existing file
        -f          Force overwrite without prompting (default)

    Path formats:
        <agfs_path>      - AGFS path (default)
        local:<path>     - Local filesystem path

    Examples:
        mv file.txt newname.txt                    # Rename within AGFS
        mv file1.txt file2.txt dir/                # Move multiple files to directory
        mv local:file.txt /agfs/path/              # Move from local to AGFS
        mv /agfs/file.txt local:~/Downloads/       # Move from AGFS to local
        mv -i file.txt existing.txt                # Prompt before overwriting
        mv -n file.txt existing.txt                # Don't overwrite if exists
    """
    # Parse options
    interactive = False
    no_clobber = False
    force = True  # Default behavior
    args = process.args[:]
    sources = []

    i = 0
    while i < len(args):
        if args[i] == '-i':
            interactive = True
            force = False
            i += 1
        elif args[i] == '-n':
            no_clobber = True
            force = False
            i += 1
        elif args[i] == '-f':
            force = True
            interactive = False
            no_clobber = False
            i += 1
        elif args[i].startswith('-'):
            # Handle combined flags like -in
            for char in args[i][1:]:
                if char == 'i':
                    interactive = True
                    force = False
                elif char == 'n':
                    no_clobber = True
                    force = False
                elif char == 'f':
                    force = True
                    interactive = False
                    no_clobber = False
                else:
                    process.stderr.write(f"mv: invalid option -- '{char}'\n")
                    return 1
            i += 1
        else:
            sources.append(args[i])
            i += 1

    # Need at least source and dest
    if len(sources) < 2:
        process.stderr.write("mv: missing file operand\n")
        process.stderr.write("Usage: mv [OPTIONS] SOURCE DEST\n")
        process.stderr.write("       mv [OPTIONS] SOURCE... DIRECTORY\n")
        return 1

    dest = sources.pop()

    # Parse source and dest to determine if local or AGFS
    source_paths = []
    for src in sources:
        is_local = src.startswith('local:')
        path = src[6:] if is_local else src
        source_paths.append({'path': path, 'is_local': is_local, 'original': src})

    dest_is_local = dest.startswith('local:')
    dest_path = dest[6:] if dest_is_local else dest

    # Resolve AGFS paths relative to cwd
    if not dest_is_local and not dest_path.startswith('/'):
        dest_path = os.path.join(process.cwd, dest_path)
        dest_path = os.path.normpath(dest_path)

    for src_info in source_paths:
        if not src_info['is_local'] and not src_info['path'].startswith('/'):
            src_info['path'] = os.path.join(process.cwd, src_info['path'])
            src_info['path'] = os.path.normpath(src_info['path'])

    # Check if moving multiple files
    if len(source_paths) > 1:
        # Multiple sources - dest must be a directory
        if dest_is_local:
            if not os.path.isdir(dest_path):
                process.stderr.write(f"mv: target '{dest}' is not a directory\n")
                return 1
        else:
            try:
                dest_info = process.filesystem.get_file_info(dest_path)
                if not (dest_info.get('isDir', False) or dest_info.get('type') == 'directory'):
                    process.stderr.write(f"mv: target '{dest}' is not a directory\n")
                    return 1
            except:
                process.stderr.write(f"mv: target '{dest}' is not a directory\n")
                return 1

        # Move each source to dest directory
        for src_info in source_paths:
            result = _mv_single(
                process, src_info['path'], dest_path,
                src_info['is_local'], dest_is_local,
                interactive, no_clobber, force,
                src_info['original'], dest
            )
            if result != 0:
                return result
    else:
        # Single source
        src_info = source_paths[0]
        return _mv_single(
            process, src_info['path'], dest_path,
            src_info['is_local'], dest_is_local,
            interactive, no_clobber, force,
            src_info['original'], dest
        )

    return 0


def _mv_single(process, source_path, dest_path, source_is_local, dest_is_local,
               interactive, no_clobber, force, source_display, dest_display):
    """
    Move a single file or directory

    Returns 0 on success, non-zero on failure
    """
    import sys

    # Determine final destination path
    final_dest = dest_path

    # Check if destination exists and is a directory
    dest_exists = False
    dest_is_dir = False

    if dest_is_local:
        dest_exists = os.path.exists(dest_path)
        dest_is_dir = os.path.isdir(dest_path)
    else:
        try:
            dest_info = process.filesystem.get_file_info(dest_path)
            dest_exists = True
            dest_is_dir = dest_info.get('isDir', False) or dest_info.get('type') == 'directory'
        except:
            dest_exists = False
            dest_is_dir = False

    # If dest is a directory, append source filename
    if dest_exists and dest_is_dir:
        source_basename = os.path.basename(source_path)
        if dest_is_local:
            final_dest = os.path.join(dest_path, source_basename)
        else:
            final_dest = os.path.join(dest_path, source_basename)
            final_dest = os.path.normpath(final_dest)

    # Check if final destination exists
    final_dest_exists = False
    if dest_is_local:
        final_dest_exists = os.path.exists(final_dest)
    else:
        try:
            process.filesystem.get_file_info(final_dest)
            final_dest_exists = True
        except:
            final_dest_exists = False

    # Handle overwrite protection
    if final_dest_exists:
        if no_clobber:
            # Don't overwrite, silently skip
            return 0

        if interactive:
            # Prompt user
            process.stderr.write(f"mv: overwrite '{final_dest}'? (y/n) ")
            process.stderr.flush()
            try:
                response = sys.stdin.readline().strip().lower()
                if response not in ['y', 'yes']:
                    return 0
            except:
                return 0

    # Perform the move operation based on source and dest types
    try:
        if source_is_local and dest_is_local:
            # Validate local paths to prevent path traversal
            try:
                source_path = _validate_local_path(source_path)
                final_dest = _validate_local_path(final_dest)
            except ValueError as e:
                process.stderr.write(f"mv: {e}\n")
                return 1
            # Local to local - use os.rename or shutil.move
            import shutil
            shutil.move(source_path, final_dest)
            return 0

        elif source_is_local and not dest_is_local:
            # Validate local source path to prevent path traversal
            try:
                source_path = _validate_local_path(source_path)
            except ValueError as e:
                process.stderr.write(f"mv: {e}\n")
                return 1
            # Local to AGFS - upload then delete local
            if os.path.isdir(source_path):
                # Move directory
                result = _upload_dir(process, source_path, final_dest)
                if result == 0:
                    # Delete local directory after successful upload
                    import shutil
                    shutil.rmtree(source_path)
                return result
            else:
                # Move file
                with open(source_path, 'rb') as f:
                    data = f.read()
                    process.filesystem.write_file(final_dest, data, append=False)
                # Delete local file after successful upload
                os.remove(source_path)
                return 0

        elif not source_is_local and dest_is_local:
            # Validate local destination path to prevent path traversal
            try:
                final_dest = _validate_local_path(final_dest)
            except ValueError as e:
                process.stderr.write(f"mv: {e}\n")
                return 1
            # AGFS to local - download then delete AGFS
            source_info = process.filesystem.get_file_info(source_path)
            is_dir = source_info.get('isDir', False) or source_info.get('type') == 'directory'

            if is_dir:
                # Move directory
                result = _download_dir(process, source_path, final_dest)
                if result == 0:
                    # Delete AGFS directory after successful download
                    process.filesystem.client.rm(source_path, recursive=True)
                return result
            else:
                # Move file
                stream = process.filesystem.read_file(source_path, stream=True)
                with open(final_dest, 'wb') as f:
                    for chunk in stream:
                        if chunk:
                            f.write(chunk)
                # Delete AGFS file after successful download
                process.filesystem.client.rm(source_path, recursive=False)
                return 0

        else:
            # AGFS to AGFS - use rename if supported, otherwise copy + delete
            # Check if source exists
            source_info = process.filesystem.get_file_info(source_path)

            # Try to use AGFS rename/move if available
            if hasattr(process.filesystem.client, 'rename'):
                process.filesystem.client.rename(source_path, final_dest)
            elif hasattr(process.filesystem.client, 'mv'):
                process.filesystem.client.mv(source_path, final_dest)
            else:
                # Fallback: copy then delete
                is_dir = source_info.get('isDir', False) or source_info.get('type') == 'directory'

                if is_dir:
                    # Copy directory recursively
                    result = _cp_agfs_dir(process, source_path, final_dest)
                    if result != 0:
                        return result
                    # Delete source directory
                    process.filesystem.client.rm(source_path, recursive=True)
                else:
                    # Copy file
                    data = process.filesystem.read_file(source_path, stream=False)
                    process.filesystem.write_file(final_dest, data, append=False)
                    # Delete source file
                    process.filesystem.client.rm(source_path, recursive=False)

            return 0

    except FileNotFoundError:
        process.stderr.write(f"mv: cannot stat '{source_display}': No such file or directory\n")
        return 1
    except PermissionError:
        process.stderr.write(f"mv: cannot move '{source_display}': Permission denied\n")
        return 1
    except Exception as e:
        error_msg = str(e)
        if "No such file or directory" in error_msg or "not found" in error_msg.lower():
            process.stderr.write(f"mv: cannot stat '{source_display}': No such file or directory\n")
        else:
            process.stderr.write(f"mv: cannot move '{source_display}' to '{dest_display}': {error_msg}\n")
        return 1


@command()
def cmd_basename(process: Process) -> int:
    """
    Extract filename from path
    Usage: basename PATH [SUFFIX]

    Examples:
        basename /local/path/to/file.txt         # file.txt
        basename /local/path/to/file.txt .txt    # file
    """
    if not process.args:
        process.stderr.write("basename: missing operand\n")
        process.stderr.write("Usage: basename PATH [SUFFIX]\n")
        return 1

    path = process.args[0]
    suffix = process.args[1] if len(process.args) > 1 else None

    # Extract basename
    basename = os.path.basename(path)

    # Remove suffix if provided
    if suffix and basename.endswith(suffix):
        basename = basename[:-len(suffix)]

    process.stdout.write(basename + '\n')
    return 0


@command()
def cmd_dirname(process: Process) -> int:
    """
    Extract directory from path
    Usage: dirname PATH

    Examples:
        dirname /local/path/to/file.txt    # /local/path/to
        dirname /local/file.txt             # /local
        dirname file.txt                    # .
    """
    if not process.args:
        process.stderr.write("dirname: missing operand\n")
        process.stderr.write("Usage: dirname PATH\n")
        return 1

    path = process.args[0]

    # Extract dirname
    dirname = os.path.dirname(path)

    # If dirname is empty, use '.'
    if not dirname:
        dirname = '.'

    process.stdout.write(dirname + '\n')
    return 0


@command()
def cmd_help(process: Process) -> int:
    """
    Display help information for built-in commands

    Usage: ? [command]
           help [command]

    Without arguments: List all available commands
    With command name: Show detailed help for that command

    Examples:
        ?                # List all commands
        ? ls             # Show help for ls command
        help grep        # Show help for grep command
    """
    if not process.args:
        # Show all commands
        process.stdout.write("Available built-in commands:\n\n")

        # Get all commands from BUILTINS, sorted alphabetically
        # Exclude '[' as it's an alias for 'test'
        commands = sorted([cmd for cmd in BUILTINS.keys() if cmd != '['])

        # Group commands by category for better organization
        categories = {
            'File Operations': ['ls', 'tree', 'cat', 'mkdir', 'rm', 'mv', 'cp', 'stat', 'upload', 'download'],
            'Text Processing': ['grep', 'wc', 'head', 'tail', 'sort', 'uniq', 'tr', 'rev', 'cut', 'jq'],
            'System': ['pwd', 'cd', 'echo', 'env', 'export', 'unset', 'sleep'],
            'Testing': ['test'],
            'AGFS Management': ['mount', 'plugins'],
        }

        # Display categorized commands
        for category, cmd_list in categories.items():
            category_cmds = [cmd for cmd in cmd_list if cmd in commands]
            if category_cmds:
                process.stdout.write(f"\033[1;36m{category}:\033[0m\n")
                for cmd in category_cmds:
                    func = BUILTINS[cmd]
                    # Get first line of docstring as short description
                    if func.__doc__:
                        lines = func.__doc__.strip().split('\n')
                        # Find first non-empty line after initial whitespace
                        short_desc = ""
                        for line in lines:
                            line = line.strip()
                            if line and not line.startswith('Usage:'):
                                short_desc = line
                                break
                        process.stdout.write(f"  \033[1;32m{cmd:12}\033[0m {short_desc}\n")
                    else:
                        process.stdout.write(f"  \033[1;32m{cmd:12}\033[0m\n")
                process.stdout.write("\n")

        # Show uncategorized commands if any
        categorized = set()
        for cmd_list in categories.values():
            categorized.update(cmd_list)
        uncategorized = [cmd for cmd in commands if cmd not in categorized]
        if uncategorized:
            process.stdout.write(f"\033[1;36mOther:\033[0m\n")
            for cmd in uncategorized:
                func = BUILTINS[cmd]
                if func.__doc__:
                    lines = func.__doc__.strip().split('\n')
                    short_desc = ""
                    for line in lines:
                        line = line.strip()
                        if line and not line.startswith('Usage:'):
                            short_desc = line
                            break
                    process.stdout.write(f"  \033[1;32m{cmd:12}\033[0m {short_desc}\n")
                else:
                    process.stdout.write(f"  \033[1;32m{cmd:12}\033[0m\n")
            process.stdout.write("\n")

        process.stdout.write("Type '? <command>' for detailed help on a specific command.\n")
        return 0

    # Show help for specific command
    command_name = process.args[0]

    if command_name not in BUILTINS:
        process.stderr.write(f"?: unknown command '{command_name}'\n")
        process.stderr.write("Type '?' to see all available commands.\n")
        return 1

    func = BUILTINS[command_name]

    if not func.__doc__:
        process.stdout.write(f"No help available for '{command_name}'\n")
        return 0

    # Display the full docstring
    process.stdout.write(f"\033[1;36mCommand: {command_name}\033[0m\n\n")

    # Format the docstring nicely
    docstring = func.__doc__.strip()

    # Process the docstring to add colors
    lines = docstring.split('\n')
    for line in lines:
        stripped = line.strip()

        # Highlight section headers (Usage:, Options:, Examples:, etc.)
        if stripped.endswith(':') and len(stripped.split()) == 1:
            process.stdout.write(f"\033[1;33m{stripped}\033[0m\n")
        # Highlight option flags
        elif stripped.startswith('-'):
            # Split option and description
            parts = stripped.split(None, 1)
            if len(parts) == 2:
                option, desc = parts
                process.stdout.write(f"  \033[1;32m{option:12}\033[0m {desc}\n")
            else:
                process.stdout.write(f"  \033[1;32m{stripped}\033[0m\n")
        # Regular line
        else:
            process.stdout.write(f"{line}\n")

    process.stdout.write("\n")
    return 0


@command()
def cmd_mount(process: Process) -> int:
    """
    Mount a plugin dynamically or list mounted filesystems

    Usage: mount [<fstype> <path> [key=value ...]]

    Without arguments: List all mounted filesystems
    With arguments: Mount a new filesystem

    Examples:
        mount                    # List all mounted filesystems
        mount memfs /test/mem
        mount sqlfs /test/db backend=sqlite db_path=/tmp/test.db
        mount s3fs /test/s3 bucket=my-bucket region=us-west-1 access_key_id=xxx secret_access_key=yyy
    """
    if not process.filesystem:
        process.stderr.write("mount: filesystem not available\n")
        return 1

    # No arguments - list mounted filesystems
    if len(process.args) == 0:
        try:
            mounts_list = process.filesystem.client.mounts()

            if not mounts_list:
                process.stdout.write("No plugins mounted\n")
                return 0

            # Print mounts in Unix mount style: <fstype> on <mountpoint> (options...)
            for mount in mounts_list:
                path = mount.get("path", "")
                plugin = mount.get("pluginName", "")
                config = mount.get("config", {})

                # Build options string from config
                options = []
                for key, value in config.items():
                    # Hide sensitive keys
                    if key in ["secret_access_key", "password", "token"]:
                        options.append(f"{key}=***")
                    else:
                        # Convert value to string, truncate if too long
                        value_str = str(value)
                        if len(value_str) > 50:
                            value_str = value_str[:47] + "..."
                        options.append(f"{key}={value_str}")

                # Format output line
                if options:
                    options_str = ", ".join(options)
                    process.stdout.write(f"{plugin} on {path} (plugin: {plugin}, {options_str})\n")
                else:
                    process.stdout.write(f"{plugin} on {path} (plugin: {plugin})\n")

            return 0
        except Exception as e:
            error_msg = str(e)
            process.stderr.write(f"mount: {error_msg}\n")
            return 1

    # With arguments - mount a new filesystem
    if len(process.args) < 2:
        process.stderr.write("mount: missing operands\n")
        process.stderr.write("Usage: mount <fstype> <path> [key=value ...]\n")
        process.stderr.write("\nExamples:\n")
        process.stderr.write("  mount memfs /test/mem\n")
        process.stderr.write("  mount sqlfs /test/db backend=sqlite db_path=/tmp/test.db\n")
        process.stderr.write("  mount s3fs /test/s3 bucket=my-bucket region=us-west-1\n")
        return 1

    fstype = process.args[0]
    path = process.args[1]
    config_args = process.args[2:] if len(process.args) > 2 else []

    # Parse key=value config arguments
    config = {}
    for arg in config_args:
        if '=' in arg:
            key, value = arg.split('=', 1)
            config[key.strip()] = value.strip()
        else:
            process.stderr.write(f"mount: invalid config argument: {arg}\n")
            process.stderr.write("Config arguments must be in key=value format\n")
            return 1

    try:
        # Use AGFS client to mount the plugin
        process.filesystem.client.mount(fstype, path, config)
        process.stdout.write(f"Mounted {fstype} at {path}\n")
        return 0
    except Exception as e:
        error_msg = str(e)
        process.stderr.write(f"mount: {error_msg}\n")
        return 1


@command()
def cmd_date(process: Process) -> int:
    """
    Display current date and time (pure Python implementation)

    Usage: date [+FORMAT]

    Examples:
        date                          # Wed Dec  6 10:23:45 PST 2025
        date "+%Y-%m-%d"              # 2025-12-06
        date "+%Y-%m-%d %H:%M:%S"     # 2025-12-06 10:23:45
        date "+%H:%M:%S"              # 10:23:45

    Format directives:
        %Y - Year with century (2025)
        %y - Year without century (25)
        %m - Month (01-12)
        %B - Full month name (December)
        %b - Abbreviated month name (Dec)
        %d - Day of month (01-31)
        %e - Day of month, space-padded ( 1-31)
        %A - Full weekday name (Wednesday)
        %a - Abbreviated weekday name (Wed)
        %H - Hour (00-23)
        %I - Hour (01-12)
        %M - Minute (00-59)
        %S - Second (00-59)
        %p - AM/PM
        %Z - Timezone name
        %z - Timezone offset (+0800)
    """
    try:
        now = datetime.datetime.now()

        if len(process.args) == 0:
            # Default format: "Wed Dec  6 10:23:45 PST 2025"
            # Note: %Z might be empty on some systems, %z gives offset
            formatted = now.strftime("%a %b %e %H:%M:%S %Z %Y")
            # Clean up double spaces that might occur
            formatted = ' '.join(formatted.split())
        elif len(process.args) == 1:
            format_str = process.args[0]

            # Remove leading '+' if present (like date +"%Y-%m-%d")
            if format_str.startswith('+'):
                format_str = format_str[1:]

            # Remove quotes if present
            format_str = format_str.strip('"').strip("'")

            # Apply the format
            formatted = now.strftime(format_str)
        else:
            process.stderr.write(b"date: too many arguments\n")
            process.stderr.write(b"Usage: date [+FORMAT]\n")
            return 1

        # Write output
        process.stdout.write(formatted.encode('utf-8'))
        process.stdout.write(b'\n')

        return 0

    except Exception as e:
        process.stderr.write(f"date: error: {str(e)}\n".encode('utf-8'))
        return 1


@command()
def cmd_exit(process: Process) -> int:
    """
    Exit the script with an optional exit code

    Usage: exit [n]

    Exit with status n (defaults to 0).
    In a script, exits the entire script.
    In interactive mode, exits the shell.

    Examples:
        exit        # Exit with status 0
        exit 1      # Exit with status 1
        exit $?     # Exit with last command's exit code
    """
    import sys

    exit_code = 0
    if process.args:
        try:
            exit_code = int(process.args[0])
        except ValueError:
            process.stderr.write(f"exit: {process.args[0]}: numeric argument required\n")
            exit_code = 2

    # Exit by raising SystemExit
    sys.exit(exit_code)


@command()
def cmd_break(process: Process) -> int:
    """
    Break out of a for loop

    Usage: break

    Exit from the innermost for loop. Can only be used inside a for loop.

    Examples:
        for i in 1 2 3 4 5; do
            if test $i -eq 3; then
                break
            fi
            echo $i
        done
        # Output: 1, 2 (stops at 3)
    """
    # Return special exit code to signal break
    # This will be caught by execute_for_loop
    return EXIT_CODE_BREAK


@command()
def cmd_continue(process: Process) -> int:
    """
    Continue to next iteration of a for loop

    Usage: continue

    Skip the rest of the current loop iteration and continue with the next one.
    Can only be used inside a for loop.

    Examples:
        for i in 1 2 3 4 5; do
            if test $i -eq 3; then
                continue
            fi
            echo $i
        done
        # Output: 1, 2, 4, 5 (skips 3)
    """
    # Return special exit code to signal continue
    # This will be caught by execute_for_loop
    return EXIT_CODE_CONTINUE


@command()
def cmd_llm(process: Process) -> int:
    """
    Interact with LLM models using the llm library

    Usage: llm [OPTIONS] [PROMPT]
           echo "text" | llm [OPTIONS]
           cat files | llm [OPTIONS] [PROMPT]
           cat image.jpg | llm [OPTIONS] [PROMPT]

    Options:
        -m MODEL    Specify the model to use (default: gpt-4o-mini)
        -s SYSTEM   System prompt
        -k KEY      API key (overrides config/env)
        -c CONFIG   Path to config file (default: /etc/llm.yaml)

    Configuration:
        The command reads configuration from:
        1. Environment variables (e.g., OPENAI_API_KEY, ANTHROPIC_API_KEY)
        2. Config file on AGFS (default: /etc/llm.yaml)
        3. Command-line arguments (-k option)

    Config file format (YAML):
        model: gpt-4o-mini
        api_key: sk-...
        system: You are a helpful assistant

    Image Support:
        Automatically detects image input (JPEG, PNG, GIF, WebP, BMP) from stdin
        and uses vision-capable models for image analysis.

    Examples:
        # Text prompts
        llm "What is 2+2?"
        echo "Hello world" | llm
        cat *.txt | llm "summarize these files"
        echo "Python code" | llm "translate to JavaScript"

        # Image analysis
        cat photo.jpg | llm "What's in this image?"
        cat screenshot.png | llm "Describe this screenshot in detail"
        cat diagram.png | llm

        # Advanced usage
        llm -m claude-3-5-sonnet-20241022 "Explain quantum computing"
        llm -s "You are a helpful assistant" "How do I install Python?"
    """
    import sys

    try:
        import llm
    except ImportError:
        process.stderr.write(b"llm: llm library not installed. Run: pip install llm\n")
        return 1

    # Parse arguments
    model_name = None
    system_prompt = None
    api_key = None
    config_path = "/etc/llm.yaml"
    prompt_parts = []

    i = 0
    while i < len(process.args):
        arg = process.args[i]
        if arg == '-m' and i + 1 < len(process.args):
            model_name = process.args[i + 1]
            i += 2
        elif arg == '-s' and i + 1 < len(process.args):
            system_prompt = process.args[i + 1]
            i += 2
        elif arg == '-k' and i + 1 < len(process.args):
            api_key = process.args[i + 1]
            i += 2
        elif arg == '-c' and i + 1 < len(process.args):
            config_path = process.args[i + 1]
            i += 2
        else:
            prompt_parts.append(arg)
            i += 1

    # Load configuration from file if it exists
    config = {}
    try:
        if process.filesystem:
            config_content = process.filesystem.read_file(config_path)
            if config_content:
                try:
                    import yaml
                    config = yaml.safe_load(config_content.decode('utf-8'))
                    if not isinstance(config, dict):
                        config = {}
                except ImportError:
                    # If PyYAML not available, try simple key=value parsing
                    config_text = config_content.decode('utf-8')
                    config = {}
                    for line in config_text.strip().split('\n'):
                        line = line.strip()
                        if line and not line.startswith('#') and ':' in line:
                            key, value = line.split(':', 1)
                            config[key.strip()] = value.strip()
                except Exception:
                    pass  # Ignore config parse errors
    except Exception:
        pass  # Config file doesn't exist or can't be read

    # Set defaults from config or hardcoded
    if not model_name:
        model_name = config.get('model', 'gpt-4o-mini')
    if not system_prompt:
        system_prompt = config.get('system')
    if not api_key:
        api_key = config.get('api_key')

    # Helper function to detect if binary data is an image
    def is_image(data):
        """Detect if binary data is an image by checking magic numbers"""
        if not data or len(data) < 8:
            return False
        # Check common image formats
        if data.startswith(b'\xFF\xD8\xFF'):  # JPEG
            return True
        if data.startswith(b'\x89PNG\r\n\x1a\n'):  # PNG
            return True
        if data.startswith(b'GIF87a') or data.startswith(b'GIF89a'):  # GIF
            return True
        if data.startswith(b'RIFF') and data[8:12] == b'WEBP':  # WebP
            return True
        if data.startswith(b'BM'):  # BMP
            return True
        return False

    # Get stdin content if available (keep as binary first)
    stdin_binary = None
    stdin_text = None
    # Use read() instead of get_value() to properly support streaming pipelines
    stdin_binary = process.stdin.read()
    if not stdin_binary:
        # Try to read from real stdin (but don't block if not available)
        try:
            import select
            if select.select([sys.stdin], [], [], 0.0)[0]:
                stdin_binary = sys.stdin.buffer.read()
        except Exception:
            pass  # No stdin available

    # Check if stdin is an image
    is_stdin_image = False
    if stdin_binary:
        is_stdin_image = is_image(stdin_binary)
        if not is_stdin_image:
            # Try to decode as text
            try:
                stdin_text = stdin_binary.decode('utf-8').strip()
            except UnicodeDecodeError:
                # Binary data but not an image we recognize
                process.stderr.write(b"llm: stdin contains binary data that is not a recognized image format\n")
                return 1

    # Get prompt from args
    prompt_text = None
    if prompt_parts:
        prompt_text = ' '.join(prompt_parts)

    # Determine the final prompt and attachments
    attachments = []
    if is_stdin_image:
        # Image input: use as attachment
        attachments.append(llm.Attachment(content=stdin_binary))
        if prompt_text:
            full_prompt = prompt_text
        else:
            full_prompt = "Describe this image"
    elif stdin_text and prompt_text:
        # Both text stdin and prompt: stdin is context, prompt is the question/instruction
        full_prompt = f"{stdin_text}\n\n===\n\n{prompt_text}"
    elif stdin_text:
        # Only text stdin: use it as the prompt
        full_prompt = stdin_text
    elif prompt_text:
        # Only prompt: use it as-is
        full_prompt = prompt_text
    else:
        # Neither: error
        process.stderr.write(b"llm: no prompt provided\n")
        return 1

    # Get the model
    try:
        model = llm.get_model(model_name)
    except Exception as e:
        error_msg = f"llm: failed to get model '{model_name}': {str(e)}\n"
        process.stderr.write(error_msg.encode('utf-8'))
        return 1

    # Prepare prompt kwargs
    prompt_kwargs = {}
    if system_prompt:
        prompt_kwargs['system'] = system_prompt
    if api_key:
        prompt_kwargs['key'] = api_key
    if attachments:
        prompt_kwargs['attachments'] = attachments

    # Execute the prompt
    try:
        response = model.prompt(full_prompt, **prompt_kwargs)
        output = response.text()
        process.stdout.write(output.encode('utf-8'))
        if not output.endswith('\n'):
            process.stdout.write(b'\n')
        return 0
    except Exception as e:
        error_msg = f"llm: error: {str(e)}\n"
        process.stderr.write(error_msg.encode('utf-8'))
        return 1


@command()
def cmd_true(process: Process) -> int:
    """
    Return success (exit code 0)

    Usage: true

    Always returns 0 (success). Useful in scripts and conditionals.
    """
    return 0


@command()
def cmd_false(process: Process) -> int:
    """
    Return failure (exit code 1)

    Usage: false

    Always returns 1 (failure). Useful in scripts and conditionals.
    """
    return 1


@command()
def cmd_local(process: Process) -> int:
    """
    Declare local variables (only valid within functions)

    Usage: local VAR=value [VAR2=value2 ...]

    Examples:
        local name="Alice"
        local count=0
        local path=/tmp/data
    """
    # Check if we have any local scopes (we're inside a function)
    # Note: This check needs to be done via env since we don't have direct access to shell
    # We'll use a special marker in env to track function depth
    if not process.env.get('_function_depth'):
        process.stderr.write("local: can only be used in a function\n")
        return 1

    if not process.args:
        process.stderr.write("local: usage: local VAR=value [VAR2=value2 ...]\n")
        return 2

    # Process each variable assignment
    for arg in process.args:
        if '=' not in arg:
            process.stderr.write(f"local: {arg}: not a valid identifier\n")
            return 1

        parts = arg.split('=', 1)
        var_name = parts[0].strip()
        var_value = parts[1] if len(parts) > 1 else ''

        # Validate variable name
        if not var_name or not var_name.replace('_', '').isalnum():
            process.stderr.write(f"local: {var_name}: not a valid identifier\n")
            return 1

        # Remove outer quotes if present
        if len(var_value) >= 2:
            if (var_value[0] == '"' and var_value[-1] == '"') or \
               (var_value[0] == "'" and var_value[-1] == "'"):
                var_value = var_value[1:-1]

        # Mark this variable as local by using a special prefix in env
        # This is a workaround since we don't have direct access to shell.local_scopes
        process.env[f'_local_{var_name}'] = var_value

    return 0


@command()
def cmd_return(process: Process) -> int:
    """
    Return from a function with an optional exit status

    Usage: return [n]

    Examples:
        return          # Return with status 0
        return 1        # Return with status 1
        return $?       # Return with last command's status
    """
    # Parse exit code
    exit_code = 0
    if process.args:
        try:
            exit_code = int(process.args[0])
        except ValueError:
            process.stderr.write(f"return: {process.args[0]}: numeric argument required\n")
            return 2

    # Store return value in env for shell to retrieve
    process.env['_return_value'] = str(exit_code)

    # Return special code to signal return statement
    return EXIT_CODE_RETURN


# Registry of built-in commands (NOT YET MIGRATED)
# These commands are still in this file and haven't been moved to the commands/ directory
_OLD_BUILTINS = {
    # Commands still in builtins.py:
    'cat': cmd_cat,
    'grep': cmd_grep,
    'head': cmd_head,
    'tail': cmd_tail,
    'tee': cmd_tee,
    'sort': cmd_sort,
    'uniq': cmd_uniq,
    'tr': cmd_tr,
    'cut': cmd_cut,
    'ls': cmd_ls,
    'tree': cmd_tree,
    'cd': cmd_cd,
    'mkdir': cmd_mkdir,
    'touch': cmd_touch,
    'rm': cmd_rm,
    'mv': cmd_mv,
    'export': cmd_export,
    'env': cmd_env,
    'unset': cmd_unset,
    'test': cmd_test,
    '[': cmd_test,  # [ is an alias for test
    'stat': cmd_stat,
    'jq': cmd_jq,
    'llm': cmd_llm,
    'upload': cmd_upload,
    'download': cmd_download,
    'cp': cmd_cp,
    'sleep': cmd_sleep,
    'plugins': cmd_plugins,
    'mount': cmd_mount,
    'date': cmd_date,
    'exit': cmd_exit,
    'break': cmd_break,
    'continue': cmd_continue,
    'local': cmd_local,
    'return': cmd_return,
    '?': cmd_help,
    'help': cmd_help,
}

# Load all commands from the new commands/ directory
# These commands have been migrated to individual files
from .commands import load_all_commands, BUILTINS as NEW_COMMANDS

# Load all command modules to populate the new registry
load_all_commands()

# Combine old and new commands for backward compatibility
# New commands take precedence if there's a duplicate
BUILTINS = {**_OLD_BUILTINS, **NEW_COMMANDS}


def get_builtin(command: str):
    """Get a built-in command executor"""
    return BUILTINS.get(command)
