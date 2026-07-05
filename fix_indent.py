import pathlib

cli = pathlib.Path("src/archivist/cli.py")
lines = cli.read_text(encoding="utf-8").splitlines(keepends=True)

new_block = [
    '    for i, hit in enumerate(hits, 1):\n',
    '        payload = hit.payload or {}\n',
    '        content = payload.get("content", "")\n',
    '        snippet = extract_snippet(content, query)\n',
    '        filepath = payload.get("filepath", "unknown")\n',
    '        source = Path(filepath).name if filepath != "unknown" else "unknown"\n',
    '        score = round(hit.score or 0.0, 4)\n',
    '\n',
    '        if json_output:\n',
    '            escaped = snippet.replace(\'"\', \'\\\"\').replace("\\n", "\\\\n")\n',
    '            typer.echo(\n',
    '                typer.style(\n',
    '                    f\'{{"rank": {i}, "score": {score}, "filepath": "{filepath}", "source": "{source}", "snippet": "{escaped}"}}\'\n',
    '                )\n',
    '            )\n',
    '        else:\n',
    '            console.print(f"\\n[bold cyan][{i}][/bold cyan] [blue]{filepath}[/blue]")\n',
    '            console.print(f"[dim]Source: {source}  |  Match: score={score}[/dim]")\n',
    '            console.print(snippet)\n',
]

lines[96:115] = new_block
cli.write_text("".join(lines), encoding="utf-8")
print("Fixed search function indentation")
