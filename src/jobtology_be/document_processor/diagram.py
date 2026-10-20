"""Helpers for visualizing document_processor models with Erdantic."""

from __future__ import annotations

import argparse
import ast
import importlib
import inspect
from pathlib import Path
import shutil
import subprocess
from typing import Any, get_args, get_origin

from . import models as models_module
from . import style_types as style_types_module

from .models import DocIR


def _load_erdantic():
    try:
        import erdantic as erd
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        if getattr(exc, "name", None) == "erdantic":
            raise RuntimeError(
                "Erdantic is not installed. Install the visualization extra with "
                "`pip install \"document-processor[viz]\"` and ensure Graphviz is available."
            ) from exc
        raise RuntimeError(
            "Erdantic is installed, but one of its native dependencies failed to load. "
            f"Original import error: {exc}. Ensure `pygraphviz` is built against the "
            "currently installed Graphviz libraries."
        ) from exc
    return erd


def resolve_model(model: type | str | None = None) -> type:
    """Resolve a model class from a class object or dotted import path."""
    if model is None:
        return DocIR
    if isinstance(model, type):
        return model
    if ":" in model:
        module_name, attr_name = model.split(":", 1)
    else:
        module_name, _, attr_name = model.rpartition(".")
    if not module_name or not attr_name:
        raise ValueError(
            "Model path must be a full dotted import path like "
            "`jobtology_be.document_processor.DocIR` or `pkg.module:Model`."
        )
    module = importlib.import_module(module_name)
    resolved = getattr(module, attr_name)
    if not isinstance(resolved, type):
        raise TypeError(f"Resolved object is not a class: {model}")
    return resolved


def _format_type(annotation: Any) -> str:
    if annotation is inspect._empty:
        return "Any"
    if annotation is None:
        return "None"
    if isinstance(annotation, str):
        return annotation

    origin = get_origin(annotation)
    if origin is None:
        return getattr(annotation, "__name__", repr(annotation).replace("typing.", ""))

    args = [arg for arg in get_args(annotation)]
    if origin in (list, tuple, set):
        inner = ", ".join(_format_type(arg) for arg in args) if args else "Any"
        return f"{origin.__name__}[{inner}]"
    if origin is dict:
        if len(args) == 2:
            return f"dict[{_format_type(args[0])}, {_format_type(args[1])}]"
        return "dict[Any, Any]"

    origin_name = getattr(origin, "__name__", repr(origin).replace("typing.", ""))
    if origin_name in {"UnionType", "Union"}:
        return " | ".join(_format_type(arg) for arg in args)
    return f"{origin_name}[{', '.join(_format_type(arg) for arg in args)}]"


def _short_signature(func: Any) -> str:
    signature = inspect.signature(func)
    params = list(signature.parameters.values())
    if params and params[0].name in {"self", "cls"}:
        params = params[1:]
    rendered = []
    for param in params:
        piece = param.name
        if param.kind is inspect.Parameter.VAR_POSITIONAL:
            piece = f"*{piece}"
        elif param.kind is inspect.Parameter.VAR_KEYWORD:
            piece = f"**{piece}"
        if param.annotation is not inspect._empty:
            piece += f": {_format_type(param.annotation)}"
        if param.default is not inspect._empty:
            piece += f" = {param.default!r}"
        rendered.append(piece)
    return_annotation = ""
    if signature.return_annotation is not inspect._empty:
        return_annotation = f" -> {_format_type(signature.return_annotation)}"
    return f"({', '.join(rendered)}){return_annotation}"


def _graphviz_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("|", "\\|")
        .replace("<", "\\<")
        .replace(">", "\\>")
        .replace('"', '\\"')
    )


def _record_section(title: str, lines: list[str]) -> str:
    content = "\\l".join(_graphviz_escape(line) for line in lines)
    if content:
        content += "\\l"
    return f"{_graphviz_escape(title)}|{content}"


def _class_record_label(cls: type[object]) -> str:
    fields: list[str] = []
    model_fields = getattr(cls, "model_fields", None)
    if model_fields:
        for name, field in model_fields.items():
            fields.append(f"{name}: {_format_type(field.annotation)}")

    methods: list[str] = []
    properties: list[str] = []
    for name, value in cls.__dict__.items():
        if name.startswith("_"):
            continue
        if isinstance(value, property):
            properties.append(f"@ {name}: property")
        elif isinstance(value, classmethod):
            methods.append(f"{name}{_short_signature(value.__func__)}")
        elif isinstance(value, staticmethod):
            methods.append(f"{name}{_short_signature(value.__func__)}")
        elif inspect.isfunction(value):
            methods.append(f"{name}{_short_signature(value)}")

    sections = [_graphviz_escape(cls.__name__)]
    if fields:
        sections.append(_record_section("Fields", fields))
    if properties:
        sections.append(_record_section("Properties", properties))
    if methods:
        sections.append(_record_section("Methods", methods))
    return "{" + "|".join(sections) + "}"


