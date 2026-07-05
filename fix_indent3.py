import pathlib, re

cli = pathlib.Path("src/archivist/cli.py")
lines = cli.read_text(encoding="utf-8").splitlines(keepends=True)

# Find the clear function and fix indentation from 2-space to 4-space
in_clear = False
new_lines = []
for line in lines:
    if 'def clear(' in line:
        in_clear = True
    if in_clear:
        stripped = line.lstrip()
        if stripped == '\n' or stripped == '' or (not line.startswith(' ') and not line.startswith('\t') and 'def ' not in stripped and '@app' not in stripped and '"""' not in stripped):
            # End of clear function
            if 'console.print' in line or 'os.remove' in line or 'client.' in line or 'import' in line or 'raise' in line:
                # Still in clear body, fix indentation
                old_spaces = len(line) - len(line.lstrip())
                new_lines.append(' ' * (old_spaces + 2) + stripped)
                continue
            in_clear = False
        # Fix 2-space indent to 4-space indent inside clear function
        old_spaces = len(line) - len(line.lstrip())
        if 0 < old_spaces <= 6 and in_clear:
            new_lines.append(' ' * (old_spaces + 2) + stripped)
            continue
    new_lines.append(line)

cli.write_text("".join(new_lines), encoding="utf-8")
print("Done")
