"""Command line entry point: interactive browser and --dump mode."""

from __future__ import annotations

import argparse
import shutil
import sys

from . import __version__
from .app import App, detect_truecolor
from .imaging import ImageLoader, terminal_supports_unicode
from .net import BrowserSession, FetchError
from .pagebuild import PageFactory
from .storage import Bookmarks, Config, History


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chroma",
        description="ChromaBrowser — a colorful web browser for the terminal.",
    )
    parser.add_argument("url", nargs="?", help="URL, local file or search query")
    parser.add_argument("--dump", action="store_true", help="render the page to stdout and exit")
    parser.add_argument("--width", type=int, default=None, help="page width for --dump (default: terminal or 100)")
    parser.add_argument(
        "--color",
        choices=["auto", "truecolor", "256", "never"],
        default=None,
        help="color mode (auto by default)",
    )
    parser.add_argument("--no-images", action="store_true", help="disable inline images")
    parser.add_argument("--home", metavar="URL", default=None, help="override the start page")
    parser.add_argument("--version", action="version", version=f"ChromaBrowser {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    config = Config.load()
    if args.home:
        config.homepage = args.home
    if args.no_images:
        config.images = False
    if args.color:
        config.color = args.color

    if args.dump:
        return dump_page(args, config)

    try:
        app = App(config)
        app.run(args.url)
    except KeyboardInterrupt:
        return 130
    except RuntimeError as exc:
        print(f"chroma: {exc}", file=sys.stderr)
        return 1
    return 0


def dump_page(args: argparse.Namespace, config: Config) -> int:
    if args.width:
        width = max(20, args.width)
    else:
        try:
            width = shutil.get_terminal_size().columns
        except OSError:
            width = 100
        width = max(20, min(width, 120))

    session = BrowserSession(config)
    loader = ImageLoader(
        session,
        max_width=config.max_image_width,
        unicode=terminal_supports_unicode(),
    )
    factory = PageFactory(
        config,
        session,
        loader,
        history=History(),
        bookmarks=Bookmarks(),
    )
    target = session.resolve(args.url or config.homepage)
    try:
        page = factory.fetch(target, width, images=config.images)
    except FetchError as exc:
        print(f"chroma: {exc.message}", file=sys.stderr)
        return 2
    except Exception as exc:  # pragma: no cover - defensive
        print(f"chroma: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    finally:
        session.close()

    rendered = page.view
    if rendered is None:
        print("chroma: page could not be rendered", file=sys.stderr)
        return 2

    # Explicit --color truecolor/256 forces ANSI even when output is piped;
    # "auto" only emits color on a terminal.
    use_ansi = config.color != "never" and (
        sys.stdout.isatty() or config.color in ("truecolor", "256")
    )
    if not use_ansi:
        previous_image = False
        output: list[str] = []
        for line in rendered.lines:
            if line.image is not None:
                if not previous_image:
                    output.append("[image]")
                previous_image = True
                continue
            previous_image = False
            output.append(line.text.rstrip())
        while output and not output[0]:
            output.pop(0)
        while output and not output[-1]:
            output.pop()
        print("\n".join(output))
    else:
        from .canvas import Canvas

        truecolor = detect_truecolor(config.color)
        canvas = Canvas(width, len(rendered.lines))
        for y, line in enumerate(rendered.lines):
            if line.image is not None:
                canvas.put_cells(0, y, line.image, max_width=width)
            else:
                x = 0
                for run in line.runs:
                    x = canvas.put(x, y, run.text, run.style)
        sys.stdout.write(canvas.render(None, truecolor))
        sys.stdout.write("\x1b[0m\n")

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
