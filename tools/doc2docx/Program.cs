using DocSharp.Binary.DocFileFormat;
using DocSharp.Binary.OpenXmlLib;
using DocSharp.Binary.OpenXmlLib.WordprocessingML;
using DocSharp.Binary.StructuredStorage.Reader;
using DocSharp.Binary.WordprocessingMLMapping;

namespace DocToDocx.Cli;

internal static class Program
{
    private static int Main(string[] args)
    {
        if (args.Length != 2)
        {
            Console.Error.WriteLine("Usage: doc2docx <input.doc> <output.docx>");
            return 64;
        }

        try
        {
            var inputPath = Path.GetFullPath(args[0]);
            var outputPath = Path.GetFullPath(args[1]);
            Directory.CreateDirectory(Path.GetDirectoryName(outputPath)!);

            using var reader = new StructuredStorageReader(inputPath);
            var document = new WordDocument(reader);
            using var output = WordprocessingDocument.Create(
                outputPath,
                WordprocessingDocumentType.Document
            );
            Converter.Convert(document, output);
            return 0;
        }
        catch (Exception exception)
        {
            Console.Error.WriteLine(exception);
            return 1;
        }
    }
}