def _module_public_functions(module: Any) -> list[str]:
    exports = getattr(module, "__all__", None)
    names: list[str]
    if exports:
        names = [name for name in exports if not name.startswith("_")]
    else:
        names = [
            name
            for name, value in module.__dict__.items()
            if inspect.isfunction(value)
            and value.__module__ == module.__name__
            and not name.startswith("_")
        ]

    functions: list[str] = []
    for name in names:
        value = getattr(module, name, None)
        if inspect.isfunction(value):
            functions.append(f"{name}{_short_signature(value)}")
    return functions


def _module_import_edges(module_path: Path, package_root: str) -> list[str]:
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    targets: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module_name = node.module or ""
            if node.level >= 1:
                if module_name.endswith("models"):
                    targets.append(f"{package_root}.models")
                elif module_name.endswith("style_types"):
                    targets.append(f"{package_root}.style_types")
                elif module_name.startswith("core.") or module_name == "core":
                    targets.append(f"{package_root}.{module_name}" if module_name != "core" else f"{package_root}.core")
            else:
                if module_name.startswith(package_root):
                    targets.append(module_name)
    return sorted(set(targets))


def create_package_diagram_dot(
    *,
    include_core_modules: bool = True,
    include_style_types: bool = True,
) -> str:
    package_root = "jobtology_be.document_processor"
    base_dir = Path(__file__).resolve().parent
    lines = [
        "digraph document_processor {",
        '  graph [rankdir=LR, fontsize=10, fontname="Helvetica"];',
        '  node [shape=record, fontsize=10, fontname="Helvetica"];',
        '  edge [fontsize=9, fontname="Helvetica"];',
    ]

    model_classes = [
        models_module.RunIR,
        models_module.ImageAsset,
        models_module.ImageIR,
        models_module.ParagraphIR,
        models_module.TableCellIR,
        models_module.TableIR,
        models_module.DocIR,
    ]
    if include_style_types:
        model_classes.extend(
            [
                style_types_module.RunStyleInfo,
                style_types_module.ParaStyleInfo,
                style_types_module.CellStyleInfo,
                style_types_module.TableStyleInfo,
                style_types_module.StyleMap,
            ]
        )

    for cls in model_classes:
        node_id = f"{cls.__module__}.{cls.__name__}"
        lines.append(f'  "{node_id}" [label="{_class_record_label(cls)}"];')

    model_edges = [
        ("jobtology_be.document_processor.models.DocIR", "jobtology_be.document_processor.models.ParagraphIR"),
        ("jobtology_be.document_processor.models.DocIR", "jobtology_be.document_processor.models.ImageAsset"),
        ("jobtology_be.document_processor.models.ParagraphIR", "jobtology_be.document_processor.models.RunIR"),
        ("jobtology_be.document_processor.models.ParagraphIR", "jobtology_be.document_processor.models.ImageIR"),
        ("jobtology_be.document_processor.models.ParagraphIR", "jobtology_be.document_processor.models.TableIR"),
        ("jobtology_be.document_processor.models.TableIR", "jobtology_be.document_processor.models.TableCellIR"),
        ("jobtology_be.document_processor.models.TableCellIR", "jobtology_be.document_processor.models.ParagraphIR"),
        ("jobtology_be.document_processor.models.RunIR", "jobtology_be.document_processor.style_types.RunStyleInfo"),
        ("jobtology_be.document_processor.models.ParagraphIR", "jobtology_be.document_processor.style_types.ParaStyleInfo"),
        ("jobtology_be.document_processor.models.TableCellIR", "jobtology_be.document_processor.style_types.CellStyleInfo"),
        ("jobtology_be.document_processor.models.TableIR", "jobtology_be.document_processor.style_types.TableStyleInfo"),
        ("jobtology_be.document_processor.style_types.StyleMap", "jobtology_be.document_processor.style_types.RunStyleInfo"),
        ("jobtology_be.document_processor.style_types.StyleMap", "jobtology_be.document_processor.style_types.ParaStyleInfo"),
        ("jobtology_be.document_processor.style_types.StyleMap", "jobtology_be.document_processor.style_types.CellStyleInfo"),
        ("jobtology_be.document_processor.style_types.StyleMap", "jobtology_be.document_processor.style_types.TableStyleInfo"),
    ]
    for left, right in model_edges:
        if not include_style_types and ".style_types." in f".{left}.{right}":
            continue
        lines.append(f'  "{left}" -> "{right}";')

    if include_core_modules:
        core_dir = base_dir / "core"
        for module_path in sorted(core_dir.glob("*.py")):
            if module_path.name == "__init__.py":
                continue
            module_name = f"{package_root}.core.{module_path.stem}"
            module = importlib.import_module(module_name)
            functions = _module_public_functions(module)
            label_sections = [
                _graphviz_escape(module_path.stem),
                _record_section("Functions", functions or ["(no public functions)"]),
            ]
            lines.append(f'  "{module_name}" [label="{{{"|".join(label_sections)}}}", shape=record, style="rounded"];')

            for target in _module_import_edges(module_path, package_root):
                if target == f"{package_root}.models":
                    lines.append(f'  "{module_name}" -> "jobtology_be.document_processor.models.DocIR" [style=dashed, label="uses models"];')
                elif target == f"{package_root}.style_types":
                    lines.append(f'  "{module_name}" -> "jobtology_be.document_processor.style_types.StyleMap" [style=dashed, label="uses styles"];')
                elif target.startswith(f"{package_root}.core."):
                    lines.append(f'  "{module_name}" -> "{target}" [style=dotted];')

    lines.append("}")
    return "\n".join(lines)


