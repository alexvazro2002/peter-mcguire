"""Unified command-line access to the same library used by the desktop UI."""
import argparse
from app.config import load_config
from library.indexing import build_index, source_files
from library.paths import resolve_source
from library.retrieval import Librarian
from library.server import ModelServer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["chat", "build", "scan"])
    parser.add_argument("--source", default="")
    args = parser.parse_args()
    settings = load_config()["library"]
    paths = resolve_source(args.source or settings["default_source"])
    if args.command == "scan":
        files = source_files(paths.text)
        print(f"Source: {paths.source}\nIndex: {paths.index}\nIndex ready: {paths.indexed}")
        for path in files:
            print(path.relative_to(paths.text))
        return 0
    if args.command == "build" or not paths.indexed:
        build_index(paths, settings)
    if args.command == "build":
        return 0
    server = ModelServer(settings)
    try:
        librarian = Librarian(paths, settings)
        server.ensure()
        print(f"Ready: {librarian.documents} documents. Type EXIT to quit.")
        while True:
            try:
                question = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if question.upper() == "EXIT":
                break
            if question:
                try:
                    answer, sources = librarian.ask(question)
                    print(answer)
                    for i, source in enumerate(sources, 1):
                        print(f"[{i}] {source['source']} — chunk {source['chunk']}")
                except Exception as error:
                    print(f"ERROR: {error}")
    finally:
        server.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
