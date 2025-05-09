
from docling.document_converter import DocumentConverter




source = "se.png"
converter = DocumentConverter()
result = converter.convert(source)

print(result.document.export_to_html())