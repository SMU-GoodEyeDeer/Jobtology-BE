from __future__ import annotations

from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


from jobtology_be.document_processor import DocIR
from jobtology_be.document_processor.diagram import (
    create_model_diagram,
    create_package_diagram_dot,
    draw_model_diagram,
    draw_package_diagram,
    main,
    resolve_model,
)


class DiagramTests(unittest.TestCase):
    def test_resolve_model_defaults_to_docir(self) -> None:
        self.assertIs(resolve_model(), DocIR)

    def test_resolve_model_by_dotted_path(self) -> None:
        self.assertIs(resolve_model("jobtology_be.document_processor.DocIR"), DocIR)

    def test_create_model_diagram_uses_erdantic_create(self) -> None:
        fake_module = types.ModuleType("erdantic")
        calls: list[type] = []

        def fake_create(model):
            calls.append(model)
            return {"model": model}

        fake_module.create = fake_create

        with patch.dict(sys.modules, {"erdantic": fake_module}):
            diagram = create_model_diagram()

        self.assertEqual(diagram["model"], DocIR)
        self.assertEqual(calls, [DocIR])

    def test_draw_model_diagram_and_cli_use_erdantic_draw(self) -> None:
        fake_module = types.ModuleType("erdantic")
        draw_calls: list[tuple[type, str]] = []

        def fake_draw(model, *, out, graph_attr=None, node_attr=None, edge_attr=None):
            draw_calls.append((model, out))

        fake_module.draw = fake_draw
        fake_module.create = lambda model: {"model": model}

        with patch.dict(sys.modules, {"erdantic": fake_module}):
            out_path = draw_model_diagram(out="docir.svg")
            exit_code = main(["--out", "cli-docir.svg"])

        self.assertEqual(out_path, Path("docir.svg"))
        self.assertEqual(exit_code, 0)
        self.assertEqual(draw_calls[0], (DocIR, "docir.svg"))
        self.assertEqual(draw_calls[1], (DocIR, "cli-docir.svg"))

    def test_create_package_diagram_dot_includes_models_and_methods(self) -> None:
        dot = create_package_diagram_dot(include_core_modules=False)

        self.assertIn("jobtology_be.document_processor.models.DocIR", dot)
        self.assertIn("from_file", dot)
        self.assertIn("to_html", dot)
        self.assertIn("jobtology_be.document_processor.models.TableIR", dot)
        self.assertIn("@ markdown: property", dot)

    def test_draw_package_diagram_writes_dot_file_without_graphviz(self) -> None:
        with patch("pathlib.Path.write_text") as write_text:
            out_path = draw_package_diagram(out="package.dot")

        self.assertEqual(out_path, Path("package.dot"))
        write_text.assert_called_once()

    def test_draw_package_diagram_renders_via_dot_and_cli(self) -> None:
        run_calls: list[list[str]] = []

        def fake_run(cmd, *, input, text, check):
            run_calls.append(cmd)
            self.assertIn("jobtology_be.document_processor.models.DocIR", input)
            self.assertTrue(text)
            self.assertTrue(check)
            return None

        with (
            patch("shutil.which", return_value="/usr/bin/dot"),
            patch("subprocess.run", side_effect=fake_run),
        ):
            out_path = draw_package_diagram(out="package.svg")
            exit_code = main(["--kind", "package", "--out", "cli-package.svg"])

        self.assertEqual(out_path, Path("package.svg"))
        self.assertEqual(exit_code, 0)
        self.assertEqual(run_calls[0], ["/usr/bin/dot", "-Tsvg", "-o", "package.svg"])
        self.assertEqual(run_calls[1], ["/usr/bin/dot", "-Tsvg", "-o", "cli-package.svg"])


if __name__ == "__main__":
    unittest.main()
