import pathlib

cli = pathlib.Path("src/archivist/cli.py")
content = cli.read_text(encoding="utf-8")

old_clear = """@app.command()
def clear(
  confirm: bool = typer.Option(False, "--confirm", prompt="This will delete ALL indexed data. Continue?"),
):
  \"\"\"Delete all vectors and reset the tracker. Use with caution.\"\"\"
  if not confirm:
    raise typer.Abort()
  from qdrant_client import QdrantClient
  client = QdrantClient(url=str(settings.qdrant_url), api_key=settings.qdrant_api_key)
  client.delete_collection(settings.qdrant_collection)
  client.close()
  import os
  if os.path.exists(settings.tracker_db):
    os.remove(settings.tracker_db)
  console.print("[green]\\u2713 Database and tracker cleared.[/green]")"""

new_clear = """@app.command()
def clear(
    confirm: bool = typer.Option(False, "--confirm", prompt="This will delete ALL indexed data. Continue?"),
):
    \"\"\"Delete all vectors and reset the tracker. Use with caution.\"\"\"
    if not confirm:
        raise typer.Abort()
    from qdrant_client import QdrantClient
    client = QdrantClient(url=str(settings.qdrant_url), api_key=settings.qdrant_api_key)
    client.delete_collection(settings.qdrant_collection)
    client.close()
    import os
    if os.path.exists(settings.tracker_db):
        os.remove(settings.tracker_db)
    console.print("[green]\\u2713 Database and tracker cleared.[/green]")"""

if old_clear in content:
    content = content.replace(old_clear, new_clear)
    cli.write_text(content, encoding="utf-8")
    print("Fixed clear function indentation")
else:
    print("Could not find clear function to fix")
