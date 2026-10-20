# DOC to DOCX converter

This framework-dependent .NET 8 CLI wraps the MIT-licensed
[`DocSharp.Binary.Doc`](https://www.nuget.org/packages/DocSharp.Binary.Doc)
package. The published output is managed IL and is portable across operating
systems and CPU architectures supported by the .NET 8 runtime.

Publish the converter into the Python package before building a wheel:

```bash
dotnet publish tools/doc2docx/DocToDocx.Cli.csproj \
  -c Release \
  -f net8.0 \
  --self-contained false \
  -p:UseAppHost=false \
  -o src/jobtology_be/document_processor/core/vendor/doc2docx/net8
```

At runtime, `document-processor` invokes:

```bash
dotnet DocToDocx.Cli.dll input.doc output.docx
```

Set `DOCUMENT_PROCESSOR_DOC_CONVERTER` to use a separately published converter
DLL or executable instead of the bundled artifact.