def draw_package_diagram(
    *,
    out: str | Path,
    include_core_modules: bool = True,
    include_style_types: bool = True,
) -> Path:
    output_path = Path(out)
    dot_source = create_package_diagram_dot(
        include_core_modules=include_core_modules,
        include_style_types=include_style_types,
    )

    suffix = output_path.suffix.lower()
    if suffix in {".dot", ".gv"}:
        output_path.write_text(dot_source, encoding="utf-8")
        return output_path

    dot_bin = shutil.which("dot")
    if dot_bin is None:
        raise RuntimeError(
            "Graphviz `dot` is not available. Install Graphviz or write to a `.dot` file instead."
        )

    fmt = suffix.lstrip(".")
    if not fmt:
        raise ValueError("Output path must include an extension such as `.svg`, `.png`, or `.dot`.")

    subprocess.run(
        [dot_bin, f"-T{fmt}", "-o", str(output_path)],
        input=dot_source,
        text=True,
        check=True,
    )
    return output_path


def create_model_diagram(model: type | str | None = None):
    """Create an Erdantic diagram object for a model tree."""
    erd = _load_erdantic()
    return erd.create(resolve_model(model))


def draw_model_diagram(
    *,
    out: str | Path,
    model: type | str | None = None,
    graph_attr: dict[str, Any] | None = None,
    node_attr: dict[str, Any] | None = None,
    edge_attr: dict[str, Any] | None = None,
) -> Path:
    """Render an Erdantic diagram for a model tree."""
    erd = _load_erdantic()
    output_path = Path(out)
    erd.draw(
        resolve_model(model),
        out=str(output_path),
        graph_attr=graph_attr,
        node_attr=node_attr,
        edge_attr=edge_attr,
    )
    return output_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render diagrams for document_processor models or package structure.",
    )
    parser.add_argument(
        "--kind",
        choices=("model", "package"),
        default="model",
        help="Diagram kind. `model` uses Erdantic. `package` renders IR classes and core modules.",
    )
    parser.add_argument(
        "--model",
        default="jobtology_be.document_processor.DocIR",
        help="Dotted import path or module:class reference. Default: jobtology_be.document_processor.DocIR",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Output image path. Format is inferred from the file extension.",
    )
    parser.add_argument(
        "--no-core-modules",
        action="store_true",
        help="When using `--kind package`, omit `jobtology_be.document_processor.core.*` modules.",
    )
    parser.add_argument(
        "--no-style-types",
        action="store_true",
        help="When using `--kind package`, omit style model nodes.",
    )
    args = parser.parse_args(argv)

    if args.kind == "package":
        draw_package_diagram(
            out=args.out,
            include_core_modules=not args.no_core_modules,
            include_style_types=not args.no_style_types,
        )
    else:
        draw_model_diagram(out=args.out, model=args.model)
    return 0


__all__ = [
    "create_package_diagram_dot",
    "create_model_diagram",
    "draw_model_diagram",
    "draw_package_diagram",
    "main",
    "resolve_model",
]
